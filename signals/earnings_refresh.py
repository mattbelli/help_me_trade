"""Refresh the live earnings calendar from Finnhub.

Pulls a window of recent + upcoming earnings (one Finnhub call covers all
companies), filters to the symbols we actually trade, and upserts into the
`earnings_calendar` table. Run on a daily schedule; safe to run repeatedly.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from db.base import DEFAULT_DB_PATH
from db.earnings_calendar import upsert_earnings
from signals.finnhub import (
    FinnhubError,
    afetch_earnings_calendar,
    fetch_earnings_calendar,
    finnhub_is_configured,
)

logger = logging.getLogger(__name__)

# Look back far enough to always have the prior earnings (and its reaction), and
# forward far enough to see the next scheduled date before the entry window opens.
DEFAULT_DAYS_BACK = 120
DEFAULT_DAYS_FWD = 45


def _window(today: datetime, days_back: int, days_fwd: int) -> tuple[str, str]:
    start = (today - timedelta(days=days_back)).date().isoformat()
    end = (today + timedelta(days=days_fwd)).date().isoformat()
    return start, end


def _filter_to_universe(
    rows: list[dict[str, str]], symbols: list[str]
) -> list[dict[str, str]]:
    universe = {s.strip().upper() for s in symbols if s and s.strip()}
    return [r for r in rows if r["symbol"] in universe]


def refresh_earnings_calendar(
    symbols: list[str],
    *,
    days_back: int = DEFAULT_DAYS_BACK,
    days_fwd: int = DEFAULT_DAYS_FWD,
    now: datetime | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Sync earnings dates for `symbols`. Returns rows upserted. 0 if unconfigured."""
    if not finnhub_is_configured():
        logger.info("Earnings refresh skipped — FINNHUB_API_KEY not set")
        return 0
    start, end = _window(now or datetime.now(UTC), days_back, days_fwd)
    try:
        rows = fetch_earnings_calendar(start, end)
    except FinnhubError:
        logger.exception("Earnings calendar fetch failed (%s..%s)", start, end)
        return 0
    scoped = _filter_to_universe(rows, symbols)
    upserted = upsert_earnings(scoped, db_path=db_path)
    logger.info(
        "Earnings calendar refreshed: %d in window, %d in universe, %d upserted",
        len(rows),
        len(scoped),
        upserted,
    )
    return upserted


async def arefresh_earnings_calendar(
    symbols: list[str],
    *,
    days_back: int = DEFAULT_DAYS_BACK,
    days_fwd: int = DEFAULT_DAYS_FWD,
    now: datetime | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Async variant for the scheduler loop."""
    if not finnhub_is_configured():
        logger.info("Earnings refresh skipped — FINNHUB_API_KEY not set")
        return 0
    start, end = _window(now or datetime.now(UTC), days_back, days_fwd)
    try:
        rows = await afetch_earnings_calendar(start, end)
    except FinnhubError:
        logger.exception("Earnings calendar fetch failed (%s..%s)", start, end)
        return 0
    scoped = _filter_to_universe(rows, symbols)
    upserted = upsert_earnings(scoped, db_path=db_path)
    logger.info(
        "Earnings calendar refreshed: %d in window, %d in universe, %d upserted",
        len(rows),
        len(scoped),
        upserted,
    )
    return upserted
