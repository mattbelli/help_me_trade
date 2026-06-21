"""Live dip-buy strategy scan — forward-test entry proposals via propose_trade.

Entry-only increment: validates live entry signal → proposal → paper fill.
Exit/position management (stop, sell-half, breakeven, trail, cap) is a follow-on.

Fidelity caveat: the backtest enters at the signal day's close; live proposals fire
during market hours and fill at market ≈ now — close-vs-live slippage is expected.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from brokers.alpaca import StockBar
from brokers.factory import get_broker
from config.settings import broker_is_configured, primary_broker, trading_config
from db.sqlite import get_latest_snapshot
from research.strategy_features import compute_market_context, is_strategy_entry
from signals.indicators import closes, pct_change
from signals.scan import _fetch_stock_bars, _scan_symbols
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


def _held_symbols(broker_name: str) -> set[str]:
    """Symbols with an open position in the latest snapshot — one-entry-per-signal guard.

    The dip predicate stays true for several consecutive days, so without this the scan
    would re-propose a name already held each cycle and stack size.
    """
    snapshot = get_latest_snapshot(broker_name)
    if not snapshot:
        return set()
    held: set[str] = set()
    for pos in snapshot.get("positions") or []:
        sym = str(pos.get("symbol", "")).upper()
        if sym:
            held.add(sym)
    return held


def _build_rationale(
    sym_bars: list[DatedBar],
    idx: int,
    spy_bars: list[DatedBar],
    spy_idx: int,
    sym: str,
) -> str:
    ret_3d = pct_change(closes(sym_bars[: idx + 1]), 3)
    mkt_ctx = compute_market_context(spy_bars, spy_idx)
    mkt_ret_3d = mkt_ctx.get("mkt_ret_3d") if mkt_ctx else None
    ret_str = f"{ret_3d:.1f}" if ret_3d is not None else "?"
    mkt_str = f"{mkt_ret_3d:.1f}" if mkt_ret_3d is not None else "?"
    return f"Dip-buy: {sym} {ret_str}%/3d, SPY {mkt_str}%/3d, >SMA200"


async def run_strategy_scan() -> list[dict[str, Any]]:
    cfg = trading_config()
    if not cfg.get("dip_strategy_enabled", False):
        return []

    notional = float(cfg.get("dip_strategy_notional_usd", 0) or 0)
    if notional <= 0:
        logger.warning(
            "Dip-strategy scan skipped — notional is %.2f (set dip_strategy_notional_usd > 0)",
            notional,
        )
        return []

    broker_name = primary_broker()
    symbols = _scan_symbols(broker_name)
    fetch_symbols = sorted(set(symbols) | {SPY_SYMBOL})
    if not fetch_symbols:
        return []

    if not broker_is_configured(broker_name):
        logger.debug("Strategy scan skipped — %s not configured", broker_name)
        return []

    try:
        broker = get_broker(broker_name)
    except Exception:
        logger.exception("Strategy scan failed to get broker %s", broker_name)
        return []

    raw_bars = await _fetch_stock_bars(
        broker,
        fetch_symbols,
        days=STRATEGY_BAR_DAYS,
    )

    spy_raw = raw_bars.get(SPY_SYMBOL, [])
    if len(spy_raw) < MIN_BARS_FOR_CONTEXT:
        logger.debug("Strategy scan skipped — insufficient SPY bars (%d)", len(spy_raw))
        return []

    spy_bars = _stock_bars_to_dated(spy_raw)
    spy_idx = len(spy_bars) - 1

    held = _held_symbols(broker_name)

    results: list[dict[str, Any]] = []
    evaluated = 0
    passed = 0

    for sym in symbols:
        if sym == SPY_SYMBOL:
            continue
        if sym in held:
            logger.debug("Strategy scan skip %s — already held (one entry per signal)", sym)
            continue
        evaluated += 1
        try:
            sym_raw = raw_bars.get(sym, [])
            if len(sym_raw) < MIN_BARS_FOR_CONTEXT:
                logger.debug(
                    "Strategy scan skip %s — insufficient bars (%d < %d)",
                    sym,
                    len(sym_raw),
                    MIN_BARS_FOR_CONTEXT,
                )
                continue

            sym_bars = _stock_bars_to_dated(sym_raw)
            idx = len(sym_bars) - 1

            if sym_bars[idx]["d"] != spy_bars[spy_idx]["d"]:
                logger.debug(
                    "Strategy scan skip %s — bar date %s != SPY %s",
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
                side="long",
            ):
                continue

            passed += 1
            candidate = TradeCandidate(
                symbol=sym,
                side="buy",
                notional_usd=Decimal(str(notional)),
                order_type="market",
                source="dip_strategy",
                rule_id=None,
                rationale=_build_rationale(sym_bars, idx, spy_bars, spy_idx, sym),
            )
            result = await propose_trade(candidate)
            results.append(result)
        except Exception:
            logger.exception("Strategy scan failed for %s", sym)
            continue

    logger.info(
        "Dip-strategy scan: evaluated=%d passed=%d statuses=%s",
        evaluated,
        passed,
        [r.get("status") for r in results],
    )
    return results
