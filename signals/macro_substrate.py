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

import httpx

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
    # --- vol term structure (Section 1) ---------------------------------------
    # VIX alone is a level; the SHAPE is the regime read. VIX > VIX3M (inversion)
    # is the market pricing stress NOW rather than someday. 2007+ on FRED, so the
    # percentile has 19 years behind it — the near-term terms (VIX1D/9D) have no
    # FRED equivalent at all and arrive from Cboe below, forward-only.
    "VXVCLS": "vol_term",  # VIX3M
    # --- cross-asset vol ------------------------------------------------------
    # Where the fear actually is. Equity vol calm while oil or gold vol is bid is
    # a different regime from everything calm, and neither shows up in VIX.
    "VXNCLS": "vol_cross",  # Nasdaq-100
    "RVXCLS": "vol_cross",  # Russell 2000 — the small-cap read our universe cares about
    "OVXCLS": "vol_cross",  # crude oil
    "GVZCLS": "vol_cross",  # gold
    # --- funding / plumbing ---------------------------------------------------
    # The channel credit spreads MISS. Sept 2019 and March 2020 both showed here
    # days before equities cared: SOFR pulling above what the Fed pays on reserves
    # means someone is paying up for overnight cash.
    "SOFR": "funding",
    "IORB": "funding",
    "RRPONTSYD": "funding",  # overnight reverse repo — cash with nowhere to go
    "WRESBAL": "funding",  # bank reserve balances
}

# Cboe index -> axis tag. NOT on FRED at any depth (checked 2026-08-11), so these
# start from the day we begin polling and carry no backfill. Free, keyless, and
# the only source for the near end of the curve.
CBOE_SERIES: dict[str, str] = {
    "VIX1D": "vol_term",
    "VIX9D": "vol_term",
    "VIX6M": "vol_term",
    "VVIX": "vol_term",  # vol OF vol — how jumpy VIX itself is priced to be
}
CBOE_QUOTE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_{}.json"

_CBOE_TIMEOUT = 15.0
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
    # Both are spreads for the same reason the curve is: the LEVEL of SOFR says
    # what the Fed set, the GAP to IORB says whether anyone is scrambling. Same
    # for vol — VIX at 15 is meaningless without knowing 3-month vol is 19.
    "vol_term": "VIXCLS-VXVCLS",  # negative = normal, positive = inverted
    "funding": "SOFR-IORB",
}


_BACKFILL_START = "1960-01-01"  # cosd — before every series' first observation
# ⚠️ ICE BofA OAS (BAML*) history is TRUNCATED AT THE VENDOR to a rolling ~3-year
# window — measured 2026-07-28 on BOTH the keyless CSV (cosd/coed ignored) and
# the free keyed observations API (identical 2023-07-28 start). This is ICE's
# licensing restriction at FRED, not an endpoint quirk; no fetch parameter
# recovers 2008/2020. regime_report DISCLOSES per-axis reference depth so no
# reader mistakes a shallow percentile for a 30-year one.
# PATCHED 2026-07-28: the live store carries HY OAS back to 2021-07-29 (the
# 2022 hiking-cycle widening, peak 5.99) via a one-time verified import of
# References/convex-bamlh0a0hym2-1825d.csv (Convex Trade, CC BY 4.0; 785/785
# overlap dates matched FRED exactly) — see tools/macro_import_csv.py. FRED
# refreshes still truncate, but upserts never delete the imported history.


def _fetch_rows(
    series_id: str, limit: int, *, start: str | None = None
) -> list[tuple[str, str, float]]:
    observations = fetch_fred_csv(series_id, limit=limit, start=start)
    return [(series_id, obs_date, value) for obs_date, value in observations]


def _fetch_cboe(index: str) -> list[tuple[str, str, float]]:
    """One Cboe index snapshot -> a single row. The payload's own ``last_trade_time``
    supplies the date: it is ET, the box clock is UTC, and after 20:00 UTC those
    disagree — dating a session by the box would file the close under tomorrow."""
    resp = httpx.get(CBOE_QUOTE_URL.format(index), timeout=_CBOE_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()["data"]
    return [(index, str(payload["last_trade_time"])[:10],
             float(payload["current_price"]))]


def refresh_cboe_curve(*, db_path: Path = DEFAULT_MACRO_DB_PATH) -> dict[str, int]:
    """Snapshot the Cboe vol curve into the store. Fail-soft per index.

    Called from two places on purpose. The backend's daily loop keeps today's row
    LIVE during the session (an agent asking "what is vol doing right now" gets a
    real answer); a close-time cron then overwrites it with the settle, because
    the percentile these feed is computed against FRED CLOSING history and an
    11am reading is not comparable to a close. Upsert semantics make the last
    write of the day win, so ordering is the only thing that matters, not count.
    """
    out: dict[str, int] = {}
    for index in CBOE_SERIES:
        try:
            rows = _fetch_cboe(index)
        except Exception:
            logger.warning("Cboe fetch failed for %s", index, exc_info=True)
            rows = []
        out[index] = upsert_macro(rows, db_path=db_path) if rows else 0
    return out


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
    cboe = refresh_cboe_curve(db_path=db_path)
    written += sum(cboe.values())
    fetched.update(cboe)
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
    """date -> value for a report axis. An ``A-B`` axis is a date-JOINED spread —
    never a cross-date subtraction, the same rule the fusion regime dot follows.
    No series id contains a hyphen, so the split is unambiguous."""
    if "-" in axis_series:
        a, b = axis_series.split("-", 1)
        left = dict(series_history(a, db_path=db_path))
        right = dict(series_history(b, db_path=db_path))
        return {d: round(left[d] - right[d], 4) for d in left.keys() & right.keys()}
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
            # Reference-depth disclosure: percentiles are only as honest as the
            # history behind them (credit is vendor-truncated to ~3 years).
            "history": {
                "n": len(values),
                "first": ordered_dates[0] if ordered_dates else None,
                "last": ordered_dates[-1] if ordered_dates else None,
            },
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
    """Latest (date, value) per stored series — the substrate's quick health read.

    Walks BOTH source dicts: a series stored but absent from this snapshot is
    invisible to every agent surface, which is the failure this whole section
    exists to stop.
    """
    out: dict[str, dict] = {}
    for series_id, axis in {**MACRO_SERIES, **CBOE_SERIES}.items():
        obs = latest_value(series_id, as_of=as_of, db_path=db_path)
        if obs is not None:
            out[series_id] = {"axis": axis, "date": obs[0], "value": obs[1]}
    return out
