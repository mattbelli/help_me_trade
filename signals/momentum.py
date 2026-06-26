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

    # Min-real-history is enforced upstream: compute_target_set requires a 252-bar lookback
    # index, so any symbol with <252 real bars at rank_date is skipped (no padding in prod
    # bars). The remaining safety screen is the extreme-residual guard below.
    target = compute_target_set(bars, sector_map, rank_date)
    return _apply_extreme_residual_gate(target)


# Calibrated on 15yr Norgate (180 month-ends, 16,190 selections): legit residuals top out at
# +10.83 (CVNA's real 25x recovery), p99.9 = +5.0. Hard-drop > +12 passes every legit selection
# ever observed and catches data artifacts (e.g. SNDK's +32 spin-off glitch); soft-flag > +8.
RESIDUAL_HARD_DROP = 12.0
RESIDUAL_SOFT_FLAG = 8.0


def _apply_extreme_residual_gate(target: list[dict]) -> list[dict]:
    """Drop implausible-residual names (data artifacts) before they can be bought.

    Momentum auto-BUYS the top quintile, and "huge 12-month return" is exactly what a
    price-history glitch (spin-off / split / incomplete listing) produces — so a bad bar
    doesn't just add noise, it gets selected. Hard-drop > +12; soft-flag (keep + log) > +8.
    """
    screened: list[dict] = []
    for row in target:
        r = float(row.get("residual_momentum", 0.0))
        if r > RESIDUAL_HARD_DROP:
            logger.warning(
                "Momentum sanity gate: HARD-DROP %s — residual %.2f > %.1f (data artifact?)",
                row.get("symbol"),
                r,
                RESIDUAL_HARD_DROP,
            )
            continue
        if r > RESIDUAL_SOFT_FLAG:
            logger.warning(
                "Momentum sanity gate: SOFT-FLAG %s — residual %.2f > %.1f (review)",
                row.get("symbol"),
                r,
                RESIDUAL_SOFT_FLAG,
            )
        screened.append(row)
    return screened
