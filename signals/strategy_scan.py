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
from db.sqlite import get_latest_snapshot
from db.strategies import list_strategies
from research.strategy_features import compute_market_context, is_strategy_entry
from signals.indicators import closes, pct_change
from signals.scan import _fetch_stock_bars, _scan_symbols
from signals.trade_context import MIN_BARS_FOR_CONTEXT, DatedBar
from trading.proposal_intake import TradeCandidate, propose_trade

logger = logging.getLogger(__name__)

STRATEGY_BAR_DAYS = 420
SPY_SYMBOL = "SPY"

_ENTRY_PARAM_KEYS = (
    "healthy_required",
    "oversold_3d_max",
    "mkt_down_3d_max",
    "side",
    "far_support_min",
)


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


def _held_symbols_for_account(
    account_id: str,
    broker_name: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> set[str]:
    """Symbols with an open position in the account snapshot — one-entry-per-signal guard."""
    snapshot = get_latest_snapshot(broker_name, account_id=account_id, db_path=db_path)
    if not snapshot:
        return set()
    held: set[str] = set()
    for pos in snapshot.get("positions") or []:
        sym = str(pos.get("symbol", "")).upper()
        if sym:
            held.add(sym)
    return held


def _entry_params_from(params: dict[str, Any]) -> dict[str, Any]:
    return {key: params[key] for key in _ENTRY_PARAM_KEYS if key in params}


def _build_rationale(
    sym_bars: list[DatedBar],
    idx: int,
    spy_bars: list[DatedBar],
    spy_idx: int,
    sym: str,
    strategy_name: str,
) -> str:
    ret_3d = pct_change(closes(sym_bars[: idx + 1]), 3)
    mkt_ctx = compute_market_context(spy_bars, spy_idx)
    mkt_ret_3d = mkt_ctx.get("mkt_ret_3d") if mkt_ctx else None
    ret_str = f"{ret_3d:.1f}" if ret_3d is not None else "?"
    mkt_str = f"{mkt_ret_3d:.1f}" if mkt_ret_3d is not None else "?"
    return f"{strategy_name}: {sym} {ret_str}%/3d, SPY {mkt_str}%/3d, >SMA200"


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
    symbols = _scan_symbols(broker_name)
    fetch_symbols = sorted(set(symbols) | {SPY_SYMBOL})
    if not fetch_symbols:
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
    held = _held_symbols_for_account(str(account_id), broker_name, db_path=db_path)
    entry_params = _entry_params_from(strategy.get("params") or {})

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

            if not is_strategy_entry(
                sym_bars,
                idx,
                spy_bars=spy_bars,
                spy_idx=spy_idx,
                **entry_params,
            ):
                continue

            passed += 1
            candidate = TradeCandidate(
                symbol=sym,
                side="buy",
                notional_usd=Decimal(str(notional)),
                order_type="market",
                source=f"strategy:{strategy['id']}",
                rule_id=None,
                rationale=_build_rationale(
                    sym_bars,
                    idx,
                    spy_bars,
                    spy_idx,
                    sym,
                    strategy["name"],
                ),
                account_id=str(account_id),
            )
            result = await propose_trade(candidate, db_path=db_path)
            results.append(result)
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
