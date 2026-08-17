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
from config.settings import broker_is_configured, trading_config
from db.accounts import get_account, get_connection
from db.base import DEFAULT_DB_PATH
from db.managed_positions import list_open_positions
from db.order_executions import list_recent_auto_entries
from db.paper_orders import list_pending_proposals
from db.sqlite import get_latest_snapshot
from db.strategies import list_strategies
from signals.scan import _fetch_stock_bars
from signals.strategy_kinds import (
    REGIME_SLEEVE_KINDS,
    EntryContext,
    filter_entry_params,
    is_portfolio_kind,
    kind_spec,
    strategy_kind,
)
from signals.strategy_universe import resolve_universe_spec
from signals.trade_context import MIN_BARS_FOR_CONTEXT, DatedBar
from signals.volatility import DEFAULT_PROFILE, forecast_vol, size_multiplier
from trading.proposal_intake import (
    _AUTO_ENTRY_DEDUP_SECONDS,
    TradeCandidate,
    propose_trade,
)

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


def _resolve_notional(
    strategy: dict[str, Any],
    snapshot: dict[str, Any] | None,
) -> float | None:
    """Per-trade notional — percent of equity when set, else the static dollar amount.

    Returns None when the strategy must be skipped (caller returns no candidates).

    FAIL-CLOSED on the percent path: if notional_pct is set but the snapshot carries
    no positive portfolio_value, we skip rather than fall back to notional_usd —
    sizing a real order off absent/stale equity is exactly the wrong-sized-order
    failure, and silently sizing by a different rule than the user configured is
    worse than not trading this tick.
    """
    pct = strategy.get("notional_pct")
    if pct is not None and float(pct) > 0:
        account = (snapshot or {}).get("account") or {}
        try:
            equity = float(account.get("portfolio_value") or 0.0)
        except (TypeError, ValueError):
            equity = 0.0
        if equity <= 0:
            logger.warning(
                "Strategy %s skipped — sized at %.2f%% of equity but the account "
                "snapshot has no usable portfolio_value (fail-closed: NOT falling "
                "back to notional_usd)",
                strategy["id"],
                float(pct),
            )
            return None
        notional = float(pct) / 100.0 * equity
    else:
        notional = float(strategy.get("notional_usd") or 0)

    if notional <= 0:
        logger.debug(
            "Strategy %s skipped — notional is %.2f",
            strategy["id"],
            notional,
        )
        return None
    return notional


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
    # Order-change rows (a book moving a stop / pulling an entry) commit no
    # new capital — leave them out (trading/book_orders.py).
    pending = [
        p
        for p in list_pending_proposals(account_id=account_id, db_path=db_path)
        if not p.get("order_action")
    ]
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


