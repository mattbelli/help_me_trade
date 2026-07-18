from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from db.base import connect
from db.idea_outcomes import list_ledger_rows, register_idea, update_outcome


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
