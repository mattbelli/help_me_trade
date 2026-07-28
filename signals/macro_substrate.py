"""Macro regime substrate — keyless FRED series into the own-store macro.db.

Board card macro-regime-substrate. Everything here rides the KEYLESS
``fredgraph.csv`` endpoint through the existing ``signals.regime.fetch_fred_csv``
(never a second fetcher definition), per the data-vs-execution doctrine: the
product never depends on the founder's own vendor accounts. Alpaca fixed income
was verified NOT available (403 on both endpoints) and wrong-shaped regardless.

Two consumers, in order (the card's scope):
1. NOW — prereg regime-composition reporting: FLOW_BASELINE_PREREG_2026-07-26
   commits "regime composition reported with every result"; ``regime_report``
   is that report. Honesty fence: REPORTING composition is fine; SLICING study
   results by regime is post-hoc subgroup analysis and requires its own dated
   addendum before it runs. Buckets here are REPORTING DEFINITIONS — self-
   relative percentiles against the series' own full history at the house
   33/80 cut (signals/volatility.py CALM_PCTILE/STORM_PCTILE), never
   calibrated signals.
2. LATER — a perception input (the fusion regime dot keeps reading the small
   regime_macro table in trading.db until that consolidation is its own round).

The trend axis (up/down/sideways) is deliberately ABSENT: it derives from SPY
bars the flow study already holds; a licensed FRED equity series adds nothing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from pathlib import Path

from db.macro import (
    DEFAULT_MACRO_DB_PATH,
    latest_value,
    series_history,
    upsert_macro,
)
from signals.regime import fetch_fred_csv

logger = logging.getLogger(__name__)

# FRED id -> axis tag. Stored under the FRED id verbatim.
MACRO_SERIES: dict[str, str] = {
    "DGS1MO": "curve",
    "DGS3MO": "curve",
    "DGS6MO": "curve",
    "DGS1": "curve",
    "DGS2": "curve",
    "DGS5": "curve",
    "DGS7": "curve",
    "DGS10": "curve",
    "DGS20": "curve",
    "DGS30": "curve",
    "BAMLH0A0HYM2": "credit",  # HY OAS — what reprices equity risk (the card's point)
    "BAMLC0A0CM": "credit",  # IG corporate OAS
    "T10YIE": "inflation",  # 10y breakeven (direct series beats DGS10-DFII10 arithmetic)
    "DFII10": "real_yield",  # 10y TIPS real yield
    "DTWEXBGS": "dollar",  # broad trade-weighted dollar
    "VIXCLS": "vol",
}

_REFRESH_LIMIT = 15  # daily job: recent observations only
_BACKFILL_LIMIT = 1_000_000  # fredgraph.csv returns full history; take it all

# Reporting definitions (documented constants, per the honesty fence).
REPORT_STALE_DAYS = 7  # as-of lookback; beyond this the date reports "missing"
BUCKET_LOW_PCTILE = 33.0  # house cut — signals/volatility.py CALM_PCTILE
BUCKET_HIGH_PCTILE = 80.0  # house cut — signals/volatility.py STORM_PCTILE

# Report axes: representative series per axis. Curve is the date-joined
# DGS10 - DGS2 spread (regime_sleeves joins on date for the same reason the
# fusion regime dot refuses cross-date ratios).
REPORT_AXES: dict[str, str] = {
    "vol": "VIXCLS",
    "credit": "BAMLH0A0HYM2",
    "curve": "DGS10-DGS2",
    "dollar": "DTWEXBGS",
}


_BACKFILL_START = "1960-01-01"  # cosd — before every series' first observation


def _fetch_rows(
    series_id: str, limit: int, *, start: str | None = None
) -> list[tuple[str, str, float]]:
    observations = fetch_fred_csv(series_id, limit=limit, start=start)
    return [(series_id, obs_date, value) for obs_date, value in observations]


async def refresh_macro_substrate(*, db_path: Path = DEFAULT_MACRO_DB_PATH) -> int:
    """Daily refresh: recent observations for every series. Fail-soft per series."""
    written = 0
    fetched: dict[str, int] = {}
    for series_id in MACRO_SERIES:
        try:
            rows = await asyncio.to_thread(_fetch_rows, series_id, _REFRESH_LIMIT)
        except Exception:
            logger.warning("macro substrate fetch failed for %s", series_id, exc_info=True)
            rows = []
        fetched[series_id] = len(rows)
        if rows:
            written += upsert_macro(rows, db_path=db_path)
    empty = sorted(s for s, n in fetched.items() if n == 0)
    logger.info(
        "Macro substrate refresh: %d rows across %d/%d series%s",
        written,
        len(fetched) - len(empty),
        len(fetched),
        f" (empty: {', '.join(empty)})" if empty else "",
    )
    return written


def backfill_macro_substrate(*, db_path: Path = DEFAULT_MACRO_DB_PATH) -> dict[str, int]:
    """Full-history backfill, sequential. Returns per-series rows written (0 = failed)."""
    out: dict[str, int] = {}
    for series_id in MACRO_SERIES:
        rows = _fetch_rows(series_id, _BACKFILL_LIMIT, start=_BACKFILL_START)
        out[series_id] = upsert_macro(rows, db_path=db_path) if rows else 0
    return out


def _percentile(history: list[float], value: float) -> float:
    """Percent of history at or below ``value`` — self-relative, like volatility.py."""
    if not history:
        return 0.0
    below = sum(1 for v in history if v <= value)
    return round(100.0 * below / len(history), 1)


def _bucket(pctile: float) -> str:
    if pctile < BUCKET_LOW_PCTILE:
        return "low"
    if pctile >= BUCKET_HIGH_PCTILE:
        return "high"
    return "mid"


def _axis_history(axis_series: str, db_path: Path) -> dict[str, float]:
    """date -> value for a report axis; the curve axis is a date-joined spread."""
    if axis_series == "DGS10-DGS2":
        ten = dict(series_history("DGS10", db_path=db_path))
        two = dict(series_history("DGS2", db_path=db_path))
        return {d: round(ten[d] - two[d], 4) for d in ten.keys() & two.keys()}
    return dict(series_history(axis_series, db_path=db_path))


def regime_report(
    dates: list[str],
    *,
    db_path: Path = DEFAULT_MACRO_DB_PATH,
) -> dict:
    """Regime composition over a set of session dates — the prereg deliverable.

    Per axis: each date's as-of value (≤ REPORT_STALE_DAYS old, else counted
    ``missing`` — never silently dropped), its percentile against the axis's own
    FULL stored history, and the 33/80 bucket. ``composition`` is the bucket
    count the study prints next to every result."""
    report: dict = {"dates": len(dates), "axes": {}}
    for axis, axis_series in REPORT_AXES.items():
        history = _axis_history(axis_series, db_path)
        ordered_dates = sorted(history)
        values = [history[d] for d in ordered_dates]
        per_date: dict[str, dict] = {}
        composition = {"low": 0, "mid": 0, "high": 0, "missing": 0}
        for session in dates:
            used = _as_of(ordered_dates, session)
            if used is None:
                composition["missing"] += 1
                per_date[session] = {"value": None, "date_used": None}
                continue
            value = history[used]
            pctile = _percentile(values, value)
            bucket = _bucket(pctile)
            composition[bucket] += 1
            per_date[session] = {
                "value": value,
                "date_used": used,
                "pctile": pctile,
                "bucket": bucket,
            }
        report["axes"][axis] = {
            "series": axis_series,
            "composition": composition,
            "per_date": per_date,
        }
    return report


def _as_of(ordered_dates: list[str], session: str) -> str | None:
    """Latest history date ≤ session and within REPORT_STALE_DAYS, else None."""
    import bisect

    idx = bisect.bisect_right(ordered_dates, session)
    if idx == 0:
        return None
    candidate = ordered_dates[idx - 1]
    try:
        gap = (date.fromisoformat(session) - date.fromisoformat(candidate)).days
    except ValueError:
        return None
    return candidate if timedelta(days=gap) <= timedelta(days=REPORT_STALE_DAYS) else None


def macro_snapshot(
    *,
    as_of: str | None = None,
    db_path: Path = DEFAULT_MACRO_DB_PATH,
) -> dict[str, dict]:
    """Latest (date, value) per stored series — the substrate's quick health read."""
    out: dict[str, dict] = {}
    for series_id, axis in MACRO_SERIES.items():
        obs = latest_value(series_id, as_of=as_of, db_path=db_path)
        if obs is not None:
            out[series_id] = {"axis": axis, "date": obs[0], "value": obs[1]}
    return out
