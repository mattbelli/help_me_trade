from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from db.base import connect
from db.idea_outcomes import (
    get_summary,
    init_idea_outcomes_tables,
    list_ledger_rows,
    register_idea,
    update_outcome,
)


def _register(
    db_path: Path,
    *,
    origin: str,
    symbol: str,
    as_of: str,
    direction: str = "long",
) -> int:
    if direction == "short":
        entry_zone = {"low": "99", "high": "100"}
        stop = "101"
        targets = ["95"]
    else:
        entry_zone = {"low": "99", "high": "100"}
        stop = "95"
        targets = ["105"]
    idea_id = register_idea(
        origin=origin,
        signal_family="test",
        symbol=symbol,
        direction=direction,
        entry_zone=entry_zone,
        stop=stop,
        targets=targets,
        as_of=as_of,
        source_ref={"kind": "test", "id": symbol},
        db_path=db_path,
    )
    assert idea_id is not None
    return idea_id


def test_list_ledger_rows_orders_and_paginates(tmp_path: Path) -> None:
    db = tmp_path / "ledger.db"
    first = _register(
        db, origin="structure", symbol="AAPL", as_of="2026-07-17T14:00:00+00:00"
    )
    _register(
        db, origin="llm_conviction", symbol="MSFT", as_of="2026-07-17T15:00:00+00:00"
    )
    with connect(db) as conn:
        conn.execute(
            "UPDATE idea_outcomes SET updated_at = ? WHERE id = ?",
            ("2026-07-17T18:00:00+00:00", first),
        )
        conn.commit()

    result = list_ledger_rows(limit=10, offset=0, db_path=db)

    assert result["total"] == 2
    assert [row["symbol"] for row in result["rows"]] == ["MSFT", "AAPL"]
    assert result["rows"][0]["as_of"] == "2026-07-17T15:00:00+00:00"
    assert result["rows"][1]["updated_at"] == "2026-07-17T18:00:00+00:00"
    assert result["rows"][0]["targets_json"] == '["105"]'
    assert list_ledger_rows(limit=1, offset=1, db_path=db)["rows"][0]["symbol"] == "AAPL"


def test_list_ledger_rows_filters_origin_and_status(tmp_path: Path) -> None:
    db = tmp_path / "ledger.db"
    structure_id = _register(
        db, origin="structure", symbol="AAPL", as_of="2026-07-17T14:00:00+00:00"
    )
    _register(
        db,
        origin="llm_conviction",
        symbol="MSFT",
        as_of="2026-07-17T15:00:00+00:00",
        direction="short",
    )
    assert update_outcome(
        structure_id,
        status="won",
        ret_pct=4.25,
        spy_ret_pct=1.0,
        alpha_pct=3.25,
        db_path=db,
    )

    resolved = list_ledger_rows(status="resolved", db_path=db)
    llm = list_ledger_rows(origin="llm_conviction", status="open", db_path=db)

    assert resolved["total"] == 1
    assert resolved["rows"][0]["ret_pct"] == 4.25
    assert resolved["rows"][0]["alpha_pct"] == 3.25
    assert llm["total"] == 1
    assert llm["rows"][0]["symbol"] == "MSFT"


def test_list_ledger_rows_includes_journal_origins(tmp_path: Path) -> None:
    db = tmp_path / "ledger.db"
    _register(
        db, origin="fusion_mech", symbol="NVDA", as_of="2026-07-17T16:00:00+00:00"
    )
    _register(
        db,
        origin="structure_vetoed",
        symbol="TSLA",
        as_of="2026-07-17T17:00:00+00:00",
    )

    result = list_ledger_rows(db_path=db)

    assert result["total"] == 2
    assert {row["origin"] for row in result["rows"]} == {
        "fusion_mech",
        "structure_vetoed",
    }


def test_lifecycle_state_column_present(tmp_path: Path) -> None:
    db = tmp_path / "ledger.db"
    init_idea_outcomes_tables(db)
    with connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(idea_outcomes)")}
    assert {"lifecycle_state", "armed_at", "trigger_json", "trigger_expires_at"} <= cols
    # The partial armed index is present.
    with connect(db) as conn:
        idx = {r[1] for r in conn.execute("PRAGMA index_list(idea_outcomes)")}
    assert "idx_idea_armed" in idx


