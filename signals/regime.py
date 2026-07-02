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
_FRED_TIMEOUT = 30.0
_REGIME_SERIES = ("DGS10", "DGS2", "VIXCLS")


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


async def refresh_regime_macro(*, db_path: Path = DEFAULT_DB_PATH) -> int:
    """Fetch DGS10, DGS2, VIXCLS from FRED and upsert into regime_macro."""
    if not fred_is_configured():
        logger.info("FRED_API_KEY not set — skipping regime macro refresh")
        return 0

    api_key = fred_api_key()
    all_rows: list[tuple[str, str, float]] = []
    per_series: dict[str, int] = {}

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

    written = upsert_regime(all_rows, db_path=db_path)
    logger.info(
        "Regime macro refresh: upserted %d rows (DGS10=%d DGS2=%d VIXCLS=%d obs)",
        written,
        per_series.get("DGS10", 0),
        per_series.get("DGS2", 0),
        per_series.get("VIXCLS", 0),
    )
    return written


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
