"""Intraday RTH session volume profiles and prior-session reference levels."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from research.intraday_bars import IntradayBar
from research.volume_profile import build_volume_profile

_NY = ZoneInfo("America/New_York")
_RTH_OPEN = time(9, 30)
_RTH_CLOSE = time(16, 0)
_LVN_TOL_PCT = 0.10
_POOR_LOW_VOL_RATIO = 0.20


def to_ny(ts: str) -> datetime:
    """Parse ISO-UTC ``t`` and return a tz-aware America/New_York datetime."""
    text = ts.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    utc = datetime.fromisoformat(text)
    if utc.tzinfo is None:
        utc = utc.replace(tzinfo=ZoneInfo("UTC"))
    return utc.astimezone(_NY)


def is_rth(ts: str) -> bool:
    """Return True when NY local time is in ``[09:30, 16:00)``."""
    ny = to_ny(ts)
    local = ny.timetz().replace(tzinfo=None)
    return _RTH_OPEN <= local < _RTH_CLOSE


def segment_rth_sessions(
    bars: list[IntradayBar],
) -> dict[date, list[IntradayBar]]:
    """Group RTH bars by NY session date, each session sorted ascending by ``t``."""
    sessions: dict[date, list[IntradayBar]] = {}
    for bar in sorted(bars, key=lambda item: item["t"]):
        if not is_rth(bar["t"]):
            continue
        session_date = to_ny(bar["t"]).date()
        sessions.setdefault(session_date, []).append(bar)
    return sessions


# --- CME Globex (futures) sessions -------------------------------------------
# Index futures (/ES, /NQ, /RTY, /YM) trade ~23h: the session for date D runs
# 18:00 ET (D-1) -> 17:00 ET (D), with a daily maintenance break 17:00-18:00 ET
# that belongs to no session. The RTH helpers above would discard the overnight
# ~14.5h — wrong for futures, so futures consumers use these instead.

_GLOBEX_OPEN = time(18, 0)
_GLOBEX_CLOSE = time(17, 0)
GLOBEX_N_BUCKETS = 276  # 23h * 60 / 5min
_GLOBEX_OPEN_MIN = 18 * 60


def globex_session_date(et: datetime) -> date | None:
    """CME Globex session date for a tz-aware America/New_York datetime.

    Session D runs 18:00 ET (D-1) -> 17:00 ET (D). Returns None inside the daily
    17:00-18:00 ET maintenance break (those bars belong to no session).
    """
    local = et.timetz().replace(tzinfo=None)
    if _GLOBEX_CLOSE <= local < _GLOBEX_OPEN:
        return None
    if local >= _GLOBEX_OPEN:
        return (et + timedelta(days=1)).date()
    return et.date()


def globex_bucket_index(et: datetime) -> int | None:
    """5-min bucket [0, GLOBEX_N_BUCKETS) within the Globex session.

    Bucket 0 = 18:00 ET (session open). Returns None inside the 17:00-18:00 ET
    maintenance break. The futures analogue of volume_profile's RTH _bucket_index.
    """
    minutes_into = ((et.hour * 60 + et.minute) - _GLOBEX_OPEN_MIN) % (24 * 60)
    if minutes_into >= GLOBEX_N_BUCKETS * 5:
        return None
    return minutes_into // 5


def segment_globex_sessions(
    bars: list[IntradayBar],
) -> dict[date, list[IntradayBar]]:
    """Group futures bars by Globex session date, each session sorted ascending.

    The futures analogue of ``segment_rth_sessions`` — keeps the full ~23h
    session (overnight Globex included), dropping only the maintenance break.
    """
    sessions: dict[date, list[IntradayBar]] = {}
    for bar in sorted(bars, key=lambda item: item["t"]):
        session_date = globex_session_date(to_ny(bar["t"]))
        if session_date is None:
            continue
        sessions.setdefault(session_date, []).append(bar)
    return sessions


def session_profile(
    session_bars: list[IntradayBar],
    *,
    n_bins: int = 100,
    value_area_pct: float = 0.70,
) -> dict:
    """Volume profile for one RTH session plus OHLC summary."""
    if not session_bars:
        return _empty_session_profile(n_bins)

    ordered = sorted(session_bars, key=lambda item: item["t"])
    profile = build_volume_profile(
        ordered, n_bins=n_bins, value_area_pct=value_area_pct
    )
    profile["session_high"] = max(float(b["h"]) for b in ordered)
    profile["session_low"] = min(float(b["l"]) for b in ordered)
    profile["session_open"] = float(ordered[0]["o"])
    profile["session_close"] = float(ordered[-1]["c"])
    profile["total_volume"] = sum(float(b["v"]) for b in ordered)
    profile["n_bars"] = len(ordered)
    return profile


def prior_session_reference(
    sessions: dict[date, list[IntradayBar]],
    current_date: date,
    *,
    n_bins: int = 100,
    value_area_pct: float = 0.70,
) -> dict | None:
    """Reference levels from the most recent session strictly before ``current_date``."""
    prior_dates = [d for d in sessions if d < current_date]
    if not prior_dates:
        return None
    ref_date = max(prior_dates)
    ref = session_profile(
        sessions[ref_date], n_bins=n_bins, value_area_pct=value_area_pct
    )
    ref["ref_date"] = ref_date
    return ref


def reference_features(price: float, ref: dict | None) -> dict:
    """Point-in-time features: where ``price`` sits vs prior-session reference ``ref``."""
    sensible = _sensible_reference_features()
    if ref is None or not ref:
        return sensible

    poc = ref.get("poc_price")
    vah = ref.get("vah")
    val = ref.get("val")
    if poc is None or vah is None or val is None:
        return sensible

    dist_to_poc_pct = None
    if price != 0:
        dist_to_poc_pct = (price - poc) / price * 100.0

    below_value = price < val
    above_value = price > vah
    in_value_area = val <= price <= vah

    value_position = None
    span = vah - val
    if span > 0:
        value_position = float(np.clip((price - val) / span, 0.0, 1.0))

    lvn_prices: list[float] = ref.get("lvn_prices") or []
    at_lvn = _near_lvn(price, lvn_prices)

    session_low = ref.get("session_low")
    at_poor_low = False
    if session_low is not None and price <= session_low:
        at_poor_low = _is_thin_at_price(price, ref)

    return {
        "dist_to_poc_pct": dist_to_poc_pct,
        "below_value": below_value,
        "above_value": above_value,
        "in_value_area": in_value_area,
        "value_position": value_position,
        "at_lvn": at_lvn,
        "at_poor_low": at_poor_low,
    }


def _empty_session_profile(n_bins: int) -> dict:
    empty = build_volume_profile([], n_bins=n_bins)
    empty.update(
        {
            "session_high": None,
            "session_low": None,
            "session_open": None,
            "session_close": None,
            "total_volume": 0.0,
            "n_bars": 0,
        }
    )
    return empty


def _sensible_reference_features() -> dict:
    return {
        "dist_to_poc_pct": None,
        "below_value": False,
        "above_value": False,
        "in_value_area": False,
        "value_position": None,
        "at_lvn": False,
        "at_poor_low": False,
    }


def _near_lvn(price: float, lvn_prices: list[float]) -> bool:
    if price == 0:
        return False
    for node in lvn_prices:
        if abs(price - node) / price * 100.0 <= _LVN_TOL_PCT:
            return True
    return False


def _is_thin_at_price(price: float, ref: dict) -> bool:
    bin_edges = ref.get("bin_edges")
    bin_volume = ref.get("bin_volume")
    if bin_edges is None or bin_volume is None:
        return False
    bin_volume = np.asarray(bin_volume, dtype=float)
    if bin_volume.size == 0:
        return False
    poc_bin_volume = float(bin_volume[int(np.argmax(bin_volume))])
    if poc_bin_volume <= 0:
        return False
    at_price = _volume_at_price(price, bin_edges, bin_volume)
    return at_price < _POOR_LOW_VOL_RATIO * poc_bin_volume


def _volume_at_price(
    price: float,
    bin_edges: np.ndarray,
    bin_volume: np.ndarray,
) -> float:
    if price < float(bin_edges[0]) or price > float(bin_edges[-1]):
        return 0.0
    bin_idx = int(np.searchsorted(bin_edges, price, side="right") - 1)
    bin_idx = int(np.clip(bin_idx, 0, len(bin_volume) - 1))
    return float(bin_volume[bin_idx])
