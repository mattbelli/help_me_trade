"""Deterministic per-trade market context and entry-style profile from daily bars."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

from brokers.alpaca import StockBar
from imports.option_symbol import is_option_symbol
from signals.indicators import (
    Bar,
    closes,
    distance_from_high,
    pct_change,
    sma,
)

logger = logging.getLogger(__name__)

# --- documented thresholds (config constants) ---
WARMUP_BARS = 252
MIN_BARS_FOR_CONTEXT = 252
BAR_SYMBOL_BATCH = 20
BAR_TIMEFRAME = "1Day"

PULLBACK_PRIOR_RETURN_3D_MAX = -1.5  # % — multi-day decline into entry
MOMENTUM_PRIOR_RETURN_3D_MIN = 1.5  # % — multi-day rally into entry
NEAR_52W_HIGH_PCT = -5.0  # dist_from_52w_high_pct >= this → within ~5% of high

SMA50_PERIOD = 50
SMA200_PERIOD = 200
HIGH_LOOKBACK = 252

ENTRY_CONTEXT_LABELS = (
    "pullback",
    "dip_below_trend",
    "breakout",
    "momentum",
    "neutral",
)


class DatedBar(Bar):
    d: str  # ISO calendar date YYYY-MM-DD


def _parse_bar_date(time_str: str) -> date:
    text = time_str.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return date.fromisoformat(text[:10])


def _stockbar_to_dated(stockbar: StockBar) -> DatedBar:
    return {
        "d": _parse_bar_date(stockbar.time).isoformat(),
        "o": stockbar.open,
        "h": stockbar.high,
        "l": stockbar.low,
        "c": stockbar.close,
        "v": stockbar.volume,
    }


def _bar_dates(bars: list[DatedBar]) -> list[date]:
    return [date.fromisoformat(bar["d"]) for bar in bars]


def _find_bar_index(bars: list[DatedBar], trade_date: date) -> int | None:
    """Index of the last bar on or before trade_date."""
    idx: int | None = None
    for i, bar in enumerate(bars):
        bar_date = date.fromisoformat(bar["d"])
        if bar_date <= trade_date:
            idx = i
        else:
            break
    return idx


def _is_equity_trade(event: dict[str, Any]) -> bool:
    if event.get("event_type") != "trade":
        return False
    symbol = event.get("symbol")
    if not symbol or is_option_symbol(symbol):
        return False
    return event.get("side") in ("buy", "sell")


def _parse_trade_date(event: dict[str, Any]) -> date | None:
    raw = event.get("trade_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _price_vs_ma(close: float, ma: float | None) -> str | None:
    if ma is None:
        return None
    return "above" if close >= ma else "below"


def _gap_prior_pct(bars: list[DatedBar], idx: int) -> float | None:
    if idx < 1:
        return None
    prior_close = bars[idx - 1]["c"]
    if prior_close == 0:
        return None
    return (bars[idx]["o"] / prior_close - 1) * 100


def _classify_entry_context(
    *,
    side: str | None,
    prior_return_3d: float | None,
    vs_sma200: str | None,
    dist_from_52w_high_pct: float | None,
) -> str | None:
    if side != "buy":
        return None
    if vs_sma200 == "below":
        return "dip_below_trend"
    if dist_from_52w_high_pct is not None and dist_from_52w_high_pct >= NEAR_52W_HIGH_PCT:
        return "breakout"
    if (
        prior_return_3d is not None
        and prior_return_3d <= PULLBACK_PRIOR_RETURN_3D_MAX
        and vs_sma200 == "above"
    ):
        return "pullback"
    if prior_return_3d is not None and prior_return_3d >= MOMENTUM_PRIOR_RETURN_3D_MIN:
        return "momentum"
    return "neutral"


def _compute_context_row(
    event: dict[str, Any],
    bars: list[DatedBar] | None,
) -> dict[str, Any]:
    symbol = str(event.get("symbol") or "").upper()
    side = event.get("side")
    trade_date = _parse_trade_date(event)
    base: dict[str, Any] = {
        "symbol": symbol,
        "side": side,
        "trade_date": trade_date.isoformat() if trade_date else event.get("trade_date"),
        "prior_return_1d": None,
        "prior_return_3d": None,
        "prior_return_5d": None,
        "vs_sma50": None,
        "vs_sma200": None,
        "dist_from_52w_high_pct": None,
        "gap_prior": None,
        "entry_context": None,
        "context_available": False,
    }

    if trade_date is None or not bars:
        return base

    idx = _find_bar_index(bars, trade_date)
    if idx is None:
        return base

    window = bars[: idx + 1]
    if len(window) < MIN_BARS_FOR_CONTEXT:
        return base

    c = closes(window)
    close = c[-1]
    sma50 = sma(c, SMA50_PERIOD)
    sma200 = sma(c, SMA200_PERIOD)

    base.update(
        {
            "prior_return_1d": pct_change(c, 1),
            "prior_return_3d": pct_change(c, 3),
            "prior_return_5d": pct_change(c, 5),
            "vs_sma50": _price_vs_ma(close, sma50),
            "vs_sma200": _price_vs_ma(close, sma200),
            "dist_from_52w_high_pct": distance_from_high(c, HIGH_LOOKBACK),
            "gap_prior": _gap_prior_pct(window, idx),
            "context_available": True,
        }
    )
    base["entry_context"] = _classify_entry_context(
        side=side,
        prior_return_3d=base["prior_return_3d"],
        vs_sma200=base["vs_sma200"],
        dist_from_52w_high_pct=base["dist_from_52w_high_pct"],
    )
    return base


def build_trade_context(
    events: list[dict[str, Any]],
    bars_by_symbol: dict[str, list[DatedBar]],
) -> list[dict[str, Any]]:
    """Pure function: per-equity-trade market context from pre-fetched daily bars.

    Options are skipped in v1 (underlying bars deferred). Missing bars → null fields
    and ``context_available: false`` — never fabricated.
    """
    rows: list[dict[str, Any]] = []
    for event in events:
        if not _is_equity_trade(event):
            continue
        symbol = str(event.get("symbol") or "").upper()
        bars = bars_by_symbol.get(symbol)
        rows.append(_compute_context_row(event, bars))
    return rows


def entry_style_profile(
    events: list[dict[str, Any]],
    context: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate BUY entry-style metrics from per-trade context rows."""
    buy_events = [
        e
        for e in events
        if _is_equity_trade(e) and e.get("side") == "buy"
    ]
    context_by_key = {
        (row["symbol"], row["trade_date"], row["side"]): row for row in context
    }

    label_counts = {label: 0 for label in ENTRY_CONTEXT_LABELS}
    fights_trend = 0
    near_high = 0
    prior_3d_values: list[float] = []
    with_context = 0
    without_context = 0

    for event in buy_events:
        trade_date = _parse_trade_date(event)
        key = (
            str(event.get("symbol") or "").upper(),
            trade_date.isoformat() if trade_date else event.get("trade_date"),
            "buy",
        )
        row = context_by_key.get(key)
        if row is None or not row.get("context_available"):
            without_context += 1
            continue

        with_context += 1
        label = row.get("entry_context") or "neutral"
        if label in label_counts:
            label_counts[label] += 1

        if row.get("vs_sma200") == "below":
            fights_trend += 1

        dist = row.get("dist_from_52w_high_pct")
        if dist is not None and dist >= NEAR_52W_HIGH_PCT:
            near_high += 1

        prior_3d = row.get("prior_return_3d")
        if prior_3d is not None:
            prior_3d_values.append(float(prior_3d))

    total_buys = len(buy_events)
    classified = sum(label_counts.values())
    buys_by_context: dict[str, dict[str, float | int]] = {}
    for label, count in label_counts.items():
        buys_by_context[label] = {
            "count": count,
            "pct": round(count / classified * 100, 1) if classified else 0.0,
        }

    mean_reversion = label_counts["pullback"] + label_counts["dip_below_trend"]
    momentum = label_counts["breakout"] + label_counts["momentum"]
    style_total = mean_reversion + momentum
    if style_total == 0:
        style_lean = "mixed"
    else:
        mr_share = mean_reversion / style_total
        if mr_share >= 0.6:
            style_lean = "mean_reversion"
        elif mr_share <= 0.4:
            style_lean = "momentum"
        else:
            style_lean = "mixed"

    return {
        "buys_by_context": buys_by_context,
        "style_lean": style_lean,
        "fights_trend_pct": round(fights_trend / with_context * 100, 1)
        if with_context
        else None,
        "near_high_pct": round(near_high / with_context * 100, 1)
        if with_context
        else None,
        "avg_prior_move_into_buys": round(
            sum(prior_3d_values) / len(prior_3d_values), 2
        )
        if prior_3d_values
        else None,
        "coverage": {
            "total_equity_buys": total_buys,
            "with_context": with_context,
            "without_context": without_context,
            "pct_with_context": round(with_context / total_buys * 100, 1)
            if total_buys
            else 0.0,
        },
        "note": (
            "Descriptive entry-style only — not per-trade P&L. "
            "Options deferred (need underlying bars)."
        ),
    }


