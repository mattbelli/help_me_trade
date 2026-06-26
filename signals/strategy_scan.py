"""Live strategy scan — registry-driven entry proposals via propose_trade.

Entry-only increment: validates live entry signal → proposal → paper fill.
Exit/position management (stop, sell-half, breakeven, trail, cap) is a follow-on.

Fidelity caveat: the backtest enters at the signal day's close; live proposals fire
during market hours and fill at market ≈ now — close-vs-live slippage is expected.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from brokers.alpaca import StockBar
from brokers.factory import connection_is_configured, get_broker_for_account
from config.settings import broker_is_configured
from db.accounts import get_account, get_connection
from db.base import DEFAULT_DB_PATH
from db.paper_orders import list_pending_proposals
from db.sqlite import get_latest_snapshot
from db.strategies import list_strategies
from signals.scan import _fetch_stock_bars
from signals.strategy_kinds import (
    EntryContext,
    filter_entry_params,
    kind_spec,
)
from signals.strategy_universe import resolve_universe_spec
from signals.trade_context import MIN_BARS_FOR_CONTEXT, DatedBar
from trading.proposal_intake import TradeCandidate, propose_trade

logger = logging.getLogger(__name__)

STRATEGY_BAR_DAYS = 420
SPY_SYMBOL = "SPY"

def _parse_bar_date(time_str: str) -> date:
    text = time_str.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return date.fromisoformat(text[:10])


def _to_dated_bar(stockbar: StockBar) -> DatedBar:
    return {
        "d": _parse_bar_date(stockbar.time).isoformat(),
        "o": stockbar.open,
        "h": stockbar.high,
        "l": stockbar.low,
        "c": stockbar.close,
        "v": stockbar.volume,
    }


def _stock_bars_to_dated(bars: list[StockBar]) -> list[DatedBar]:
    dated = [_to_dated_bar(bar) for bar in bars]
    dated.sort(key=lambda bar: bar["d"])
    return dated


def _held_symbols(snapshot: dict[str, Any] | None) -> set[str]:
    """Symbols with an open position in the snapshot — one-entry-per-signal guard."""
    if not snapshot:
        return set()
    held: set[str] = set()
    for pos in snapshot.get("positions") or []:
        sym = str(pos.get("symbol", "")).upper()
        if sym:
            held.add(sym)
    return held


def _held_symbols_for_account(
    account_id: str,
    broker_name: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> set[str]:
    snapshot = get_latest_snapshot(broker_name, account_id=account_id, db_path=db_path)
    return _held_symbols(snapshot)


def _open_exposure(snapshot: dict[str, Any] | None) -> tuple[int, float]:
    """(open-position count, total |market_value|) from an account snapshot."""
    if not snapshot:
        return 0, 0.0
    positions = snapshot.get("positions") or []
    value = 0.0
    for pos in positions:
        try:
            value += abs(float(pos.get("market_value") or 0.0))
        except (TypeError, ValueError):
            continue
    return len(positions), value


def _latest_close_map(raw_bars: dict[str, list[StockBar]]) -> dict[str, float]:
    """Symbol -> most-recent close, for valuing pending proposals at scan time.

    Best-effort and per-symbol resilient: a malformed bar set is skipped (its
    pending proposals fall back to the strategy notional) so one bad symbol can't
    break the budget computation for the whole scan.
    """
    prices: dict[str, float] = {}
    for sym, bars in raw_bars.items():
        if not bars:
            continue
        try:
            dated = _stock_bars_to_dated(bars)
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        if dated:
            prices[sym.upper()] = float(dated[-1]["c"])
    return prices


def _pending_exposure(
    account_id: str,
    *,
    prices: dict[str, float],
    fallback_notional: float,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[int, float]:
    """(pending-proposal count, est. $ committed) for an account's un-approved proposals.

    Pending orders aren't filled yet but WILL consume capital once approved, so they
    must count against a strategy's budget — otherwise one scan could propose past the
    cap and a batch-approve would blow through it. Valued at qty x latest close; when
    the symbol's price isn't on hand, falls back to the strategy's per-trade notional.
    """
    pending = list_pending_proposals(account_id=account_id, db_path=db_path)
    value = 0.0
    for prop in pending:
        symbol = str(prop.get("symbol", "")).upper()
        try:
            qty = abs(float(prop.get("qty") or 0.0))
        except (TypeError, ValueError):
            qty = 0.0
        price = prices.get(symbol)
        if price is not None and qty > 0:
            value += qty * price
        else:
            value += fallback_notional
    return len(pending), value


def _account_is_ready(
    account: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    if not account.get("enabled") or not account.get("execution_capable"):
        return False
    broker_name = str(account["broker"]).strip().lower()
    connection_id = account.get("connection_id")
    if connection_id:
        connection = get_connection(str(connection_id), db_path=db_path)
        if connection is None or not connection_is_configured(connection):
            return False
    elif not broker_is_configured(broker_name):
        return False
    return True


async def _run_strategy(
    strategy: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    spec = kind_spec(strategy["id"])
    if spec is None:
        logger.warning(
            "Strategy %s skipped — unknown strategy kind",
            strategy["id"],
        )
        return []

    notional = float(strategy.get("notional_usd") or 0)
    if notional <= 0:
        logger.debug(
            "Strategy %s skipped — notional is %.2f",
            strategy["id"],
            notional,
        )
        return []

    account_id = strategy.get("account_id")
    if not account_id:
        logger.debug("Strategy %s skipped — no bound account", strategy["id"])
        return []

    account = get_account(str(account_id), db_path=db_path)
    if account is None or not _account_is_ready(account, db_path=db_path):
        logger.debug(
            "Strategy %s skipped — account %s missing or not execution-ready",
            strategy["id"],
            account_id,
        )
        return []

    broker_name = str(account["broker"]).strip().lower()
    # The strategy's OWN universe (decoupled from the cockpit's active watchlist).
    symbols = resolve_universe_spec(strategy.get("universe"))
    fetch_symbols = sorted(set(symbols) | {SPY_SYMBOL})
    if not symbols:
        logger.debug(
            "Strategy %s skipped — empty universe %r",
            strategy["id"],
            strategy.get("universe"),
        )
        return []

    try:
        broker = get_broker_for_account(str(account_id))
    except Exception:
        logger.exception(
            "Strategy %s failed to get broker for account %s",
            strategy["id"],
            account_id,
        )
        return []

    raw_bars = await _fetch_stock_bars(
        broker,
        fetch_symbols,
        days=STRATEGY_BAR_DAYS,
    )

    spy_raw = raw_bars.get(SPY_SYMBOL, [])
    if len(spy_raw) < MIN_BARS_FOR_CONTEXT:
        logger.debug(
            "Strategy %s skipped — insufficient SPY bars (%d)",
            strategy["id"],
            len(spy_raw),
        )
        return []

    spy_bars = _stock_bars_to_dated(spy_raw)
    spy_idx = len(spy_bars) - 1

    snapshot = get_latest_snapshot(
        broker_name, account_id=str(account_id), db_path=db_path
    )
    held = _held_symbols(snapshot)
    entry_params = filter_entry_params(spec, strategy.get("params") or {})

    # Per-strategy risk caps (None = uncapped). Committed exposure = already-open
    # positions + still-pending proposals in the bound account; we stop proposing
    # once a cap would be exceeded. Counting is account-scoped against this
    # strategy's own limit — exact for the recommended one-strategy-per-account
    # setup, conservative when an account is shared. This is a best-effort,
    # propose-time advisory cap that assumes serialized scans (the scheduler runs
    # one at a time); the HARD guarantee remains RiskEngine.validate() + buying
    # power at the chokepoint. Only compute exposure when a cap is actually set,
    # so the uncapped path stays read-for-read identical to before.
    max_concurrent = strategy.get("max_concurrent_positions")
    max_deployed = strategy.get("max_deployed_usd")
    committed_count = 0
    committed_usd = 0.0
    if max_concurrent is not None or max_deployed is not None:
        open_count, open_value = _open_exposure(snapshot)
        pending_count, pending_value = _pending_exposure(
            str(account_id),
            prices=_latest_close_map(raw_bars),
            fallback_notional=notional,
            db_path=db_path,
        )
        committed_count = open_count + pending_count
        committed_usd = open_value + pending_value

    results: list[dict[str, Any]] = []
    evaluated = 0
    passed = 0

    for sym in symbols:
        if sym == SPY_SYMBOL:
            continue
        if sym in held:
            logger.debug(
                "Strategy %s skip %s — already held (one entry per signal)",
                strategy["id"],
                sym,
            )
            continue
        evaluated += 1
        try:
            sym_raw = raw_bars.get(sym, [])
            if len(sym_raw) < MIN_BARS_FOR_CONTEXT:
                logger.debug(
                    "Strategy %s skip %s — insufficient bars (%d < %d)",
                    strategy["id"],
                    sym,
                    len(sym_raw),
                    MIN_BARS_FOR_CONTEXT,
                )
                continue

            sym_bars = _stock_bars_to_dated(sym_raw)
            idx = len(sym_bars) - 1

            if sym_bars[idx]["d"] != spy_bars[spy_idx]["d"]:
                logger.debug(
                    "Strategy %s skip %s — bar date %s != SPY %s",
                    strategy["id"],
                    sym,
                    sym_bars[idx]["d"],
                    spy_bars[spy_idx]["d"],
                )
                continue

            ctx = EntryContext(
                sym=sym,
                sym_bars=sym_bars,
                idx=idx,
                spy_bars=spy_bars,
                spy_idx=spy_idx,
                params=entry_params,
                db_path=db_path,
            )
            if not spec.check_entry(ctx):
                continue

            # Per-strategy risk caps: stop once the next order would breach the
            # concurrent-position or deployed-capital budget. Same notional every
            # time, so once a cap is hit no later candidate can fit → break.
            if max_concurrent is not None and committed_count >= max_concurrent:
                logger.info(
                    "Strategy %s cap hit — %d/%d concurrent (open+pending); "
                    "stop proposing",
                    strategy["id"],
                    committed_count,
                    max_concurrent,
                )
                break
            if max_deployed is not None and committed_usd + notional > max_deployed:
                logger.info(
                    "Strategy %s budget hit — $%.0f committed + $%.0f next > $%.0f; "
                    "stop proposing",
                    strategy["id"],
                    committed_usd,
                    notional,
                    max_deployed,
                )
                break

            passed += 1
            candidate = TradeCandidate(
                symbol=sym,
                side="buy",
                notional_usd=Decimal(str(notional)),
                order_type="market",
                source=f"strategy:{strategy['id']}",
                rule_id=None,
                rationale=spec.build_rationale(ctx, strategy["name"]),
                account_id=str(account_id),
            )
            result = await propose_trade(candidate, db_path=db_path)
            results.append(result)
            # Only a freshly-created proposal adds to committed exposure (duplicates
            # were already in pending_count; blocks/errors commit nothing).
            if result.get("status") == "proposed":
                committed_count += 1
                committed_usd += notional
        except Exception:
            logger.exception(
                "Strategy %s failed for %s",
                strategy["id"],
                sym,
            )
            continue

    logger.info(
        "Strategy %s scan: evaluated=%d passed=%d statuses=%s",
        strategy["id"],
        evaluated,
        passed,
        [r.get("status") for r in results],
    )
    return results


async def run_strategy_scan(
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for strategy in list_strategies(enabled_only=True, db_path=db_path):
        try:
            strategy_results = await _run_strategy(strategy, db_path=db_path)
            results.extend(strategy_results)
        except Exception:
            logger.exception("Strategy scan failed for %s", strategy.get("id"))
            continue
    return results
