"""Periodic signal scan: indicator facts + portfolio facts → rule evaluation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ai.rules.engine import build_facts_from_snapshot, evaluate_rules
from brokers.alpaca import StockBar
from brokers.factory import get_broker
from config.settings import broker_is_configured, get_watchlist, primary_broker
from db.ai_tables import list_rules
from db.sqlite import get_latest_snapshot
from signals.features import build_indicator_facts
from signals.indicators import Bar

logger = logging.getLogger(__name__)

BAR_SYMBOL_BATCH = 20
BAR_DAYS = 300
BAR_TIMEFRAME = "1Day"


def _to_bar(stockbar: StockBar) -> Bar:
    return {
        "o": stockbar.open,
        "h": stockbar.high,
        "l": stockbar.low,
        "c": stockbar.close,
        "v": stockbar.volume,
    }


def build_scan_facts(
    snapshot: dict[str, Any] | None,
    symbol_bars: dict[str, list[Bar]],
) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    if snapshot:
        facts.update(build_facts_from_snapshot(snapshot))
    facts.update(build_indicator_facts(symbol_bars))
    return facts


def _has_enabled_rules() -> bool:
    return any(rule.get("enabled") for rule in list_rules())


def _scan_symbols(broker_name: str) -> list[str]:
    symbols: set[str] = {str(s).upper() for s in get_watchlist() if s}
    snapshot = get_latest_snapshot(broker_name)
    if snapshot:
        for pos in snapshot.get("positions") or []:
            sym = str(pos.get("symbol", "")).upper()
            if sym:
                symbols.add(sym)
    return sorted(symbols)


async def _fetch_stock_bars(
    broker: Any,
    symbols: list[str],
    *,
    days: int = BAR_DAYS,
) -> dict[str, list[StockBar]]:
    fetch = getattr(broker, "aget_stock_bars_batch", None)
    if not callable(fetch):
        logger.warning("Broker does not support batch bar fetch — skipping bar fetch")
        return {}

    symbol_bars: dict[str, list[StockBar]] = {}
    for start in range(0, len(symbols), BAR_SYMBOL_BATCH):
        chunk = symbols[start : start + BAR_SYMBOL_BATCH]
        try:
            raw = await fetch(
                chunk,
                timeframe=BAR_TIMEFRAME,
                days=days,
            )
        except Exception:
            logger.exception("Bar fetch failed for symbols %s", chunk)
            continue
        for sym, bars in (raw or {}).items():
            symbol_bars[sym] = list(bars or [])
    return symbol_bars


async def _fetch_symbol_bars(
    broker: Any,
    symbols: list[str],
    *,
    days: int = BAR_DAYS,
) -> dict[str, list[Bar]]:
    symbol_bars: dict[str, list[Bar]] = {}
    raw = await _fetch_stock_bars(broker, symbols, days=days)
    for sym, bars in raw.items():
        try:
            symbol_bars[sym] = [_to_bar(bar) for bar in bars]
        except Exception:
            logger.exception("Failed to convert bars for %s", sym)
    return symbol_bars


async def run_signal_scan() -> list[dict[str, Any]]:
    if not _has_enabled_rules():
        return []

    broker_name = primary_broker()
    symbols = _scan_symbols(broker_name)
    if not symbols:
        return []

    if not broker_is_configured(broker_name):
        logger.debug("Signal scan skipped — %s not configured", broker_name)
        return []

    try:
        broker = get_broker(broker_name)
    except Exception:
        logger.exception("Signal scan failed to get broker %s", broker_name)
        return []

    symbol_bars = await _fetch_symbol_bars(broker, symbols)
    snapshot = await asyncio.to_thread(get_latest_snapshot, broker_name)
    facts = build_scan_facts(snapshot, symbol_bars)

    try:
        return evaluate_rules(facts, event_type="scan")
    except Exception:
        logger.exception("Rule evaluation failed during signal scan")
        return []
