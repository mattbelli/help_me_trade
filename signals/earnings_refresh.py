"""Refresh the live earnings calendar from Alpha Vantage + Finnhub.

Alpha Vantage provides the forward calendar (including mega-caps) in one call;
Finnhub supplements past dates for last-earnings lookups. Filters to the symbols
we actually trade and upserts into the `earnings_calendar` table. Run on a daily
schedule; safe to run repeatedly.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from db.base import DEFAULT_DB_PATH
from db.earnings_calendar import upsert_earnings
from signals.alphavantage import (
    afetch_earnings_calendar as av_afetch_earnings_calendar,
)
from signals.alphavantage import (
    alphavantage_is_configured,
)
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


# Finnhub /calendar/earnings caps a response at ~1500 rows and returns a slice from one
# end, so a wide window silently drops the rest (e.g. a 165-day window returned only the
# far-future week, missing every recent report). Fetch in ≤30-day chunks to stay under
# the cap and capture the full range.
_MAX_CHUNK_DAYS = 30


def _chunk_windows(start: str, end: str, max_days: int = _MAX_CHUNK_DAYS) -> list[tuple[str, str]]:
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out: list[tuple[str, str]] = []
    cur = s
    while cur <= e:
        chunk_end = min(cur + timedelta(days=max_days - 1), e)
        out.append((cur.isoformat(), chunk_end.isoformat()))
        cur = chunk_end + timedelta(days=1)
    return out


def _filter_to_universe(
    rows: list[dict[str, str]], symbols: list[str]
) -> list[dict[str, str]]:
    universe = {s.strip().upper() for s in symbols if s and s.strip()}
    return [r for r in rows if r["symbol"] in universe]


async def _fetch_calendar_rows(start: str, end: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if alphavantage_is_configured():
        rows.extend(await av_afetch_earnings_calendar(horizon="6month"))
    if finnhub_is_configured():
        try:
            for chunk_start, chunk_end in _chunk_windows(start, end):
                rows.extend(await afetch_earnings_calendar(chunk_start, chunk_end))
        except Exception:
            logger.warning("Finnhub earnings calendar fetch failed", exc_info=True)
    return rows


def refresh_earnings_calendar(
    symbols: list[str],
    *,
    days_back: int = DEFAULT_DAYS_BACK,
    days_fwd: int = DEFAULT_DAYS_FWD,
    now: datetime | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Sync earnings dates for `symbols`. Returns rows upserted. 0 if unconfigured."""
    if not (alphavantage_is_configured() or finnhub_is_configured()):
        logger.info("Earnings refresh skipped — no earnings calendar API key configured")
        return 0
    start, end = _window(now or datetime.now(UTC), days_back, days_fwd)
    rows: list[dict[str, str]] = []
    if finnhub_is_configured():
        for chunk_start, chunk_end in _chunk_windows(start, end):
            try:
                rows.extend(fetch_earnings_calendar(chunk_start, chunk_end))
            except FinnhubError:
                logger.exception(
                    "Earnings calendar chunk fetch failed (%s..%s)", chunk_start, chunk_end
                )
                # Partial data beats none — keep the chunks that succeeded.
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
    if not (alphavantage_is_configured() or finnhub_is_configured()):
        logger.info("Earnings refresh skipped — no earnings calendar API key configured")
        return 0
    start, end = _window(now or datetime.now(UTC), days_back, days_fwd)
    rows = await _fetch_calendar_rows(start, end)
    scoped = _filter_to_universe(rows, symbols)
    upserted = upsert_earnings(scoped, db_path=db_path)
    logger.info(
        "Earnings calendar refreshed: %d in window, %d in universe, %d upserted",
        len(rows),
        len(scoped),
        upserted,
    )
    return upserted
