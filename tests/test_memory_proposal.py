"""Founder-review supersede lane (M-1): resolving a pending proposal.

The judge parks a correction that targeted a protected founder row as a fresh
ADD stamped with ``proposed_supersedes_id``. These tests cover the founder's
Accept (promote + supersede) and Keep-old (archive) resolutions, plus the
endpoint's founder-token gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import db.ai_tables as ai_tables


def _proposal_pair(db: Path) -> tuple[dict, dict]:
    """A protected (user_confirmed) row + a pending proposal against it."""
    old = ai_tables.create_memory(
        memory_type="directive",
        content="Cap any single name at 20% of portfolio",
        authority="user_confirmed",
        source="widget_confirm",
        db_path=db,
    )
    proposal = ai_tables.create_memory(
        memory_type="directive",
        content="Cap any single name at 30% of portfolio",
        authority="agent_inferred",
        source="chat_extract",
        proposed_supersedes_id=old["id"],
        db_path=db,
    )
    return old, proposal


def test_accept_promotes_new_and_retires_old(tmp_path: Path) -> None:
    db = tmp_path / "proposal.db"
    old, proposal = _proposal_pair(db)

    result = ai_tables.resolve_memory_proposal(
        proposal["id"], action="accept", db_path=db
    )
    assert result is not None

    promoted = ai_tables.get_memory(proposal["id"], db_path=db)
    assert promoted["authority"] == "user_confirmed"
    assert promoted["status"] == "active"
    assert promoted["supersedes_id"] == old["id"]
    # Proposal marker cleared → injectable again.
    assert "proposed_supersedes_id" not in promoted

    retired = ai_tables.get_memory(old["id"], db_path=db)
    assert retired["status"] == "superseded"

    # Only the promoted correction is active now.
    active = ai_tables.list_memories(db_path=db)
    assert [m["content"] for m in active] == [
        "Cap any single name at 30% of portfolio"
    ]


def test_reject_archives_proposal_leaves_old_untouched(tmp_path: Path) -> None:
    db = tmp_path / "proposal.db"
    old, proposal = _proposal_pair(db)

    result = ai_tables.resolve_memory_proposal(
        proposal["id"], action="reject", db_path=db
    )
    assert result is not None

    archived = ai_tables.get_memory(proposal["id"], db_path=db)
    assert archived["status"] == "archived"
    assert "proposed_supersedes_id" not in archived

    # Old row is byte-for-byte untouched, still the confirmed rule.
    still = ai_tables.get_memory(old["id"], db_path=db)
    assert still["status"] == "active"
    assert still["authority"] == "user_confirmed"
    assert still["content"] == "Cap any single name at 20% of portfolio"

    active = ai_tables.list_memories(db_path=db)
    assert [m["content"] for m in active] == [
        "Cap any single name at 20% of portfolio"
    ]


def test_resolve_no_pending_proposal_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "proposal.db"
    plain = ai_tables.create_memory(
        memory_type="fact", content="Trades from his phone", db_path=db
    )
    assert (
        ai_tables.resolve_memory_proposal(plain["id"], action="accept", db_path=db)
        is None
    )
    assert ai_tables.resolve_memory_proposal(9999, action="reject", db_path=db) is None


def test_resolve_rejects_bad_action(tmp_path: Path) -> None:
    db = tmp_path / "proposal.db"
    _, proposal = _proposal_pair(db)
    with pytest.raises(ValueError):
        ai_tables.resolve_memory_proposal(proposal["id"], action="promote", db_path=db)


def test_non_founder_cannot_resolve_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth: the resolve endpoint mutates, so the security middleware gates it
    behind the founder API token — a tokenless request 401s before the handler
    runs (per test_auth_scopes patterns)."""
    monkeypatch.setattr("api.auth.API_TOKEN", "founder-secret")
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        res = client.post(
            "/api/memories/1/resolve-proposal", json={"action": "accept"}
        )
    assert res.status_code == 401
