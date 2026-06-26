"""Momentum universe bar refresh and production target-set ranking."""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from brokers.errors import BrokerError
from brokers.factory import get_market_data_provider
from db.base import DEFAULT_DB_PATH
from db.momentum_bars import load_daily_bars, upsert_daily_bars
from research.momentum_reference import compute_target_set, load_sector_map
from signals.strategy_universe import resolve_universe_spec

logger = logging.getLogger(__name__)

# CALENDAR days to fetch. compute_target_set needs MOM_LONG(252)+MOM_SKIP(21)=273 TRADING
# days of history at the rank date; 273 trading days ≈ 396 calendar days, so 480 gives a
# comfortable margin (~331 trading days). (Schwab's `days` param is a calendar lookback.)
DEFAULT_REFRESH_DAYS = 480


def _bar_date(time_str: str) -> str:
    return str(time_str).strip()[:10]


def _default_as_of(
    bars: dict[str, tuple[list[str], list[float], list[float]]],
) -> str | None:
    if "SPY" in bars and bars["SPY"][0]:
        return bars["SPY"][0][-1]

    latest_dates = [dates[-1] for dates, _closes, _opens in bars.values() if dates]
    if not latest_dates:
        return None
    return Counter(latest_dates).most_common(1)[0][0]


async def refresh_universe_bars(
    *,
    days: int = DEFAULT_REFRESH_DAYS,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Backfill/refresh daily bars for the S&P 500 momentum universe."""
    symbols = resolve_universe_spec("sp500")
    if not symbols:
        return 0

    logger.info(
        "Momentum bars refresh: fetching %d symbols (%d calendar days)",
        len(symbols),
        days,
    )

    try:
        provider = get_market_data_provider()
    except BrokerError as exc:
        logger.warning("Momentum bars refresh skipped — provider unavailable: %s", exc)
        return 0

    try:
        bars_by_symbol = await provider.aget_stock_bars_batch(
            symbols,
            timeframe="1Day",
            days=days,
        )
    except BrokerError:
        logger.exception("Momentum bars batch fetch failed")
        return 0
    except Exception:
        logger.exception("Momentum bars batch fetch failed")
        return 0

    rows: list[tuple[str, str, float, float]] = []
    missing: list[str] = []
    for sym in symbols:
        sym_bars = bars_by_symbol.get(sym)
        if not sym_bars:
            missing.append(sym)
            continue
        for bar in sym_bars:
            rows.append((sym, _bar_date(bar.time), float(bar.open), float(bar.close)))

    if missing:
        logger.warning(
            "Momentum bars refresh: no data for %d/%d symbols (sample: %s)",
            len(missing),
            len(symbols),
            ", ".join(missing[:5]),
        )

    if not rows:
        logger.info("Momentum bars refresh: no rows to upsert")
        return 0

    written = upsert_daily_bars(rows, db_path=db_path)
    logger.info(
        "Momentum bars refresh: upserted %d rows across %d symbols",
        written,
        len(bars_by_symbol),
    )
    return written


def production_target_set(
    *,
    as_of: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """Compute the live residual-momentum top-quintile target set."""
    sector_map = load_sector_map()
    universe = resolve_universe_spec("sp500")
    bars = load_daily_bars(universe, db_path=db_path)

    rank_date = as_of
    if not rank_date:
        rank_date = _default_as_of(bars)
    if not rank_date:
        return []

    return compute_target_set(bars, sector_map, rank_date)
