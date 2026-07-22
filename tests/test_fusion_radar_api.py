from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from db.base import connect
from db.fusion import init_fusion_tables
from db.options_flow import init_db as init_options

MARKET_DAY = "2026-07-22"


def _insert_episode(db_path: Path, episode_id: str, symbol: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO fusion_episodes(id, symbol, market_day, opened_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (episode_id, symbol, MARKET_DAY, f"{MARKET_DAY}T14:00:00+00:00", f"{MARKET_DAY}T14:00:00+00:00"),
        )
        conn.commit()


def _insert_dot(
    db_path: Path,
    dot_id: str,
    episode_id: str,
    symbol: str,
    *,
    direction: str,
    raw_value: str,
    ts: str,
) -> None:
    # stamped=1 explicitly: score_decayed drops unstamped dots, and the column
    # default is 0. No DTE anywhere → options route to THESIS (absent DTE).
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO evidence_dots(
                id, episode_id, source_store, source_event_id, source_ingest_seq,
                symbol, ts, component, actor_group, dot_kind, direction,
                raw_value, points, detail_json, stamped, created_at
            ) VALUES (
                ?, ?, 'options_flow', ?, 1, ?, ?, 'option_block', 'options_actor',
                'event', ?, ?, 0, '{}', 1, ?
            )
            """,
            (dot_id, episode_id, f"ev-{dot_id}", symbol, ts, direction, raw_value, ts),
        )
        conn.commit()


def test_radar_sorts_by_thesis_z_and_flags_contested(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "fusion.db"
    init_fusion_tables(db_path)
    init_options(db_path)
    # ts ≈ now so thesis mass barely decays by the time the endpoint scores it.
    now_ts = datetime.now(timezone.utc).isoformat()

    # AAA: one-sided bull, point 2.0 → z = 2/√(2+prior) = 1.0, conviction > 0.
    _insert_episode(db_path, "ep-aaa", "AAA")
    _insert_dot(db_path, "aaa1", "ep-aaa", "AAA", direction="bull", raw_value="1000000", ts=now_ts)

    # BBB: balanced two-sided, each point 1.0 → net 0 → z 0, conviction 0, contested.
    _insert_episode(db_path, "ep-bbb", "BBB")
    _insert_dot(db_path, "bbb1", "ep-bbb", "BBB", direction="bull", raw_value="500000", ts=now_ts)
    _insert_dot(db_path, "bbb2", "ep-bbb", "BBB", direction="bear", raw_value="500000", ts=now_ts)

    monkeypatch.setattr("api.main.DEFAULT_DB_PATH", db_path)
    from api.main import app

    response = TestClient(app, base_url="http://127.0.0.1").get("/api/fusion/radar")
    assert response.status_code == 200
    body = response.json()
    rows = body["rows"]
    assert body["sessions"] == 5
    assert len(rows) == 2

    # Sorted by thesis z desc: the one-sided name outranks the cancelled one.
    assert rows[0]["symbol"] == "AAA"
    assert rows[1]["symbol"] == "BBB"
    assert rows[0]["thesis"]["z"] > rows[1]["thesis"]["z"]

    # Expected field shape on the top row.
    top = rows[0]
    assert set(top) == {
        "symbol",
        "tier",
        "maturity",
        "thesis",
        "trigger",
        "last_evidence_at",
        "dot_count",
    }
    # No hot-lane rows in this fixture: tier derives from gross vs noise floor.
    assert top["tier"] in {"poll", "scan"}
    assert set(top["thesis"]) == {
        "bull_mass",
        "bear_mass",
        "net",
        "conviction",
        "z",
        "contested",
    }
    assert set(top["trigger"]) == {"bull_mass", "bear_mass"}
    assert top["dot_count"] == 1
    assert top["last_evidence_at"] == now_ts
    assert top["thesis"]["conviction"] > 0
    # One ~2.0-point bull dot: z = 2/√(2 [gross] + 2.0 [prior]) ≈ 1.0.
    assert top["thesis"]["z"] > 0.9
    assert top["thesis"]["net"] > 0

    # Cancellation-death is z's job to expose, not hide: BBB nets to zero.
    bbb_row = rows[1]
    assert bbb_row["thesis"]["z"] == 0.0
    assert bbb_row["thesis"]["net"] == 0.0

    # The two-sided symbol shows contested with low conviction.
    bbb = next(row for row in rows if row["symbol"] == "BBB")
    assert bbb["dot_count"] == 2
    assert bbb["thesis"]["contested"] is True
    assert bbb["thesis"]["conviction"] < 0.4


def test_radar_empty_db_returns_no_rows(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "fusion.db"
    init_fusion_tables(db_path)
    init_options(db_path)
    monkeypatch.setattr("api.main.DEFAULT_DB_PATH", db_path)
    from api.main import app

    response = TestClient(app, base_url="http://127.0.0.1").get("/api/fusion/radar?sessions=3")
    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == []
    assert body["sessions"] == 3
