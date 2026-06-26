"""Intraday volume profile and relative volume (RVOL) for a symbol."""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from brokers.alpaca import StockBar
from brokers.errors import BrokerError
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
) -> Profile | None:
    by_date: dict[datetime.date, list[float]] = {}

    for bar in bars:
        et = _bar_et(bar)
        idx = _bucket_index(et)
        if idx is None:
            continue
        d = et.date()
        if d not in by_date:
            by_date[d] = [0.0] * N_BUCKETS
        by_date[d][idx] += bar.volume

    qualifying: list[tuple[datetime.date, list[float], float]] = []
    for d, vector in by_date.items():
        total = sum(vector)
        if total <= 0:
            continue
        last_populated = max(i for i, v in enumerate(vector) if v > 0)
        if last_populated < 76:
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


def rvol_now(profile: Profile | None, today_bars: list[StockBar]) -> RvolResult | None:
    if profile is None or not today_bars:
        return None

    latest_date = max(_bar_et(b).date() for b in today_bars)
    session_bars = [
        b
        for b in today_bars
        if _bar_et(b).date() == latest_date and _bucket_index(_bar_et(b)) is not None
    ]
    if not session_bars:
        return None

    last_bucket = max(_bucket_index(_bar_et(b)) for b in session_bars)
    if last_bucket < (WARMUP_MIN // 5):
        return RvolResult(None, "warming")

    today_cum = sum(b.volume for b in session_bars)
    expected_cum = profile.median_daily_total * profile.cumfrac[last_bucket]
    if expected_cum <= 0:
        return None

    value = round(today_cum / expected_cum, 2)
    state = "settled" if last_bucket >= 77 else "live"
    return RvolResult(value, state)


# --- profile store (I/O) ---


def profile_dir() -> Path:
    return Path(os.environ.get("VOLUME_PROFILE_DIR", "data/volume_profiles"))


def save_profile(profile: Profile) -> None:
    directory = profile_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": profile.symbol,
        "cumfrac": profile.cumfrac,
        "median_daily_total": profile.median_daily_total,
        "n_days": profile.n_days,
    }
    (directory / f"{profile.symbol}.json").write_text(json.dumps(payload))


def load_profile(symbol: str, *, max_age_days: float = 7.0) -> Profile | None:
    filepath = profile_dir() / f"{symbol}.json"
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
) -> Profile | None:
    try:
        bars = await provider.aget_stock_bars(symbol, timeframe="5Min", days=days)
    except BrokerError as exc:
        logger.warning("Failed to fetch bars for %s: %s", symbol, exc)
        return None
    prof = compute_profile(symbol, bars, window=window)
    if prof is not None:
        save_profile(prof)
    return prof
