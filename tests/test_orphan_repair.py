"""orphan-repair-surface: mark_orphan_repaired flips status (bookkeeping only),
stays idempotent, and preserves the sweep-suppression invariant; the API route
lists + repairs behind the mutation-token guard; prune_earnings_alerts_sent
drops stale dedup keys.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db import earnings_alerts as ea
from db import fill_attribution as fa
from db.base import DEFAULT_DB_PATH, connect

# --- DB layer -----------------------------------------------------------------


def test_mark_orphan_repaired_transitions_and_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "fa.db"
    oid = fa.record_orphan(
        execution_id="exec1", reason="no_managed_row", symbol="AAA", db_path=db
    )
    assert oid

    assert fa.mark_orphan_repaired(oid, db_path=db) is True  # open -> repaired
    assert fa.mark_orphan_repaired(oid, db_path=db) is False  # already repaired
    assert fa.mark_orphan_repaired("orphan_nope", db_path=db) is False  # unknown

    # Gone from the open list, present as 'repaired' in the full list.
    assert fa.list_open_orphans(db_path=db) == []
    all_orphans = fa.list_orphans(db_path=db)
    assert len(all_orphans) == 1
    assert all_orphans[0]["status"] == "repaired"

    # Sweep-suppression invariant: a repaired row still counts as existing, so
    # the unmanaged sweep never re-quarantines the same execution.
    assert fa.orphan_exists_for_execution("exec1", db_path=db) is True


def test_mark_orphan_repaired_from_notified(tmp_path: Path) -> None:
    db = tmp_path / "fa.db"
    oid = fa.record_orphan(execution_id="exec2", reason="r", symbol="BBB", db_path=db)
    fa.mark_orphan_notified(oid, db_path=db)
    assert fa.list_orphans(status="notified", db_path=db)[0]["id"] == oid
    assert fa.mark_orphan_repaired(oid, db_path=db) is True
    assert fa.list_orphans(status="repaired", db_path=db)[0]["id"] == oid


def test_prune_earnings_alerts_sent(tmp_path: Path) -> None:
    db = tmp_path / "ea.db"
    ea.mark_sent("rvol:AAA:2026-01-01", db_path=db)  # created ~now → survives
    ea.init_earnings_alert_tables(db)
    old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    with connect(db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO earnings_alerts_sent (key, created_at) VALUES (?, ?)",
            ("rvol:OLD:2025-01-01", old),
        )
        conn.commit()

    assert ea.prune_earnings_alerts_sent(keep_days=90, db_path=db) == 1
    assert ea.was_sent("rvol:AAA:2026-01-01", db_path=db) is True
    assert ea.was_sent("rvol:OLD:2025-01-01", db_path=db) is False


# --- API layer (conftest freezes DEFAULT_DB_PATH to a throwaway temp DB) -------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    fa.init_fill_attribution_tables(DEFAULT_DB_PATH)
    with connect(DEFAULT_DB_PATH) as conn:
        conn.execute("DELETE FROM fill_attribution_orphans")
        conn.commit()
    token = "founder-secret"
    monkeypatch.setattr("api.auth.API_TOKEN", token)

    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers.update({"X-API-Token": token})
        yield c


def test_list_and_repair_flow(client) -> None:
    oid = fa.record_orphan(execution_id="e1", reason="r", symbol="AAA")

    listed = client.get("/api/fill-orphans").json()["orphans"]
    assert [o["id"] for o in listed] == [oid]
    assert listed[0]["status"] == "open"

    resp = client.post(f"/api/fill-orphans/{oid}/repair")
    assert resp.status_code == 200
    assert resp.json()["status"] == "repaired"

    # Idempotent: repairing again is a 404 (nothing to transition).
    assert client.post(f"/api/fill-orphans/{oid}/repair").status_code == 404
    # Status filter round-trips.
    assert client.get("/api/fill-orphans?status=repaired").json()["orphans"][0]["id"] == oid
    assert client.get("/api/fill-orphans?status=open").json()["orphans"] == []


def test_repair_unknown_404(client) -> None:
    assert client.post("/api/fill-orphans/orphan_nope/repair").status_code == 404


def test_repair_requires_auth(client) -> None:
    oid = fa.record_orphan(execution_id="e2", reason="r", symbol="BBB")
    resp = client.post(
        f"/api/fill-orphans/{oid}/repair", headers={"X-API-Token": "wrong"}
    )
    assert resp.status_code == 401
    del client.headers["X-API-Token"]
    assert client.post(f"/api/fill-orphans/{oid}/repair").status_code == 401
