"""Intraday volume profile and relative volume (RVOL) for a symbol."""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from brokers.alpaca import StockBar
from brokers.errors import BrokerError
from research.intraday_profile import (
    GLOBEX_N_BUCKETS,
    globex_bucket_index,
    globex_session_date,
)
from trading.market_hours import _ET, _SESSION_CLOSE, _SESSION_OPEN

logger = logging.getLogger(__name__)

N_BUCKETS = 78
DEFAULT_WINDOW = 20
WARMUP_MIN = 15

_SESSION_MINUTES = (_SESSION_CLOSE[0] * 60 + _SESSION_CLOSE[1]) - (
    _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
)


def _bar_et(bar: StockBar) -> datetime:
    return datetime.fromisoformat(bar.time.replace("Z", "+00:00")).astimezone(_ET)


def _bucket_index(et: datetime) -> int | None:
    minutes = (et.hour * 60 + et.minute) - (_SESSION_OPEN[0] * 60 + _SESSION_OPEN[1])
    if minutes < 0 or minutes >= _SESSION_MINUTES:
        return None
    return minutes // 5


def _rth_session_date(et: datetime) -> date:
    """RTH session date = the calendar date (an RTH session sits within one day)."""
    return et.date()


def _session_config(session: str) -> tuple:
    """Return (bucket_fn, date_fn, n_buckets, qualify_min, settled_min).

    date_fn: maps a bar's ET time to the SESSION it belongs to. For RTH that's the
        calendar date; for Globex a session straddles midnight (18:00 ET prev day ->
        17:00 ET), so it must use ``globex_session_date`` — grouping futures bars by
        calendar date would split each session in half.
    qualify_min: minimum last-populated bucket index to count a day as a full session.
    settled_min: last_bucket index at which RVOL flips to "settled".
    """
    if session == "globex":
        return globex_bucket_index, globex_session_date, GLOBEX_N_BUCKETS, 270, 275
    return _bucket_index, _rth_session_date, N_BUCKETS, 76, 77


def _profile_path(symbol: str) -> Path:
    """Return the JSON path for a symbol's profile.

    Futures symbols start with "/" (e.g. "/ES"). Placing them in a ``futures/``
    subdirectory avoids two bugs:
    - ``Path(dir) / "/ES.json"`` resolves to an absolute path at filesystem root.
    - Collision with an equity whose ticker matches the stripped symbol (e.g.
      equity ES = Eversource Energy).
    Equity paths are unchanged.
    """
    if symbol.startswith("/"):
        return profile_dir() / "futures" / f"{symbol.lstrip('/')}.json"
    return profile_dir() / f"{symbol}.json"


@dataclass(frozen=True)
class Profile:
    symbol: str
    cumfrac: list[float]
    median_daily_total: float
    n_days: int


def compute_profile(
    symbol: str,
    bars: list[StockBar],
    *,
    window: int = DEFAULT_WINDOW,
    min_days: int = 5,
    session: str = "rth",
) -> Profile | None:
    bucket_fn, date_fn, n_buckets, qualify_min, _settled = _session_config(session)
    by_date: dict[date, list[float]] = {}

    for bar in bars:
        et = _bar_et(bar)
        idx = bucket_fn(et)
        if idx is None:
            continue
        d = date_fn(et)
        if d not in by_date:
            by_date[d] = [0.0] * n_buckets
        by_date[d][idx] += bar.volume

    qualifying: list[tuple[datetime.date, list[float], float]] = []
    for d, vector in by_date.items():
        total = sum(vector)
        if total <= 0:
            continue
        last_populated = max(i for i, v in enumerate(vector) if v > 0)
        if last_populated < qualify_min:
            continue
        qualifying.append((d, vector, total))

    qualifying.sort(key=lambda x: x[0])
    kept = qualifying[-window:]

    if len(kept) < min_days:
        return None

    fractions = [[v / total for v in vector] for _, vector, total in kept]
    expected_fraction = [statistics.median(fs) for fs in zip(*fractions, strict=True)]
    frac_sum = sum(expected_fraction)
    if frac_sum <= 0:
        return None
    expected_fraction = [f / frac_sum for f in expected_fraction]

    cumfrac: list[float] = []
    running = 0.0
    for frac in expected_fraction:
        running += frac
        cumfrac.append(running)

    median_daily_total = statistics.median(total for _, _, total in kept)

    return Profile(
        symbol=symbol,
        cumfrac=cumfrac,
        median_daily_total=median_daily_total,
        n_days=len(kept),
    )


@dataclass(frozen=True)
class RvolResult:
    value: float | None
    state: str


def rvol_now(
    profile: Profile | None,
    today_bars: list[StockBar],
    *,
    session: str = "rth",
) -> RvolResult | None:
    if profile is None or not today_bars:
        return None

    bucket_fn, date_fn, _n_buckets, _qualify_min, settled_min = _session_config(session)

    # Group bars by SESSION (not calendar date — a Globex session straddles
    # midnight), keeping only bars actually inside a session (break bars dropped).
    in_session: list[tuple[date, StockBar]] = []
    for b in today_bars:
        et = _bar_et(b)
        if bucket_fn(et) is None:
            continue
        in_session.append((date_fn(et), b))
    if not in_session:
        return None

    latest_date = max(d for d, _ in in_session)
    session_bars = [b for d, b in in_session if d == latest_date]

    last_bucket = max(bucket_fn(_bar_et(b)) for b in session_bars)
    if last_bucket < (WARMUP_MIN // 5):
        return RvolResult(None, "warming")

    today_cum = sum(b.volume for b in session_bars)
    expected_cum = profile.median_daily_total * profile.cumfrac[last_bucket]
    if expected_cum <= 0:
        return None

    value = round(today_cum / expected_cum, 2)
    state = "settled" if last_bucket >= settled_min else "live"
    return RvolResult(value, state)


# --- profile store (I/O) ---


def profile_dir() -> Path:
    return Path(os.environ.get("VOLUME_PROFILE_DIR", "data/volume_profiles"))


def save_profile(profile: Profile) -> None:
    filepath = _profile_path(profile.symbol)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": profile.symbol,
        "cumfrac": profile.cumfrac,
        "median_daily_total": profile.median_daily_total,
        "n_days": profile.n_days,
    }
    filepath.write_text(json.dumps(payload))


def load_profile(symbol: str, *, max_age_days: float = 7.0) -> Profile | None:
    filepath = _profile_path(symbol)
    if not filepath.is_file():
        return None
    age_seconds = time.time() - os.path.getmtime(filepath)
    if age_seconds > max_age_days * 86400:
        return None
    data = json.loads(filepath.read_text())
    return Profile(
        symbol=data["symbol"],
        cumfrac=data["cumfrac"],
        median_daily_total=data["median_daily_total"],
        n_days=data["n_days"],
    )


async def build_profile(
    provider,
    symbol: str,
    *,
    days: int = 40,
    window: int = DEFAULT_WINDOW,
    session: str = "rth",
) -> Profile | None:
    try:
        bars = await provider.aget_stock_bars(symbol, timeframe="5Min", days=days)
    except BrokerError as exc:
        logger.warning("Failed to fetch bars for %s: %s", symbol, exc)
        return None
    prof = compute_profile(symbol, bars, window=window, session=session)
    if prof is not None:
        save_profile(prof)
    return prof
