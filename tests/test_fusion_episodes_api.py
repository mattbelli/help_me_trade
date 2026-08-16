from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from db.base import connect
from db.fusion import init_fusion_tables, list_recent_episodes


def _insert_episode(
    db_path: Path,
    episode_id: str,
    *,
    symbol: str,
    market_day: str,
    score: float,
    updated_at: str,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO fusion_episodes(
                id, symbol, market_day, opened_at, updated_at, anchor_direction,
                score, direction_state, signature
            ) VALUES (?, ?, ?, ?, ?, 'bull', ?, 'aligned(bull)', 'option_sweep')
            """,
            (episode_id, symbol, market_day, updated_at, updated_at, score),
        )
        conn.commit()


def _insert_dot(db_path: Path, episode_id: str, dot_number: int = 1) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO evidence_dots(
                id, episode_id, source_store, source_event_id, source_ingest_seq,
                symbol, ts, component, actor_group, dot_kind, direction,
                raw_value, points, detail_json, created_at
            ) VALUES (
                ?, ?, 'options_flow', ?, ?, 'AAPL',
                '2026-07-18T14:30:00+00:00', 'option_sweep', 'options_actor',
                'event', 'bull', '1200000', 2.5, '{"kind":"sweep"}',
                '2026-07-18T14:30:01+00:00'
            )
            """,
            (
                f"dot-{episode_id}-{dot_number}",
                episode_id,
                f"event-{episode_id}-{dot_number}",
                dot_number,
            ),
        )
        conn.commit()


def test_list_recent_episodes_resolves_latest_day_and_orders_score(tmp_path: Path) -> None:
    db_path = tmp_path / "fusion.db"
    init_fusion_tables(db_path)
    _insert_episode(
        db_path,
        "old",
        symbol="MSFT",
        market_day="2026-07-17",
        score=9,
        updated_at="2026-07-17T20:00:00+00:00",
    )
    _insert_episode(
        db_path,
        "lower",
        symbol="TSLA",
        market_day="2026-07-18",
        score=2,
        updated_at="2026-07-18T15:00:00+00:00",
    )
    _insert_episode(
        db_path,
        "higher",
        symbol="AAPL",
        market_day="2026-07-18",
        score=4,
        updated_at="2026-07-18T14:00:00+00:00",
    )
    for dot_number in range(1, 4):
        _insert_dot(db_path, "higher", dot_number)

    rows = list_recent_episodes(db_path=db_path)

    assert [row["id"] for row in rows] == ["higher", "lower"]
    assert all(row["market_day"] == "2026-07-18" for row in rows)
    assert {row["id"]: row["dot_count"] for row in rows} == {
        "higher": 3,
        "lower": 0,
    }


def test_list_recent_episodes_accepts_day_and_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "fusion.db"
    init_fusion_tables(db_path)
    _insert_episode(
        db_path,
        "first",
        symbol="AAPL",
        market_day="2026-07-17",
        score=3,
        updated_at="2026-07-17T14:00:00+00:00",
    )
    _insert_episode(
        db_path,
        "second",
        symbol="MSFT",
        market_day="2026-07-17",
        score=3,
        updated_at="2026-07-17T15:00:00+00:00",
    )

    rows = list_recent_episodes(
        market_day="2026-07-17",
        limit=1,
        db_path=db_path,
    )

    assert [row["id"] for row in rows] == ["second"]


def test_fusion_episode_routes_return_list_and_detail_shapes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "fusion.db"
    init_fusion_tables(db_path)
    _insert_episode(
        db_path,
        "episode-1",
        symbol="AAPL",
        market_day="2026-07-18",
        score=4.5,
        updated_at="2026-07-18T14:30:00+00:00",
    )
    _insert_dot(db_path, "episode-1")
    monkeypatch.setattr("api.fusion_routes.DEFAULT_DB_PATH", db_path)
    from api.main import app

    client = TestClient(app, base_url="http://127.0.0.1")
    listing = client.get("/api/fusion/episodes")
    detail = client.get("/api/fusion/episodes/episode-1")

    assert listing.status_code == 200
    assert listing.json()["day"] == "2026-07-18"
    assert listing.json()["episodes"][0]["dot_count"] == 1
    assert detail.status_code == 200
    assert detail.json()["episode"]["id"] == "episode-1"
    assert detail.json()["dots"][0]["detail"] == {"kind": "sweep"}


def test_fusion_episode_detail_returns_404(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "fusion.db"
    init_fusion_tables(db_path)
    monkeypatch.setattr("api.fusion_routes.DEFAULT_DB_PATH", db_path)
    from api.main import app

    response = TestClient(app, base_url="http://127.0.0.1").get(
        "/api/fusion/episodes/missing"
    )

    assert response.status_code == 404


def _hermetic_dossier_defaults(monkeypatch, tmp_path: Path) -> None:
    """The builder's EDGAR/macro kwdefaults point at the checkout's real data/ —
    the route must read nothing this test did not seed (same guard as
    tests/test_dossier.py's autouse fixture)."""
    from signals.dossier import build_dossier

    absent = tmp_path / "no_such_data"
    for key in ("facts_db_path", "fundamentals_db_path", "macro_db_path"):
        monkeypatch.setitem(build_dossier.__kwdefaults__, key, absent / key)


def test_fusion_episode_dossier_route_builds_on_demand(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "fusion.db"
    init_fusion_tables(db_path)
    _insert_episode(
        db_path,
        "episode-1",
        symbol="AAPL",
        market_day="2026-07-18",
        score=4.5,
        updated_at="2026-07-18T14:30:00+00:00",
    )
    _insert_dot(db_path, "episode-1")
    monkeypatch.setattr("api.fusion_routes.DEFAULT_DB_PATH", db_path)
    _hermetic_dossier_defaults(monkeypatch, tmp_path)
    from api.main import app

    response = TestClient(app, base_url="http://127.0.0.1").get(
        "/api/fusion/episodes/episode-1/dossier"
    )

    assert response.status_code == 200
    dossier = response.json()["dossier"]
    assert dossier["episode_id"] == "episode-1"
    assert dossier["symbol"] == "AAPL"
    assert any(claim["angle"] == "evidence" for claim in dossier["claims"])
    assert dossier["summary"]
    # On-demand build is ephemeral — the read path never writes a store.
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'idea_dossiers'"
        ).fetchone()
    assert row is None


def test_fusion_episode_dossier_route_404(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "fusion.db"
    init_fusion_tables(db_path)
    monkeypatch.setattr("api.fusion_routes.DEFAULT_DB_PATH", db_path)
    from api.main import app

    response = TestClient(app, base_url="http://127.0.0.1").get(
        "/api/fusion/episodes/missing/dossier"
    )

    assert response.status_code == 404
