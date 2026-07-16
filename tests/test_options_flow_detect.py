"""Hermetic tests for UV-1/UV-2 detection + options_flow perception resource."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ai.perception.resources import _RESOURCE_ALIASES, RESOURCES, query
from db import options_flow as of_db
from signals.options_flow import constants as C
from signals.options_flow.detect import (
    _caveat,
    _premium_floor,
    detect_uv1,
    detect_uv2,
)

# --- helpers ---------------------------------------------------------------------


def _uv1_kwargs(**overrides: Any) -> dict[str, Any]:
    """Contract that clears every UV-1 gate (single-name premium floor)."""
    base: dict[str, Any] = {
        "underlying": "NVDA",
        "occ_symbol": "NVDA260821C00190000",
        "right": "call",
        "strike": 190.0,
        "expiry": "2026-08-21",
        "dte": 43,
        "otm_pct": 8.2,
        "volume": 4100,
        "oi": 670,
        "close": 3.5,  # premium = 4100 * 3.5 * 100 = 1_435_000
        "event_date": "2026-07-09",
        "seq": 0,
    }
    base.update(overrides)
    return base


def _healthy_baseline(
    *, mean: float = 100_000.0, std: float = 10_000.0, n: int = 20
) -> dict:
    # std sits above ZSCORE_STD_FLOOR so these fixtures exercise the raw z math.
    return {"mean": mean, "std": std, "n": n}


# --- UV-1 ------------------------------------------------------------------------


def test_premium_floor_index_vs_single_name() -> None:
    assert _premium_floor("NVDA") == C.MIN_PREMIUM_USD
    assert _premium_floor("spy") == C.MIN_PREMIUM_INDEX_USD
    assert _premium_floor("SPY") == C.MIN_PREMIUM_INDEX_USD
    assert _premium_floor("QQQ") == C.MIN_PREMIUM_INDEX_USD


def test_uv1_fires_at_thresholds() -> None:
    # Exactly at every gate: ratio 1.25, vol 500, oi 100, price 0.10, premium 25k
    # premium = 500 * 0.50 * 100 = 25_000; ratio = 500/400 = 1.25
    ev = detect_uv1(**_uv1_kwargs(volume=500, oi=400, close=0.50))
    assert ev is not None
    assert ev["event_type"] == "unusual_volume"
    assert ev["contracts"] == 500
    assert ev["vol_oi_ratio"] == 1.25
    assert ev["premium_usd"] == 25_000.0
    assert ev["direction"] == "undetermined"
    assert ev["direction_basis"] == "none"
    assert ev["vol_zscore"] is None
    assert ev["spread_flag"] is None
    assert ev["n_prints"] is None
    assert ev["exchanges"] == []
    assert ev["ts"] == "2026-07-09T21:00:00Z"
    assert ev["id"] == "of_2026-07-09_NVDA_NVDA260821C00190000"
    assert ev["reason"]
    assert ev["caveat"] == _caveat()
    assert "win_prob" not in ev
    assert "ev" not in ev
    assert "confidence" not in ev


def test_uv1_does_not_fire_when_any_gate_fails() -> None:
    # ratio too low: 500/500 = 1.0 < 1.25
    assert detect_uv1(**_uv1_kwargs(volume=500, oi=500, close=1.0)) is None
    # volume too low
    assert detect_uv1(**_uv1_kwargs(volume=499, oi=100, close=1.0)) is None
    # oi too low
    assert detect_uv1(**_uv1_kwargs(volume=500, oi=99, close=1.0)) is None
    # price too low
    assert detect_uv1(**_uv1_kwargs(volume=500, oi=100, close=0.09)) is None
    # premium too low for single name: 500 * 0.40 * 100 = 20_000 < 25_000
    # (ratio 500/100 = 5, vol/oi/price ok)
    assert detect_uv1(**_uv1_kwargs(volume=500, oi=100, close=0.40)) is None


def test_uv1_zero_dte_index_excluded() -> None:
    # Would otherwise clear gates if not excluded
    assert (
        detect_uv1(
            **_uv1_kwargs(
                underlying="SPY",
                occ_symbol="SPY260709C00500000",
                dte=1,
                volume=5000,
                oi=1000,
                close=2.0,  # premium 1M > 250k index floor
            )
        )
        is None
    )
    assert (
        detect_uv1(
            **_uv1_kwargs(
                underlying="SPY",
                occ_symbol="SPY260709C00500000",
                dte=0,
                volume=5000,
                oi=1000,
                close=2.0,
            )
        )
        is None
    )
    # Same contract with dte>1 on SPY should fire
    ev = detect_uv1(
        **_uv1_kwargs(
            underlying="SPY",
            occ_symbol="SPY260821C00500000",
            dte=43,
            volume=5000,
            oi=1000,
            close=2.0,
        )
    )
    assert ev is not None


def test_uv1_oi_none_does_not_fire() -> None:
    # oi=None → 0; fails MIN_OI even though vol/max(oi,1) is huge
    assert detect_uv1(**_uv1_kwargs(oi=None, volume=5000, close=5.0)) is None


def test_uv1_premium_floor_spy_vs_single() -> None:
    # premium = 500 * 1.0 * 100 = 50_000 — clears single-name floor, not index
    nvda = detect_uv1(**_uv1_kwargs(volume=500, oi=100, close=1.0))
    assert nvda is not None
    spy = detect_uv1(
        **_uv1_kwargs(
            underlying="SPY",
            occ_symbol="SPY260821C00500000",
            volume=500,
            oi=100,
            close=1.0,
            dte=43,
        )
    )
    assert spy is None
    spy_ok = detect_uv1(
        **_uv1_kwargs(
            underlying="SPY",
            occ_symbol="SPY260821C00500000",
            volume=5000,
            oi=1000,
            close=1.0,  # premium 500_000 >= 250_000
            dte=43,
        )
    )
    assert spy_ok is not None


def test_uv1_id_stable_for_contract_day() -> None:
    a = detect_uv1(**_uv1_kwargs(seq=0))
    b = detect_uv1(**_uv1_kwargs(seq=7))
    assert a is not None and b is not None
    assert a["id"] == b["id"]
    assert a["id"] == "of_2026-07-09_NVDA_NVDA260821C00190000"


def test_uv1_reason_shape() -> None:
    ev = detect_uv1(**_uv1_kwargs())
    assert ev is not None
    # "NVDA Aug $190C: 4,100 contracts, 6.1x OI, $1.4M premium (EOD)"
    assert "NVDA" in ev["reason"]
    assert "Aug" in ev["reason"]
    assert "$190C" in ev["reason"]
    assert "4,100" in ev["reason"]
    assert "OI" in ev["reason"]
    assert "(EOD)" in ev["reason"]
    assert "no direction at EOD" in ev["caveat"]


# --- UV-2 ------------------------------------------------------------------------


def test_uv2_fires_on_z_ge_3() -> None:
    # z = (130000 - 100000) / 10000 = 3.0
    ev = detect_uv2(
        underlying="NVDA",
        dte_bucket="8-30",
        today_vol=130_000.0,
        baseline=_healthy_baseline(),
        event_date="2026-07-09",
    )
    assert ev is not None
    assert ev["event_type"] == "unusual_volume"
    assert ev["vol_zscore"] == 3.0
    assert ev["contracts"] == 130_000
    assert ev["vol_oi_ratio"] is None
    assert ev["occ_symbol"] is None
    assert ev["strike"] is None
    assert ev["expiry"] is None
    assert ev["right"] is None
    assert ev["direction"] == "undetermined"
    assert ev["direction_basis"] == "none"
    assert ev["id"] == "of_2026-07-09_NVDA_bucket_8-30"
    assert "8-30DTE options" in ev["reason"]
    assert "3.0σ" in ev["reason"]
    assert ev["caveat"] == _caveat()
    assert "win_prob" not in ev
    assert "ev" not in ev
    assert "confidence" not in ev


def test_uv2_none_when_n_lt_10_or_std_nonpositive() -> None:
    assert (
        detect_uv2(
            underlying="NVDA",
            dte_bucket="8-30",
            today_vol=10_000.0,
            baseline=_healthy_baseline(n=9),
            event_date="2026-07-09",
        )
        is None
    )
    # std=0 → floored to ZSCORE_STD_FLOOR: a modest absolute move must NOT fire
    # (the thin-name guard — z = 900 / floor ≈ 0.7)
    assert (
        detect_uv2(
            underlying="NVDA",
            dte_bucket="8-30",
            today_vol=1_000.0,
            baseline={"mean": 100.0, "std": 0.0, "n": 20},
            event_date="2026-07-09",
        )
        is None
    )
    # z just under threshold
    assert (
        detect_uv2(
            underlying="NVDA",
            dte_bucket="8-30",
            today_vol=129_900.0,  # z = 2.99
            baseline=_healthy_baseline(),
            event_date="2026-07-09",
        )
        is None
    )


def test_uv2_z_math_on_synthetic_baseline() -> None:
    # z = (450000 - 150000) / 50000 = 6.0
    ev = detect_uv2(
        underlying="AAPL",
        dte_bucket="31-90",
        today_vol=450_000.0,
        baseline={"mean": 150_000.0, "std": 50_000.0, "n": 20},
        event_date="2026-07-08",
    )
    assert ev is not None
    assert ev["vol_zscore"] == 6.0
    assert ev["id"] == "of_2026-07-08_AAPL_bucket_31-90"


# --- perception resource ---------------------------------------------------------


def _sample_event(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "of_2026-07-09_NVDA_NVDA260821C00190000",
        "ts": "2026-07-09T21:00:00Z",
        "underlying": "NVDA",
        "occ_symbol": "NVDA260821C00190000",
        "right": "call",
        "strike": 190.0,
        "expiry": "2026-08-21",
        "dte": 43,
        "otm_pct": 8.2,
        "event_type": "unusual_volume",
        "contracts": 4100,
        "premium_usd": 1_435_000.0,
        "vol_oi_ratio": 6.12,
        "vol_zscore": None,
        "direction": "undetermined",
        "direction_basis": "none",
        "spread_flag": None,
        "n_prints": None,
        "exchanges": [],
        "reason": "test reason",
        "caveat": _caveat(),
    }
    base.update(overrides)
    return base


def test_options_flow_resource_registered() -> None:
    assert "options_flow" in RESOURCES
    assert _RESOURCE_ALIASES.get("flow") == "options_flow"
    entry = RESOURCES["options_flow"]
    assert callable(entry["read"])
    assert callable(entry["summarize"])
    assert "caveat" in entry["description"].lower() or "NOT a buy" in entry["description"]


@pytest.mark.asyncio
async def test_options_flow_read_filters_and_alias(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    of_db.init_db(db)
    of_db.insert_event(
        _sample_event(
            id="e_new",
            underlying="NVDA",
            ts="2026-07-09T21:00:00Z",
            premium_usd=1_000_000.0,
        ),
        db_path=db,
    )
    of_db.insert_event(
        _sample_event(
            id="e_old",
            underlying="AAPL",
            occ_symbol="AAPL260821C00200000",
            ts="2026-07-08T21:00:00Z",
            premium_usd=50_000.0,
            event_type="block",
        ),
        db_path=db,
    )
    of_db.insert_event(
        _sample_event(
            id="e_mid",
            underlying="NVDA",
            occ_symbol="NVDA260821P00180000",
            ts="2026-07-09T20:00:00Z",
            premium_usd=30_000.0,
            spread_flag=True,
        ),
        db_path=db,
    )

    read = RESOURCES["options_flow"]["read"]
    rows = read({}, db_path=db)
    assert [r["id"] for r in rows] == ["e_new", "e_mid", "e_old"]  # newest first

    by_sym = read({"symbol": "nvda"}, db_path=db)
    assert {r["id"] for r in by_sym} == {"e_new", "e_mid"}

    by_type = read({"event_type": "block"}, db_path=db)
    assert [r["id"] for r in by_type] == ["e_old"]

    by_prem = read({"min_premium": 100_000}, db_path=db)
    assert [r["id"] for r in by_prem] == ["e_new"]

    since = read({"since": "2026-07-09T00:00:00Z"}, db_path=db)
    assert {r["id"] for r in since} == {"e_new", "e_mid"}

    # alias "flow" resolves via query()
    result = await query("flow", {"symbol": "AAPL"}, db_path=db)
    assert result.get("error") != "unknown resource"
    assert result["resource"] == "options_flow"
    assert result["count"] == 1
    assert result["rows"][0]["id"] == "e_old"


def test_options_flow_summarize_groups(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    of_db.init_db(db)
    of_db.insert_event(
        _sample_event(
            id="n1",
            underlying="NVDA",
            ts="2026-07-09T21:00:00Z",
            premium_usd=100_000.0,
            spread_flag=True,
        ),
        db_path=db,
    )
    of_db.insert_event(
        _sample_event(
            id="n2",
            underlying="NVDA",
            occ_symbol="NVDA260821P00180000",
            ts="2026-07-09T20:00:00Z",
            premium_usd=50_000.0,
            event_type="block",
        ),
        db_path=db,
    )
    of_db.insert_event(
        _sample_event(
            id="a1",
            underlying="AAPL",
            occ_symbol="AAPL260821C00200000",
            ts="2026-07-09T19:00:00Z",
            premium_usd=25_000.0,
        ),
        db_path=db,
    )

    summarize = RESOURCES["options_flow"]["summarize"]
    summary = summarize({"since": "2026-07-09T00:00:00Z"}, db_path=db)
    assert summary["n_underlyings"] == 2
    nvda = summary["by_underlying"]["NVDA"]
    assert nvda["count"] == 2
    assert nvda["total_premium_usd"] == 150_000.0
    assert set(nvda["event_types"]) == {"unusual_volume", "block"}
    assert nvda["has_spread_dominated"] is True
    aapl = summary["by_underlying"]["AAPL"]
    assert aapl["count"] == 1
    assert aapl["total_premium_usd"] == 25_000.0
    assert aapl["has_spread_dominated"] is False
