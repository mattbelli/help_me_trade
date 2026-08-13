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
import bisect
import logging
from datetime import date, timedelta
from pathlib import Path

import httpx

from config.settings import fred_api_key, fred_is_configured
from db.macro import (
    DEFAULT_MACRO_DB_PATH,
    events_between,
    series_history,
    upsert_events,
    upsert_macro,
)
from signals.market_internals import DERIVED_SERIES, compute_internals
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
    # The channel credit spreads MISS: SOFR pulling UP toward (or above) what the
    # Fed pays on reserves means someone is paying up for overnight cash.
    # ⚠️ HONESTY FENCE — this axis CANNOT speak to Sept 2019 or March 2020, and an
    # earlier version of this comment wrongly claimed it could. `IORB` begins
    # 2021-07-29, so the reference window is entirely post-RRP; its median is
    # about -0.09 and the resting ample-reserves state is a small NEGATIVE gap.
    # A "high" bucket here therefore means "firm relative to the last four
    # years", NOT "2019-style funding stress". FRED's pre-2021 equivalent is
    # IOER, which is a different series and is not collected.
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
    # Implied correlation — the market's FORECAST of how much stocks move as one
    # blob. Pairs with the realized measure computed from our own bars in
    # signals/market_internals.py: two independent sources for the one regime
    # question a stock picker actually cares about, and when they agree the read
    # is trustworthy in a way a single number never is.
    "COR1M": "correlation",
    "COR3M": "correlation",
    # What the market pays for crash protection, from the shape of the SPX put
    # skew. Publishes ONCE at settle, so open==high==low==last by design — that
    # is not a frozen feed, and prev_day_close differs day to day (137.13 -> 135.59
    # on 08-11). Typical range is roughly 110-145.
    "SKEW": "tail_risk",
}
# ⚠️ DELIBERATELY NOT COLLECTED — checked 2026-08-11, do not "fix" by adding them.
# `_VXTLT` (bond vol) and `_VXEEM` (EM vol) both serve current_price EXACTLY equal
# to prev_day_close while simultaneously reporting a real intraday range
# (VXEEM: open 34.52, low 28.01, "current" 29.03 == yesterday's close). Two exact
# matches on the same day is not coincidence, and their timestamps carry a
# different timezone offset and stop ~16:02 ET rather than at the settle. Until
# that is understood they would ship a wrong LEVEL wearing a confident percentile.
CBOE_QUOTE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_{}.json"

# FRED release id -> label. Scheduled events the tape actually reprices on, which
# is a much shorter list than FRED's ~300 daily releases. Keyed by ID, not name,
# because names get reworded upstream.
# ⚠️ Release 101 "FOMC Press Release" is DELIBERATELY ABSENT: FRED marks it on
# EVERY calendar day (measured 36/37 days in a 37-day window vs 1/37 for CPI), so
# whitelisting it would tell the agent there is a Fed decision daily — a calendar
# that is worse than no calendar. Release 326 carries the real quarterly meeting
# dates instead. ⚠️ That means the four NON-projection FOMC meetings a year are
# ABSENT from this calendar; FRED has no free source for them.
MACRO_EVENT_RELEASES: dict[int, str] = {
    10: "CPI",
    46: "PPI",
    50: "Employment Situation",
    53: "GDP",
    54: "Personal Income and Outlays (PCE)",
    9: "Retail Sales",
    180: "Jobless Claims",
    192: "JOLTS",
    326: "FOMC projections",
}
FRED_RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"
# A scheduled event is rare by definition. Weekly claims — the densest real entry
# — land on ~13.5% of days; the FOMC artifact lands on 97.3%. Anything above this
# is a continuously-updated series wearing an event's clothes, so drop it and say
# so rather than flooding the agent with a daily "event".
_EVENT_MAX_DAY_SHARE = 0.5

_CBOE_TIMEOUT = 15.0
_FRED_API_TIMEOUT = 30.0
_REFRESH_LIMIT = 15  # daily job: recent observations only
_BACKFILL_LIMIT = 1_000_000  # fredgraph.csv returns full history; take it all