def _inflight_auto_exposure(
    account_id: str,
    strategy_id: str,
    *,
    held: set[str],
    prices: dict[str, float],
    fallback_notional: float,
    within_seconds: float,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[int, float]:
    """(count, est. $) of this strategy's in-flight auto-entry orders not yet held.

    Auto-entries land in order_executions, invisible to `_pending_exposure`; this lets the
    concurrency/deployed caps see them so a capped strategy can't exceed its cap across scan
    ticks on different symbols. Symbols already in the snapshot's `held` set are skipped —
    `_open_exposure` already counts those filled+reconciled positions (no double counting).
    """
    rows = list_recent_auto_entries(
        account_id, strategy_id, within_seconds=within_seconds, db_path=db_path
    )
    count = 0
    value = 0.0
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        if symbol in held:
            continue
        count += 1
        try:
            qty = abs(float(row.get("qty") or 0.0))
        except (TypeError, ValueError):
            qty = 0.0
        price = prices.get(symbol)
        if price is not None and qty > 0:
            value += qty * price
        else:
            value += fallback_notional
    return count, value


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


async def _run_portfolio_strategy(
    strategy: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Rank → diff vs holdings; execute when paper-fenced auto, else read-only plan log."""
    exec_mode = trading_config()["execution_mode"]
    armed = (strategy.get("entry_mode") == "auto") and exec_mode in (
        "dry_run",
        "paper",
    )
    if armed:
        from signals.momentum import execute_rebalance

        result = await execute_rebalance(strategy, db_path=db_path)
        return [result]

    from signals.momentum import production_target_set

    sid = strategy["id"]
    target_rows = production_target_set(db_path=db_path)
    if not target_rows:
        logger.warning(
            "Momentum rebalance skipped for %s — empty target set (no bars yet?)",
            sid,
        )
        return []

    target = {str(d["symbol"]).upper() for d in target_rows}
    held = {
        str(row["symbol"]).upper()
        for row in list_open_positions(db_path=db_path)
        if row["strategy_id"] == sid
    }
    sells = held - target
    buys = target - held
    holds = held & target

    logger.info(
        "Momentum rebalance PLAN (read-only) for %s: target=%d held=%d → "
        "buy %d, sell %d, hold %d | buys=%s sells=%s",
        sid,
        len(target),
        len(held),
        len(buys),
        len(sells),
        len(holds),
        sorted(buys)[:10],
        sorted(sells)[:10],
    )
    return [
        {
            "status": "planned",
            "strategy_id": sid,
            "n_target": len(target),
            "n_buy": len(buys),
            "n_sell": len(sells),
            "n_hold": len(holds),
        }
    ]


async def _run_strategy(
    strategy: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    if is_portfolio_kind(strategy["id"]):
        return await _run_portfolio_strategy(strategy, db_path=db_path)

    # Regime-local sleeves are scanned by their own post-close loop
    # (signals.regime_sleeves) — silently skip them here. LLM books ("book")
    # are driven by their own nightly pipeline (research/books.py) — same deal.
    if strategy_kind(strategy["id"]) in REGIME_SLEEVE_KINDS or (
        strategy_kind(strategy["id"]) == "book"
    ):
        return []

    spec = kind_spec(strategy["id"])
    if spec is None:
        logger.warning(
            "Strategy %s skipped — unknown strategy kind",
            strategy["id"],
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
    tcfg = trading_config()
    exec_mode = tcfg["execution_mode"]
    # Vol-targeted sizing is two deliberate switches, and the master one is
    # shadow-safe by design: with it ON but the strategy's vol_target OFF we
    # compute and log the read without touching a single notional. Only both
    # ON changes an order's size.
    # .get() and not [] on purpose: a config read for a sizing toggle must
    # fail-soft to OFF. A partial/older settings dict leaves sizing untouched
    # rather than killing the scan.
    vol_enabled = bool(tcfg.get("volatility_enabled", False))
    vol_profile = tcfg.get("vol_target_profile", DEFAULT_PROFILE)
    vol_applies = vol_enabled and bool(strategy.get("vol_target"))
    auto_entry = (
        (strategy.get("entry_mode") or "propose") == "auto"
        and exec_mode in ("dry_run", "paper")
        and strategy["id"] != "session_node_spy"
    )

    # Snapshot first: it's a local DB read (broker + account only), and percent-of-
    # equity sizing needs its portfolio_value. Resolving notional here keeps the
    # unsized/unsizable early-out ahead of the broker bar fetch, as before.
    snapshot = get_latest_snapshot(
        broker_name, account_id=str(account_id), db_path=db_path
    )
    held = _held_symbols(snapshot)
    notional = _resolve_notional(strategy, snapshot)
    if notional is None:
        return []

    # Percent-of-equity sizing deploys an exact dollar fraction, so it fills fractionally
    # (whole-share flooring would strand cash and drop names above the per-name budget).
    # Static-dollar strategies keep the whole-share default. Mirrors _resolve_notional's
    # percent branch.
    _pct = strategy.get("notional_pct")
    allow_fractional = _pct is not None and float(_pct or 0) > 0

    # A strategy may target ONE explicit symbol (params["symbol"]) instead of a universe —
    # for regime-driven sleeves whose predicate picks no symbol of its own. Explicit
    # targeting wins over `universe`. Only honoured for kinds that REGISTER "symbol":
    # otherwise a stray param would silently narrow a universe-scanning strategy to one
    # name while its predicate never even sees the key.
    target_symbol = None
    if "symbol" in spec.entry_param_keys:
        target_symbol = (
            str((strategy.get("params") or {}).get("symbol") or "").strip().upper() or None
        )
    # The strategy's OWN universe (decoupled from the cockpit's active watchlist).
    symbols = [target_symbol] if target_symbol else resolve_universe_spec(strategy.get("universe"))
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
        prices = _latest_close_map(raw_bars)
        open_count, open_value = _open_exposure(snapshot)
        pending_count, pending_value = _pending_exposure(
            str(account_id),
            prices=prices,
            fallback_notional=notional,
            db_path=db_path,
        )
        # In-flight auto-entries live in order_executions (not pending phone proposals), so
        # without this the concurrency/deployed caps are blind to them and a capped strategy
        # could exceed the cap across scan ticks on different symbols. Held symbols are
        # excluded — _open_exposure already counts filled+reconciled positions.
        inflight_count, inflight_value = _inflight_auto_exposure(
            str(account_id),
            strategy["id"],
            held=held,
            prices=prices,
            fallback_notional=notional,
            within_seconds=_AUTO_ENTRY_DEDUP_SECONDS,
            db_path=db_path,
        )
        committed_count = open_count + pending_count + inflight_count
        committed_usd = open_value + pending_value + inflight_value

    results: list[dict[str, Any]] = []
    evaluated = 0
    passed = 0

    for sym in symbols:
        # SPY is fetched as the market benchmark for every strategy, so skip it unless
        # this strategy explicitly targets it — otherwise it'd be traded just for being fetched.
        if sym == SPY_SYMBOL and sym != target_symbol:
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

            sym_notional = Decimal(str(notional))
            if vol_enabled:
                # Only bars the strategy itself already evaluated, and
                # forecast_vol drops the still-forming bar dated today.
                forecast = forecast_vol(sym, sym_bars[: idx + 1])
                multiplier = size_multiplier(forecast, profile=vol_profile)
                if vol_applies:
                    sym_notional = sym_notional * multiplier
                logger.info(
                    "Strategy %s %s vol: regime=%s annualized=%s multiplier=%s "
                    "applied=%s notional=%.2f→%.2f",
                    strategy["id"],
                    sym,
                    forecast.regime if forecast else "unknown",
                    f"{forecast.annualized:.4f}" if forecast else "n/a",
                    multiplier,
                    vol_applies,
                    notional,
                    float(sym_notional),
                )

            # Per-strategy risk caps: stop once the next order would breach the
            # concurrent-position or deployed-capital budget. With vol-targeted
            # sizing the notional now VARIES per symbol, so the old "same notional
            # every time" reasoning no longer holds — a later, calmer name could
            # still fit under the budget after this one doesn't. We keep the break
            # anyway: stopping the strategy's cycle early under-trades, while
            # continuing would let a cap be approached repeatedly by ever-smaller
            # orders. Under-deploying is the safe side of this cap.
            if max_concurrent is not None and committed_count >= max_concurrent:
                logger.info(
                    "Strategy %s cap hit — %d/%d concurrent (open+pending); "
                    "stop proposing",
                    strategy["id"],
                    committed_count,
                    max_concurrent,
                )
                break
            if max_deployed is not None and committed_usd + float(sym_notional) > max_deployed:
                logger.info(
                    "Strategy %s budget hit — $%.0f committed + $%.0f next > $%.0f; "
                    "stop proposing",
                    strategy["id"],
                    committed_usd,
                    float(sym_notional),
                    max_deployed,
                )
                break

            passed += 1
            candidate = TradeCandidate(
                symbol=sym,
                side="buy",
                notional_usd=sym_notional,
                order_type="market",
                source=f"strategy:{strategy['id']}",
                rule_id=strategy["id"],
                rationale=spec.build_rationale(ctx, strategy["name"]),
                account_id=str(account_id),
                allow_fractional=allow_fractional,
            )
            if auto_entry:
                from trading.proposal_intake import auto_execute_entry

                result = await auto_execute_entry(candidate, db_path=db_path)
            else:
                result = await propose_trade(candidate, db_path=db_path)
            results.append(result)
            if result.get("status") in ("proposed", "executed"):
                committed_count += 1
                committed_usd += float(sym_notional)
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