def _equity_symbols_from_events(events: list[dict[str, Any]]) -> list[str]:
    symbols: set[str] = set()
    for event in events:
        if not _is_equity_trade(event):
            continue
        symbols.add(str(event["symbol"]).upper())
    return sorted(symbols)


def _compute_fetch_days(events: list[dict[str, Any]]) -> int:
    dates: list[date] = []
    for event in events:
        if not _is_equity_trade(event):
            continue
        trade_date = _parse_trade_date(event)
        if trade_date is not None:
            dates.append(trade_date)
    if not dates:
        return WARMUP_BARS + 30
    earliest = min(dates)
    span = (date.today() - earliest).days + WARMUP_BARS + 30
    return max(span, WARMUP_BARS + 30)


async def fetch_bars_for_trade_context(
    events: list[dict[str, Any]],
    broker: Any,
) -> dict[str, list[DatedBar]]:
    """Fetch daily bars once per equity symbol (chunked), with ≥252-bar warmup."""
    symbols = _equity_symbols_from_events(events)
    if not symbols:
        return {}

    fetch = getattr(broker, "aget_stock_bars_batch", None)
    if not callable(fetch):
        logger.warning("Broker does not support batch bar fetch — skipping trade context")
        return {}

    days = _compute_fetch_days(events)
    symbol_bars: dict[str, list[DatedBar]] = {}
    for start in range(0, len(symbols), BAR_SYMBOL_BATCH):
        chunk = symbols[start : start + BAR_SYMBOL_BATCH]
        try:
            raw = await fetch(
                chunk,
                timeframe=BAR_TIMEFRAME,
                days=days,
            )
        except Exception:
            logger.exception("Trade-context bar fetch failed for symbols %s", chunk)
            continue
        for sym, bars in (raw or {}).items():
            try:
                dated = [_stockbar_to_dated(bar) for bar in bars]
                dated.sort(key=lambda b: b["d"])
                symbol_bars[str(sym).upper()] = dated
            except Exception:
                logger.exception("Failed to convert trade-context bars for %s", sym)
    return symbol_bars