def test_register_idea_lifecycle_state_born_only(tmp_path: Path) -> None:
    """Registration can mint `born` (the reason-lane dossier path, §3) and ONLY
    born — later states are sweep/ARM transitions, never registration inputs.
    Default (every non-arming lane) stays NULL."""
    db = tmp_path / "ledger.db"
    plain = _register(db, origin="setup_scan", symbol="AAPL", as_of="2026-07-17T14:00:00+00:00")
    born = register_idea(
        origin="reason_v1",
        signal_family="fusion",
        symbol="MSFT",
        entry_zone={"low": "99", "high": "100"},
        stop="95",
        targets=["105"],
        as_of="2026-07-17T15:00:00+00:00",
        lifecycle_state="born",
        db_path=db,
    )
    assert born is not None
    rejected = register_idea(
        origin="reason_v1",
        signal_family="fusion",
        symbol="NVDA",
        entry_zone={"low": "99", "high": "100"},
        stop="95",
        targets=["105"],
        as_of="2026-07-17T15:00:00+00:00",
        lifecycle_state="armed",
        db_path=db,
    )
    assert rejected is None
    with connect(db) as conn:
        states = {
            row["id"]: row["lifecycle_state"]
            for row in conn.execute("SELECT id, lifecycle_state FROM idea_outcomes")
        }
    assert states[plain] is None
    assert states[born] == "born"


def test_counterfactual_expired_row_leaves_headline_unchanged(tmp_path: Path) -> None:
    """A counterfactual-expired arming row (fill_status='counterfactual') is a
    graded 'expired' row in the ONE ledger; it must not move the headline
    win-rate (which counts only won/lost) nor pollute the journal filters."""
    db = tmp_path / "ledger.db"
    won = _register(db, origin="setup_scan", symbol="AAPL", as_of="2026-07-17T14:00:00+00:00")
    assert update_outcome(won, status="won", ret_pct=3.0, db_path=db)

    baseline = get_summary(db_path=db)
    assert baseline["win_rate"] == 1.0

    armed = _register(db, origin="setup_scan", symbol="MSFT", as_of="2026-07-17T15:00:00+00:00")
    assert update_outcome(
        armed,
        status="expired",
        lifecycle_state="expired",
        exit_reason="trigger_ttl",
        fill_status="counterfactual",
        ret_pct=-2.0,
        spy_ret_pct=1.0,
        alpha_pct=-3.0,
        db_path=db,
    )

    after = get_summary(db_path=db)
    # Win-rate unchanged (expired is neither won nor lost); resolved count grows.
    assert after["win_rate"] == 1.0
    assert after["counts"].get("expired") == 1
    assert after["resolved"] == baseline["resolved"] + 1


def test_idea_ledger_route_shape(monkeypatch) -> None:
    expected = {
        "rows": [
            {
                "id": 7,
                "as_of": "2026-07-17T16:00:00+00:00",
                "updated_at": "2026-07-17T16:05:00+00:00",
                "symbol": "NVDA",
            }
        ],
        "total": 1,
    }
    monkeypatch.setattr("db.idea_outcomes.list_ledger_rows", lambda **_kwargs: expected)
    from api.main import app

    response = TestClient(app, base_url="http://127.0.0.1").get(
        "/api/ideas/ledger",
        params={"origin": "fusion_mech", "status": "open", "limit": 25, "offset": 0},
    )

    assert response.status_code == 200
    assert response.json() == expected
    assert set(response.json()) == {"rows", "total"}


def test_init_idea_outcomes_tables_reruns_after_file_recreated(tmp_path) -> None:
    """The schema check is memoised per (path, inode) — a DB deleted and recreated
    at the SAME path (fixture teardown) must get its tables again, not a stale
    'already initialised' skip."""
    from db.idea_outcomes import init_idea_outcomes_tables

    db = tmp_path / "ideas.db"
    init_idea_outcomes_tables(db)
    init_idea_outcomes_tables(db)  # memo hit
    db.unlink()
    init_idea_outcomes_tables(db)  # new inode → DDL runs again
    with connect(db) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'idea_outcomes'"
        ).fetchone()


def test_register_idea_survives_lock_burst(tmp_path: Path, monkeypatch) -> None:
    """Card #223: a 'database is locked' on the first write attempt must NOT drop
    the idea — the write retries and the row lands. Non-lock errors still raise."""
    import sqlite3

    import db.idea_outcomes as mod

    db_path = tmp_path / "ledger.db"
    init_idea_outcomes_tables(db_path)
    monkeypatch.setattr(mod, "_LOCK_RETRY_SLEEPS", (0.0, 0.0))
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    real_connect = mod._connect
    calls = {"n": 0}

    def flaky_connect(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_connect(path)

    monkeypatch.setattr(mod, "_connect", flaky_connect)
    idea_id = _register(db_path, origin="canary", symbol="SPY", as_of="2026-07-23T15:11:00+00:00")
    assert idea_id is not None
    assert calls["n"] == 2
    rows = list_ledger_rows(db_path=db_path)["rows"]
    assert [r["symbol"] for r in rows] == ["SPY"]

    # A non-lock OperationalError is not retried.
    def broken_connect(path):
        raise sqlite3.OperationalError("no such table: nope")

    monkeypatch.setattr(mod, "_connect", broken_connect)
    try:
        _register(db_path, origin="canary", symbol="QQQ", as_of="2026-07-23T15:12:00+00:00")
    except sqlite3.OperationalError as exc:
        assert "no such table" in str(exc)
    else:
        raise AssertionError("non-lock error should propagate")
