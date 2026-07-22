"""Phase C1b — STRUCTURE state dots wired into the fusion evidence bus.

Structure is a STATE group (spec §2.1/§2.2): it attaches session-level / founder
price-alert context to an actor-anchored episode and NEVER creates one. These
tests pin the trigger rule, CONV_LEVEL_ATR proximity, latest/best refresh, the
unstamped skip, and the downstream C1 archetype (options_actor + structure).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from db.base import connect
from db.chart_features import init_chart_features
from db.fusion import init_fusion_tables, put_atr, put_session_levels
from db.lane_stats import day as lane_day
from db.lane_stats import et_today
from db.options_flow import insert_event as insert_flow
from signals.fusion.intake import intake_once


@pytest.fixture
def fusion_db(tmp_path: Path) -> Path:
    path = tmp_path / "fusion.db"
    init_fusion_tables(path)
    init_chart_features(path)
    return path


def _flow(
    event_id: str,
    *,
    symbol: str = "TSLA",
    premium: int = 1_000_000,
    ts: str = "2026-07-17T14:30:00+00:00",
) -> dict:
    return {
        "id": event_id,
        "ts": ts,
        "underlying": symbol,
        "occ_symbol": f"{symbol}260821C00300000",
        "right": "call",
        "event_type": "sweep",
        "premium_usd": premium,
        "direction": "buy_side",
        "created_at": ts,
    }


def _episode_id(db: Path, symbol: str) -> str:
    with connect(db) as conn:
        row = conn.execute(
            "SELECT id FROM fusion_episodes WHERE symbol = ?", (symbol,)
        ).fetchone()
    assert row is not None, f"no episode for {symbol}"
    return str(row["id"])


def _structure_dots(db: Path, episode_id: str, component: str | None = None) -> list[dict]:
    query = (
        "SELECT * FROM evidence_dots WHERE episode_id = ? AND actor_group = 'structure'"
    )
    params: list = [episode_id]
    if component is not None:
        query += " AND component = ?"
        params.append(component)
    with connect(db) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def _group_scores(db: Path, episode_id: str) -> dict:
    with connect(db) as conn:
        row = conn.execute(
            """
            SELECT score_json FROM fusion_score_history
            WHERE episode_id = ? ORDER BY id DESC LIMIT 1
            """,
            (episode_id,),
        ).fetchone()
    assert row is not None
    return json.loads(row["score_json"])["group_scores"]


def _insert_alert(db: Path, symbol: str, price: float, *, repeat: int = 0) -> None:
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO price_alerts(symbol, price, direction, note, triggered, repeat, created_at)
            VALUES (?, ?, 'cross', '', 0, ?, '2026-07-17T00:00:00Z')
            """,
            (symbol.strip().upper(), price, repeat),
        )
        conn.commit()


def _meter(db: Path, counter: str) -> float:
    return lane_day("fusion_intake", et_today(), db_path=db).get(counter, 0.0)


def test_actor_near_level_gains_structure_dot_and_positive_group_score(
    fusion_db: Path,
) -> None:
    put_atr("TSLA", "2026-07-16", Decimal("12.50"), db_path=fusion_db)
    put_session_levels("TSLA", "2026-07-17", [{"name": "PDH", "price": 320.5}], db_path=fusion_db)
    insert_flow(_flow("flow-1"), fusion_db)

    counters = intake_once(db_path=fusion_db, price_lookup=lambda _s: Decimal("320.25"))
    assert counters["scored"] == 1

    ep = _episode_id(fusion_db, "TSLA")
    dots = _structure_dots(fusion_db, ep, "session_level")
    assert len(dots) == 1
    dot = dots[0]
    assert dot["direction"] == "none"
    assert dot["dot_kind"] == "state"
    assert dot["points"] > 0

    groups = _group_scores(fusion_db, ep)
    assert "options_actor" in groups
    assert groups["structure"] > 0