# Reporting definitions (documented constants, per the honesty fence).
REPORT_STALE_DAYS = 7  # as-of lookback; beyond this the date reports "missing"
BUCKET_LOW_PCTILE = 33.0  # house cut — signals/volatility.py CALM_PCTILE
BUCKET_HIGH_PCTILE = 80.0  # house cut — signals/volatility.py STORM_PCTILE
# A percentile needs a population. ~1 quarter of sessions is the floor below
# which macro_snapshot reports pctile/bucket as None rather than a number that
# is arithmetically true and practically a lie.
_PCTILE_MIN_OBS = 60

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
    # The two that say whether SINGLE-NAME work is worth doing at all, which is
    # the whole product. High correlation = everything trades as one macro blob
    # and selection is just leveraged beta; narrow breadth = the index is being
    # carried by a handful of names and a $10B-$500B book is not in them.
    # Realized, not implied, is the axis: COR1M/COR3M are forward-only from
    # 2026-08-11 and cannot be ranked for another quarter — they sit in `series`
    # as the independent cross-check.
    "correlation": "corr_realized_21d",
    "breadth": "breadth_above_200dma",
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
    """One Cboe index snapshot -> a single row, or [] if the payload is not usable.

    The payload's own ``last_trade_time`` supplies the date: it is ET, the box
    clock is UTC, and after 20:00 UTC those disagree — dating a session by the
    box would file the close under tomorrow. A STALE timestamp is harmless (it
    rewrites the prior session's own value under the prior date — idempotent).

    Both validations below are load-bearing, not defensive noise. These four
    series are forward-only with no backfill, so a bad row is permanent:
      - a null/garbage ``last_trade_time`` used to slice to the literal string
        ``'None'``, which sorts AFTER every real ISO date, so it became the
        series' latest observation forever while `_as_of` threw it out — the two
        surfaces then disagreed about the same series.
      - Cboe emits ``0.0`` placeholders freely elsewhere in this payload
        (``bid``/``ask``/``iv30``), and a volatility index is never 0 or negative.
    """
    resp = httpx.get(CBOE_QUOTE_URL.format(index), timeout=_CBOE_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()["data"]
    obs_date = str(payload.get("last_trade_time") or "")[:10]
    try:
        date.fromisoformat(obs_date)
        value = float(payload["current_price"])
    except (TypeError, ValueError):
        logger.warning("%s: unusable Cboe payload %r", index, payload)
        return []
    if value <= 0.0:
        logger.warning("%s: Cboe returned a non-positive level %r", index, value)
        return []
    return [(index, obs_date, value)]


def refresh_macro_events(
    *,
    start: str | None = None,
    days: int = 120,
    db_path: Path = DEFAULT_MACRO_DB_PATH,
) -> dict[str, int]:
    """Scheduled macro-release dates into the store. Fail-soft per release.

    Needs the FRED key (the keyless fredgraph.csv endpoint serves series, not the
    release calendar), so it no-ops without one rather than failing the refresh.
    """
    if not fred_is_configured():
        logger.info("macro events: no FRED key configured, skipping")
        return {}
    begin = start or (date.today() - timedelta(days=14)).isoformat()
    end = (date.fromisoformat(begin) + timedelta(days=days)).isoformat()
    span_days = (date.fromisoformat(end) - date.fromisoformat(begin)).days or 1
    out: dict[str, int] = {}
    for release_id, label in MACRO_EVENT_RELEASES.items():
        try:
            payload = httpx.get(
                FRED_RELEASE_DATES_URL,
                params={
                    "api_key": fred_api_key(),
                    "file_type": "json",
                    "release_id": release_id,
                    "realtime_start": begin,
                    "realtime_end": end,
                    "sort_order": "asc",
                    "limit": 1000,
                    # Without this the endpoint returns only dates that ALREADY
                    # released — i.e. the entire forward calendar, the only part
                    # that matters, is silently missing.
                    "include_release_dates_with_no_data": "true",
                },
                timeout=_FRED_API_TIMEOUT,
            )
            payload.raise_for_status()
            dates = [r["date"] for r in payload.json().get("release_dates", [])]
        except Exception:
            logger.warning("macro events fetch failed for %s", label, exc_info=True)
            out[label] = 0
            continue
        if len(set(dates)) > span_days * _EVENT_MAX_DAY_SHARE:
            logger.warning(
                "macro events: %s marked on %d of %d days — not an event, dropped",
                label, len(set(dates)), span_days,
            )
            out[label] = 0
            continue
        out[label] = upsert_events(
            [(d, label, release_id) for d in dates], db_path=db_path
        )
    return out


def refresh_cboe_curve(*, db_path: Path = DEFAULT_MACRO_DB_PATH) -> dict[str, int]:
    """Snapshot the Cboe vol curve into the store. Fail-soft per index.

    Two callers by design: the daily loop keeps today's row live intraday, and a
    close-time cron overwrites it with the settle — these percentiles rank against
    FRED CLOSING history, so an 11am reading is not comparable to a close. Upsert,
    so only the ORDER of the two writes matters, never the count.
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
    try:
        written += sum(refresh_macro_events(db_path=db_path).values())
    except Exception:
        logger.warning("macro events refresh failed", exc_info=True)
    # Derived from our own bars, so it needs no vendor and cannot fail on one —
    # but it reads a different store, so it fails independently of everything above.
    try:
        written += sum(
            (await asyncio.to_thread(compute_internals, macro_db_path=db_path)).values()
        )
    except Exception:
        logger.warning("market internals recompute failed", exc_info=True)
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
    """Percent of history at or BELOW-OR-EQUAL ``value`` — self-relative, like
    volatility.py. The tie convention is not neutral on step-function series: a
    policy rate like IORB sits at its current level for hundreds of sessions, so
    ties inflate the rank (IORB at 3.65 reads 38.4 counting ties, 25.1 counting
    strictly-below — opposite sides of the 33.0 bucket cut). Kept for consistency
    with the house definition; read a policy-rate percentile with that in mind."""
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


# Why a series is shorter than an agent expects, where we KNOW why. Without this an
# agent that asks for 2008 credit spreads, gets a 2021 start and no reason, concludes
# the app is incomplete and goes to the open web for data we already reason about —
# the exact failure the perception layer exists to stop.
DEPTH_NOTES: dict[str, str] = {
    "BAMLH0A0HYM2": "ICE truncates BAML OAS history at FRED to a rolling window; "
                    "2021-07-29 onward is a verified one-time import and is ALL that "
                    "exists here — 2008/2020 credit is not obtainable free, so do not "
                    "go looking for it, and read this axis as calm-vs-recent-years",
    "BAMLC0A0CM": "same ICE truncation as HY OAS — this window has no crisis in it",
}


def known_names() -> dict[str, list[str]]:
    """{"axes": [...], "series": [...]} — what `history()` will resolve."""
    return {
        "axes": sorted(REPORT_AXES),
        "series": sorted({**MACRO_SERIES, **CBOE_SERIES, **DERIVED_SERIES}),
    }


def history(
    name: str,
    *,
    db_path: Path | None = None,
) -> list[tuple[str, float]] | None:
    """(date, value) ASCENDING for a series id, a report AXIS name, or an `A-B`
    spread; None if the name resolves to nothing.

    The drill-down behind the snapshot. An axis name resolves through REPORT_AXES to
    exactly the series the report ranked — ask for "credit" or "BAMLH0A0HYM2" and the
    numbers agree, and "curve" gets the date-JOINED spread rather than two levels the
    caller would have to subtract (and would subtract across dates sooner or later).
    """
    # Resolved at CALL time, never as a default argument — a module-level default
    # binds at import and silently pins every caller (tests included) to the real
    # store, the lesson db/oi_walls.py records against standing_walls.
    db_path = db_path or DEFAULT_MACRO_DB_PATH
    target = REPORT_AXES.get(name, name)
    known = {**MACRO_SERIES, **CBOE_SERIES, **DERIVED_SERIES}
    parts = target.split("-", 1) if "-" in target else [target]
    if not all(p in known for p in parts):
        return None
    return sorted(_axis_history(target, db_path).items())


def event_calendar(
    today: str,
    *,
    ahead_days: int = 10,
    db_path: Path = DEFAULT_MACRO_DB_PATH,
) -> dict:
    """{"today": [...], "upcoming": [{date, release, in_days}]} — what reprices the
    tape on a schedule. Blindness to this is what it fixes: on 2026-08-11 neither
    paper book mentioned that CPI printed the next morning, because nothing in
    production had ever carried a macro calendar."""
    end = (date.fromisoformat(today) + timedelta(days=ahead_days)).isoformat()
    rows = events_between(today, end, db_path=db_path)
    here = date.fromisoformat(today)
    return {
        "today": [name for d, name in rows if d == today],
        "upcoming": [
            {
                "date": d,
                "release": name,
                "in_days": (date.fromisoformat(d) - here).days,
            }
            for d, name in rows
            if d != today
        ],
        # Stated, not silent: an agent that sees a calendar with no FOMC could
        # reasonably infer there is no meeting scheduled.
        "not_covered": "FOMC meetings without projections (no free source)",
    }


def regime_report(
    dates: list[str],
    *,
    db_path: Path = DEFAULT_MACRO_DB_PATH,
) -> dict:
    """Regime composition over a set of session dates — the prereg deliverable.

    Per axis: each date's as-of value (≤ REPORT_STALE_DAYS old, else counted
    ``missing`` — never silently dropped), its percentile, and the 33/80 bucket.
    ``composition`` is the bucket count the study prints next to every result.

    The percentile population is **truncated at the session being reported**, and
    thin populations report no rank at all (``unranked``) rather than a number.
    Both rules match ``macro_snapshot``; before 2026-08-11 this function had
    neither. ``signals/dossier.py`` calls this per BACKDATED episode day, so a
    full-history percentile stamped a historical session with a bucket computed
    from its own future — measured bucket flips, e.g. credit on 2024-04-16 read
    ``mid`` on full history and ``low`` on what was actually known that day.
    That is the claim-matrix inversion in another costume."""
    report: dict = {"dates": len(dates), "axes": {}}
    for axis, axis_series in REPORT_AXES.items():
        history = _axis_history(axis_series, db_path)
        ordered_dates = sorted(history)
        values = [history[d] for d in ordered_dates]
        per_date: dict[str, dict] = {}
        composition = {"low": 0, "mid": 0, "high": 0, "unranked": 0, "missing": 0}
        for session in dates:
            used = _as_of(ordered_dates, session)
            if used is None:
                composition["missing"] += 1
                per_date[session] = {"value": None, "date_used": None}
                continue
            value = history[used]
            known = values[: bisect.bisect_right(ordered_dates, used)]
            pctile = (
                _percentile(known, value) if len(known) >= _PCTILE_MIN_OBS else None
            )
            bucket = None if pctile is None else _bucket(pctile)
            composition["unranked" if bucket is None else bucket] += 1
            per_date[session] = {
                "value": value,
                "date_used": used,
                "pctile": pctile,
                "bucket": bucket,
                # The population this rank was actually computed against, not the
                # store's total — the two differ for every backdated session.
                "ranked_against": len(known),
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
    """Latest value per stored series, each WITH its own percentile and bucket.

    Walks BOTH source dicts — a series stored but absent here is invisible to
    every agent surface, the failure this section exists to stop. Every series is
    ranked because a bare "OVXCLS 56.06" is unreasonable-about; the ranking
    population is AS-OF TRUNCATED, so a backtest reading this cannot see its own
    future, and the buckets are the house 33/80 REPORTING cuts, never signals.
    """
    out: dict[str, dict] = {}
    for series_id, axis in {**MACRO_SERIES, **CBOE_SERIES, **DERIVED_SERIES}.items():
        rows = [(d, v) for d, v in series_history(series_id, db_path=db_path)
                if as_of is None or d <= as_of]
        if not rows:
            continue
        values = [v for _, v in rows]
        # Thin history withholds the rank — see _PCTILE_MIN_OBS for why.
        pctile = (
            _percentile(values, values[-1]) if len(rows) >= _PCTILE_MIN_OBS else None
        )
        out[series_id] = {
            "axis": axis,
            "date": rows[-1][0],
            "value": values[-1],
            "pctile": pctile,
            "bucket": None if pctile is None else _bucket(pctile),
            "history_n": len(rows),
            # Depth without a START DATE is not disclosure: DGS1MO at 80th
            # ("high") and DGS3MO at 50th ("mid") are the same ~3.8% rate ranked
            # against windows beginning 2001 and 1981. The reader cannot see that
            # without this field, and the block's own note tells them to look.
            "history_since": rows[0][0],
        }
    return out
