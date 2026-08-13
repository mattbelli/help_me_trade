"""Conviction score: rarity × tenor × OTM × type. Perception, not a signal."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db import options_flow as of_db
from db.flow_conviction import (
    UNRANKED_RARITY,
    attach,
    dte_kernel,
    otm_kernel,
    rarity_weight,
    score_event,
    type_gate,
)


def _ev(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "t1",
        "underlying": "PAAS",
        "event_type": "sweep",
        "right": "call",
        "strike": 60.0,
        "dte": 65,
        "otm_pct": 14.0,
        "premium_usd": 150_000.0,
        "rarity_p": 0.04,
        "direction": "undetermined",
        "detail": {"underlying_price": 52.6},
    }
    base.update(overrides)
    return base


def test_dte_kernel_peaks_near_two_months() -> None:
    peak = dte_kernel(55)
    nov = dte_kernel(100)
    short = dte_kernel(0)
    weekly = dte_kernel(7)
    leap = dte_kernel(180)
    assert peak > nov > 0.5
    assert peak > dte_kernel(37) * 0.9  # September still high
    assert short < 0.30
    assert weekly < 0.35
    assert short < nov
    assert leap < nov
    assert dte_kernel(None) == 0.4


def test_otm_kernel_peaks_near_12pct() -> None:
    peak = otm_kernel(12.0)
    atm = otm_kernel(0.0)
    deep_itm = otm_kernel(-25.0)
    far = otm_kernel(40.0)
    assert peak > otm_kernel(8.0) * 0.95
    assert peak > otm_kernel(20.0)
    assert atm < peak
    assert deep_itm < atm
    assert far < otm_kernel(20.0)
    assert otm_kernel(None) == 0.5


def test_type_gate_clusters_almost_cannot_win() -> None:
    assert type_gate("sweep") == 1.0
    assert type_gate("split") == 1.0
    assert type_gate("block") == 0.85
    assert type_gate("flow_cluster") == 0.08
    assert type_gate("flow_burst") == 0.08
    assert type_gate("directional_pressure") == 0.08
    assert type_gate("unusual_volume") == 0.08


def test_unranked_is_conservative_not_fake_rare() -> None:
    w, ranked = rarity_weight(None)
    assert ranked is False
    assert w == UNRANKED_RARITY
    w2, ranked2 = rarity_weight(0.04)
    assert ranked2 is True
    assert w2 == 0.96


def test_cluster_cannot_outrank_mid_name_60d_otm() -> None:
    """Even a 'rare' cluster loses to a shaped equity sweep — type_gate."""
    sweep, _ = score_event(_ev())
    cluster, parts = score_event(
        _ev(
            id="c1",
            underlying="SOXL",
            event_type="flow_cluster",
            dte=55,
            otm_pct=12.0,
            premium_usd=2_300_000.0,
            rarity_p=0.01,  # fake-rare on purpose
        )
    )
    assert sweep > cluster
    assert parts["type"] == 0.08
    assert cluster < 1.0
    assert sweep > 6.0


def test_no_known_side_bonus() -> None:
    a, _ = score_event(_ev(direction="buy_side"))
    b, _ = score_event(_ev(direction="undetermined"))
    c, _ = score_event(_ev(direction="sell_side"))
    assert a == b == c


def test_no_short_dte_bonus() -> None:
    long_, _ = score_event(_ev(dte=55, otm_pct=12.0))
    zero, _ = score_event(_ev(dte=0, otm_pct=12.0))
    assert long_ > zero * 2


def test_attach_fills_otm_and_stamps_score() -> None:
    ev = _ev(otm_pct=None)
    attach(ev)
    assert ev["otm_pct"] is not None
    assert ev["otm_pct"] > 0
    assert ev["conviction"] > 0
    assert ev["conviction_parts"]["ranked"] is True
    blob = str(ev["conviction_parts"]).lower()
    for banned in ("win", "smart money", "signal", "will move"):
        assert banned not in blob


def test_attach_never_opens_corpus(monkeypatch) -> None:
    import sqlite3

    real = sqlite3.connect

    def wrapped(database, *args, **kwargs):
        uri = str(database)
        assert "flow_corpus" not in uri, uri
        return real(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", wrapped)
    attach(_ev())


def test_order_conviction_ranks_shaped_sweep_over_cluster(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    of_db.init_db(db)
    of_db.insert_event(
        {
            "id": "cluster",
            "ts": "2026-08-12T18:00:00Z",
            "underlying": "SOXL",
            "occ_symbol": "SOXL260918C00045000",
            "right": "call",
            "strike": 45.0,
            "expiry": "2026-09-18",
            "dte": 37,
            "otm_pct": 5.0,
            "event_type": "flow_cluster",
            "premium_usd": 2_300_000.0,
            "direction": "buy_side",
            "n_prints": 193,
            "reason": "cluster",
            "caveat": "perception",
            "detail": {"underlying_price": 42.8},
        },
        db_path=db,
    )
    of_db.insert_event(
        {
            "id": "paas",
            "ts": "2026-08-12T17:00:00Z",
            "underlying": "PAAS",
            "occ_symbol": "PAAS261016C00060000",
            "right": "call",
            "strike": 60.0,
            "expiry": "2026-10-16",
            "dte": 65,
            "otm_pct": 14.0,
            "event_type": "sweep",
            "premium_usd": 150_000.0,
            "direction": "undetermined",
            "n_prints": 8,
            "reason": "sweep",
            "caveat": "perception",
            "rarity_p": 0.04,
            "rarity_n": 120,
            "rarity_cell": "C/31-90/otm10p",
            "detail": {"underlying_price": 52.6},
        },
        db_path=db,
    )
    recent = of_db.recent_events(db_path=db)
    assert [r["id"] for r in recent] == ["cluster", "paas"]  # newest first
    assert "conviction" in recent[0]
    ranked = of_db.recent_events(order="conviction", db_path=db)
    assert [r["id"] for r in ranked] == ["paas", "cluster"]
    assert "unusual_score" not in ranked[0]
    assert ranked[0]["conviction"] > ranked[1]["conviction"]


def test_perception_order_conviction(tmp_path: Path) -> None:
    from ai.perception.resources import RESOURCES

    db = tmp_path / "flow.db"
    of_db.init_db(db)
    of_db.insert_event(
        {
            "id": "uv",
            "ts": "2026-08-12T18:01:00Z",
            "underlying": "NVDA",
            "occ_symbol": "NVDA260821C00190000",
            "right": "call",
            "strike": 190.0,
            "expiry": "2026-08-21",
            "dte": 9,
            "otm_pct": 2.0,
            "event_type": "unusual_volume",
            "premium_usd": 8_000_000.0,
            "direction": "undetermined",
            "reason": "eod",
            "caveat": "perception",
        },
        db_path=db,
    )
    of_db.insert_event(
        {
            "id": "orcl",
            "ts": "2026-08-12T17:30:00Z",
            "underlying": "ORCL",
            "occ_symbol": "ORCL261120C00165000",
            "right": "call",
            "strike": 165.0,
            "expiry": "2026-11-20",
            "dte": 100,
            "otm_pct": 9.0,
            "event_type": "sweep",
            "premium_usd": 5_020_000.0,
            "direction": "buy_side",
            "reason": "sweep",
            "caveat": "perception",
            "rarity_p": 0.08,
            "detail": {"underlying_price": 151.4},
        },
        db_path=db,
    )
    read = RESOURCES["options_flow"]["read"]
    rows = read({"order": "conviction"}, db_path=db)
    # unusual_volume is dropped from the conviction scan unless asked for
    assert [r["id"] for r in rows] == ["orcl"]
    desc = RESOURCES["options_flow"]["description"].lower()
    assert "conviction" in desc
    assert "not a forecast" in RESOURCES["options_flow"]["filters"]["order"].lower()
    assert "will move" not in desc
    assert "smart money" not in desc