def fetch_bars_for_trade_context_sync(
    events: list[dict[str, Any]],
    broker: Any,
) -> dict[str, list[DatedBar]]:
    """Sync wrapper — uses ``get_stock_bars_batch`` when available, else asyncio."""
    symbols = _equity_symbols_from_events(events)
    if not symbols:
        return {}

    sync_fetch = getattr(broker, "get_stock_bars_batch", None)
    if callable(sync_fetch):
        days = _compute_fetch_days(events)
        symbol_bars: dict[str, list[DatedBar]] = {}
        for start in range(0, len(symbols), BAR_SYMBOL_BATCH):
            chunk = symbols[start : start + BAR_SYMBOL_BATCH]
            try:
                raw = sync_fetch(
                    chunk,
                    timeframe=BAR_TIMEFRAME,
                    days=days,
                )
            except Exception:
                logger.exception("Trade-context bar fetch failed for symbols %s", chunk)
                continue
            for sym, bars in (raw or {}).items():
                try:
                    dated = [_stockbar_to_dated(bar) for bar in bars]
                    dated.sort(key=lambda b: b["d"])
                    symbol_bars[str(sym).upper()] = dated
                except Exception:
                    logger.exception("Failed to convert trade-context bars for %s", sym)
        return symbol_bars

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(fetch_bars_for_trade_context(events, broker))
    if loop.is_running():
        logger.warning("Cannot fetch trade-context bars synchronously inside running loop")
        return {}
    return loop.run_until_complete(fetch_bars_for_trade_context(events, broker))


def build_entry_style_block(
    events: list[dict[str, Any]],
    *,
    bars_by_symbol: dict[str, list[DatedBar]] | None = None,
    broker: Any | None = None,
) -> dict[str, Any]:
    """Build entry_style profile (+ optional per-trade rows) from events and bars."""
    if bars_by_symbol is None:
        bars_by_symbol = {}
        if broker is not None:
            bars_by_symbol = fetch_bars_for_trade_context_sync(events, broker)

    context_rows = build_trade_context(events, bars_by_symbol)
    profile = entry_style_profile(events, context_rows)
    return {
        "profile": profile,
        "trades": context_rows,
    }
