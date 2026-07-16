"""Deterministic UV-1 / UV-2 detection (design §5) — pure, no I/O.

Returns §6 event dicts or None. No network, no DB, no clock reads except
injected ``event_date``. Direction at EOD is always ``undetermined``.
``reason`` / ``caveat`` are fixed templates — the LLM narrates from them.
"""

from __future__ import annotations

from typing import Any

from signals.options_flow.constants import (
    INDEX_MEGACAP_SET,
    MIN_CONTRACTS,
    MIN_OI,
    MIN_PREMIUM_INDEX_USD,
    MIN_PREMIUM_USD,
    MIN_PRICE,
    VOL_OI_RATIO_MIN,
    VOL_ZSCORE_MIN,
    ZERO_DTE_EXCLUDE,
    ZSCORE_STD_FLOOR,
)

_CAVEAT = (
    "volume/OI is an opening-activity proxy (OI lags 1-3 days); cannot distinguish "
    "new position vs close vs hedge vs spread leg; no direction at EOD"
)

_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _premium_floor(underlying: str) -> float:
    sym = str(underlying).strip().upper()
    if sym in INDEX_MEGACAP_SET:
        return float(MIN_PREMIUM_INDEX_USD)
    return float(MIN_PREMIUM_USD)


def _caveat() -> str:
    return _CAVEAT


def _fmt_premium(premium: float) -> str:
    if premium >= 1_000_000:
        return f"${premium / 1_000_000:.1f}M"
    if premium >= 1_000:
        return f"${premium / 1_000:.1f}k"
    return f"${premium:.0f}"


def _fmt_strike(strike: float) -> str:
    if float(strike) == int(strike):
        return f"${int(strike)}"
    return f"${strike:g}"


def _expiry_month(expiry: str) -> str:
    # YYYY-MM-DD → mon abbreviation
    try:
        month = int(str(expiry)[5:7])
        if 1 <= month <= 12:
            return _MONTHS[month - 1]
    except (TypeError, ValueError, IndexError):
        pass
    return "???"


def _right_letter(right: str) -> str:
    r = str(right or "").strip().lower()
    if r.startswith("c"):
        return "C"
    if r.startswith("p"):
        return "P"
    return "?"


def _reason_uv1(ev: dict[str, Any]) -> str:
    """Deterministic UV-1 reason template (design §6 example shape)."""
    underlying = ev.get("underlying") or "?"
    month = _expiry_month(str(ev.get("expiry") or ""))
    strike = _fmt_strike(float(ev.get("strike") or 0))
    letter = _right_letter(str(ev.get("right") or ""))
    contracts = int(ev.get("contracts") or 0)
    ratio = float(ev.get("vol_oi_ratio") or 0)
    premium = float(ev.get("premium_usd") or 0)
    return (
        f"{underlying} {month} {strike}{letter}: {contracts:,} contracts, "
        f"{ratio:.1f}x OI, {_fmt_premium(premium)} premium (EOD)"
    )


def _reason_uv2(underlying: str, dte_bucket: str, z: float) -> str:
    return (
        f"{underlying} {dte_bucket}DTE options: today's volume "
        f"{z:.1f}σ above 20-day avg (EOD)"
    )


def detect_uv1(
    *,
    underlying: str,
    occ_symbol: str,
    right: str,
    strike: float,
    expiry: str,
    dte: int,
    otm_pct: float | None,
    volume: int,
    oi: int | None,
    close: float,
    event_date: str,
    seq: int = 0,
) -> dict | None:
    """UV-1 vol/OI rule (design §5). Returns a §6 event dict or None."""
    del seq  # reserved for future multi-event same-day ids; id is contract-day stable
    sym = str(underlying).strip().upper()
    occ = str(occ_symbol).strip().upper()

    if ZERO_DTE_EXCLUDE and sym in INDEX_MEGACAP_SET and int(dte) <= 1:
        return None

    oi_val = 0 if oi is None else int(oi)
    vol = int(volume)
    px = float(close)
    premium = float(vol) * px * 100.0
    ratio = vol / max(oi_val, 1)

    if ratio < VOL_OI_RATIO_MIN:
        return None
    if vol < MIN_CONTRACTS:
        return None
    if oi_val < MIN_OI:
        return None
    if px < MIN_PRICE:
        return None
    if premium < _premium_floor(sym):
        return None

    event: dict[str, Any] = {
        "id": f"of_{event_date}_{sym}_{occ}",
        "ts": f"{event_date}T21:00:00Z",
        "underlying": sym,
        "occ_symbol": occ,
        "right": right,
        "strike": float(strike),
        "expiry": expiry,
        "dte": int(dte),
        "otm_pct": otm_pct,
        "event_type": "unusual_volume",
        "contracts": vol,
        "premium_usd": premium,
        "vol_oi_ratio": round(ratio, 2),
        "vol_zscore": None,
        "direction": "undetermined",
        "direction_basis": "none",
        "spread_flag": None,
        "n_prints": None,
        "exchanges": [],
        "reason": "",
        "caveat": _caveat(),
    }
    event["reason"] = _reason_uv1(event)
    return event


def detect_uv2(
    *,
    underlying: str,
    dte_bucket: str,
    today_vol: float,
    baseline: dict,
    event_date: str,
) -> dict | None:
    """UV-2 volume z-score rule (design §5). Underlying-level §6 event or None."""
    sym = str(underlying).strip().upper()
    n = int(baseline.get("n") or 0)
    std = max(float(baseline.get("std") or 0.0), float(ZSCORE_STD_FLOOR))
    if n < 10 or std <= 0:
        return None

    mean = float(baseline.get("mean") or 0.0)
    z = (float(today_vol) - mean) / std
    if z < VOL_ZSCORE_MIN:
        return None

    z_rounded = round(z, 2)
    return {
        "id": f"of_{event_date}_{sym}_bucket_{dte_bucket}",
        "ts": f"{event_date}T21:00:00Z",
        "underlying": sym,
        "occ_symbol": None,
        "right": None,
        "strike": None,
        "expiry": None,
        "dte": None,
        "otm_pct": None,
        "event_type": "unusual_volume",
        "contracts": int(today_vol),
        "premium_usd": None,
        "vol_oi_ratio": None,
        "vol_zscore": z_rounded,
        "direction": "undetermined",
        "direction_basis": "none",
        "spread_flag": None,
        "n_prints": None,
        "exchanges": [],
        "reason": _reason_uv2(sym, dte_bucket, z_rounded),
        "caveat": _caveat(),
    }