def test_level_alone_creates_no_episode(fusion_db: Path) -> None:
    # A level and an alert sit on AAPL, but no actor event ever fires: the trigger
    # rule forbids structure from anchoring a stack, so no episode may exist.
    put_atr("AAPL", "2026-07-16", Decimal("5"), db_path=fusion_db)
    put_session_levels("AAPL", "2026-07-17", [{"name": "POC", "price": 150.0}], db_path=fusion_db)
    _insert_alert(fusion_db, "AAPL", 150.1)

    counters = intake_once(db_path=fusion_db, price_lookup=lambda _s: Decimal("150"))
    assert sum(counters.values()) == 0
    with connect(fusion_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fusion_episodes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM evidence_dots").fetchone()[0] == 0


def test_proximity_boundary_just_inside_vs_just_outside(fusion_db: Path) -> None:
    # ATR 10 → CONV_LEVEL_ATR(0.5)×10 = 5.0 threshold. Price 100 for both symbols.
    for symbol in ("INSD", "OUTS"):
        put_atr(symbol, "2026-07-16", Decimal("10"), db_path=fusion_db)
    put_session_levels("INSD", "2026-07-17", [{"name": "VAH", "price": 104.9}], db_path=fusion_db)
    put_session_levels("OUTS", "2026-07-17", [{"name": "VAH", "price": 105.1}], db_path=fusion_db)
    insert_flow(_flow("flow-in", symbol="INSD"), fusion_db)
    insert_flow(_flow("flow-out", symbol="OUTS"), fusion_db)

    intake_once(db_path=fusion_db, price_lookup=lambda _s: Decimal("100"))

    inside = _structure_dots(fusion_db, _episode_id(fusion_db, "INSD"), "session_level")
    outside = _structure_dots(fusion_db, _episode_id(fusion_db, "OUTS"), "session_level")
    assert len(inside) == 1  # dist 4.9 ≤ 5.0
    assert outside == []  # dist 5.1 > 5.0


def test_price_alert_attaches_as_structure(fusion_db: Path) -> None:
    put_atr("NVDA", "2026-07-16", Decimal("10"), db_path=fusion_db)
    _insert_alert(fusion_db, "NVDA", 500.2)  # dist 0.2 ≤ 5.0
    insert_flow(_flow("flow-nvda", symbol="NVDA"), fusion_db)

    intake_once(db_path=fusion_db, price_lookup=lambda _s: Decimal("500"))

    ep = _episode_id(fusion_db, "NVDA")
    alert_dots = _structure_dots(fusion_db, ep, "price_alert")
    assert len(alert_dots) == 1
    assert alert_dots[0]["direction"] == "none"
    # No session levels cached → no session_level dot, only the alert one.
    assert _structure_dots(fusion_db, ep, "session_level") == []
    assert _group_scores(fusion_db, ep)["structure"] > 0


def test_repeated_events_refresh_not_append(fusion_db: Path) -> None:
    put_atr("TSLA", "2026-07-16", Decimal("12.50"), db_path=fusion_db)
    put_session_levels("TSLA", "2026-07-17", [{"name": "PDH", "price": 320.5}], db_path=fusion_db)

    insert_flow(_flow("flow-1", premium=1_000_000), fusion_db)
    intake_once(db_path=fusion_db, price_lookup=lambda _s: Decimal("320.25"))
    insert_flow(_flow("flow-2", premium=1_500_000, ts="2026-07-17T14:40:00+00:00"), fusion_db)
    intake_once(db_path=fusion_db, price_lookup=lambda _s: Decimal("320.30"))

    ep = _episode_id(fusion_db, "TSLA")
    # One dot per (episode, component) — the second pass refreshed in place.
    assert len(_structure_dots(fusion_db, ep, "session_level")) == 1
    # ...but both attach passes counted (mint + refresh).
    assert _meter(fusion_db, "structure_dots") == 2.0


def test_unstamped_atr_skip_is_counted(fusion_db: Path) -> None:
    # Price is available but ATR is not cached → the actor dot is unstampable and
    # structure is skipped for the pass, counted (never raised), never minted.
    put_session_levels("TSLA", "2026-07-17", [{"name": "PDH", "price": 320.5}], db_path=fusion_db)
    insert_flow(_flow("flow-1"), fusion_db)

    counters = intake_once(db_path=fusion_db, price_lookup=lambda _s: Decimal("320.25"))
    assert counters["unstampable"] == 1

    ep = _episode_id(fusion_db, "TSLA")
    assert _structure_dots(fusion_db, ep) == []
    assert _meter(fusion_db, "structure_skipped_unstamped") == 1.0
    assert _meter(fusion_db, "structure_dots") == 0.0


def test_options_actor_plus_structure_triggers_c1_investigation(
    fusion_db: Path, monkeypatch
) -> None:
    # This test pins the intake→C1 PLUMBING (a live co-fire reaches the gate and
    # stamps a row). The real cut-line — the z dual gate at the founder's T,
    # where one print correctly does NOT clear — is pinned in
    # tests/test_fusion_investigate.py; here both gate legs are opened wide so a
    # single synthetic print exercises the wiring.
    import signals.fusion.investigate as investigate_mod

    monkeypatch.setattr(investigate_mod, "FUSION_Z_GATE_T", 0.0)
    monkeypatch.setattr(investigate_mod, "CONVICTION_BAR", 0.0)

    live_ts = "2026-07-20T18:00:00+00:00"  # → 14:00 ET 2026-07-20 (a Monday)
    put_atr("TSLA", "2026-07-17", Decimal("12.50"), db_path=fusion_db)
    put_session_levels("TSLA", "2026-07-20", [{"name": "PDH", "price": 320.5}], db_path=fusion_db)
    insert_flow(_flow("flow-live", premium=3_000_000, ts=live_ts), fusion_db)

    intake_once(db_path=fusion_db, price_lookup=lambda _s: Decimal("320.25"))

    ep = _episode_id(fusion_db, "TSLA")
    groups = _group_scores(fusion_db, ep)
    assert {"options_actor", "structure"} <= set(groups)
    with connect(fusion_db) as conn:
        row = conn.execute(
            "SELECT decision FROM fusion_investigations WHERE episode_id = ?", (ep,)
        ).fetchone()
    assert row is not None
    assert row["decision"] == "shadow_investigate"
