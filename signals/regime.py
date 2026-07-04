"""FRED-backed macro regime feed — yield curve + VIX for tactical sleeves."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from pathlib import Path

import httpx

from config.settings import fred_api_key, fred_is_configured
from db.base import DEFAULT_DB_PATH
from db.regime import latest_observation, upsert_regime
from trading.market_hours import _ET

logger = logging.getLogger(__name__)

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
_FRED_TIMEOUT = 30.0
_REGIME_SERIES = ("DGS10", "DGS2", "VIXCLS")

# VIX3M (FRED VXVCLS) is fetched via the KEYLESS CSV endpoint and stored under the
# macro series name "vix3m" (per References/REGIME_SLEEVES_SPEC.md). The vol_event
# term_flip trigger cannot fire without it.
VIX3M_SERIES = "vix3m"
VIX3M_FRED_ID = "VXVCLS"
_VIX3M_UPSERT_LIMIT = 30
_VIX3M_MAX_STALE_TRADING_DAYS = 3


def fetch_fred(
    series_id: str,
    *,
    api_key: str,
    limit: int = 15,
) -> list[tuple[str, float]]:
    """Fetch recent FRED observations (newest first). Fail-soft → []."""
    try:
        resp = httpx.get(
            FRED_OBSERVATIONS_URL,
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": limit,
            },
            timeout=_FRED_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError, TypeError):
        logger.warning("FRED fetch failed for %s", series_id, exc_info=True)
        return []

    raw = payload.get("observations") if isinstance(payload, dict) else None
    out: list[tuple[str, float]] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        obs_date = str(entry.get("date", "")).strip()
        value = str(entry.get("value", "")).strip()
        if not obs_date or value in (".", ""):
            continue
        try:
            out.append((obs_date, float(value)))
        except ValueError:
            continue
    return out


def parse_fred_csv(
    text: str,
    *,
    limit: int = _VIX3M_UPSERT_LIMIT,
) -> list[tuple[str, float]]:
    """Parse a fredgraph.csv payload (``observation_date,VALUE``; '.' = missing).

    Returns the most recent ``limit`` observations, NEWEST FIRST (matching
    ``fetch_fred``). Pure so tests can exercise it on fixture text.
    """
    rows: list[tuple[str, float]] = []
    for line in text.splitlines()[1:]:  # skip the header row
        parts = line.strip().split(",")
        if len(parts) != 2:
            continue
        obs_date, value = parts[0].strip(), parts[1].strip()
        if not obs_date or value in (".", ""):
            continue
        try:
            rows.append((obs_date, float(value)))
        except ValueError:
            continue
    rows.sort(key=lambda r: r[0])
    recent = rows[-limit:]
    recent.reverse()
    return recent


def fetch_fred_csv(
    series_id: str,
    *,
    limit: int = _VIX3M_UPSERT_LIMIT,
) -> list[tuple[str, float]]:
    """Fetch recent observations via the KEYLESS fredgraph CSV endpoint. Fail-soft → []."""
    try:
        resp = httpx.get(
            FRED_CSV_URL,
            params={"id": series_id},
            timeout=_FRED_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        text = resp.text
    except httpx.HTTPError:
        logger.warning("FRED CSV fetch failed for %s", series_id, exc_info=True)
        return []
    return parse_fred_csv(text, limit=limit)


async def refresh_regime_macro(*, db_path: Path = DEFAULT_DB_PATH) -> int:
    """Fetch DGS10, DGS2, VIXCLS (keyed API) + vix3m (keyless CSV) into regime_macro."""
    all_rows: list[tuple[str, str, float]] = []
    per_series: dict[str, int] = {}

    if fred_is_configured():
        api_key = fred_api_key()
        for series_id in _REGIME_SERIES:
            try:
                observations = await asyncio.to_thread(
                    fetch_fred, series_id, api_key=api_key
                )
            except Exception:
                logger.warning(
                    "FRED fetch failed for %s", series_id, exc_info=True
                )
                observations = []
            per_series[series_id] = len(observations)
            for obs_date, value in observations:
                all_rows.append((series_id, obs_date, value))
    else:
        logger.info(
            "FRED_API_KEY not set — skipping keyed regime series (vix3m still refreshes)"
        )

    # vix3m needs no API key — refresh it regardless of FRED_API_KEY.
    try:
        vix3m_obs = await asyncio.to_thread(fetch_fred_csv, VIX3M_FRED_ID)
    except Exception:
        logger.warning("FRED CSV fetch failed for %s", VIX3M_FRED_ID, exc_info=True)
        vix3m_obs = []
    per_series[VIX3M_SERIES] = len(vix3m_obs)
    for obs_date, value in vix3m_obs:
        all_rows.append((VIX3M_SERIES, obs_date, value))

    written = upsert_regime(all_rows, db_path=db_path)
    logger.info(
        "Regime macro refresh: upserted %d rows (DGS10=%d DGS2=%d VIXCLS=%d vix3m=%d obs)",
        written,
        per_series.get("DGS10", 0),
        per_series.get("DGS2", 0),
        per_series.get("VIXCLS", 0),
        per_series.get(VIX3M_SERIES, 0),
    )
    return written


def vix3m_is_fresh(
    *,
    max_stale_trading_days: int = _VIX3M_MAX_STALE_TRADING_DAYS,
    db_path: Path = DEFAULT_DB_PATH,
    now: date | None = None,
) -> bool:
    """True iff the latest vix3m observation is within max_stale_trading_days (Mon–Fri).

    Stale/missing vix3m does NOT error — the term_flip trigger goes DORMANT (visible
    in the sleeve status) until the series catches up.
    """
    from research.strategy_features import _trading_days_until

    obs = latest_observation(VIX3M_SERIES, db_path=db_path)
    if obs is None:
        return False
    today = now if now is not None else datetime.now(_ET).date()
    gap = _trading_days_until(obs[0], today.isoformat())
    return 0 <= gap <= max_stale_trading_days


def market_regime(
    *,
    as_of: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict | None:
    """Latest yield-curve + VIX snapshot, or None if any series is missing."""
    dgs10 = latest_observation("DGS10", as_of=as_of, db_path=db_path)
    dgs2 = latest_observation("DGS2", as_of=as_of, db_path=db_path)
    vix = latest_observation("VIXCLS", as_of=as_of, db_path=db_path)
    if dgs10 is None or dgs2 is None or vix is None:
        return None

    dgs10_date, dgs10_val = dgs10
    dgs2_date, dgs2_val = dgs2
    vix_date, vix_val = vix
    regime_as_of = max(dgs10_date, dgs2_date, vix_date)

    return {
        "as_of": regime_as_of,
        "term_spread": dgs10_val - dgs2_val,
        "vix": vix_val,
        "dgs10": dgs10_val,
        "dgs2": dgs2_val,
    }


def regime_is_fresh(
    *,
    max_age_days: int = 5,
    db_path: Path = DEFAULT_DB_PATH,
    now: date | None = None,
) -> bool:
    """True iff the latest regime date is within max_age_days calendar days (ET)."""
    regime = market_regime(db_path=db_path)
    if regime is None:
        return False
    today = now if now is not None else datetime.now(_ET).date()
    latest = date.fromisoformat(str(regime["as_of"]))
    return (today - latest).days <= max_age_days
