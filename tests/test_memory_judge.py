"""The memory-write judge decision matrix (ADD / UPDATE / SUPERSEDE / NOOP).

Deterministic: a scripted stub LLM returns the judge's decision JSON so the store
side-effects and the authority containment rules are asserted without a live model.
"""

from __future__ import annotations

import json
from pathlib import Path

import db.ai_tables as ai_tables
from ai.memories.judge import Candidate, judge_and_store


class StubLLM:
    """Returns a fixed {"decisions": [...]} payload for every call."""

    provider = "stub"

    def __init__(self, decisions: list[dict]) -> None:
        self._payload = json.dumps({"decisions": decisions})

    def complete_json(self, messages, *, max_tokens=1024, temperature=0.1) -> str:
        return self._payload

    def complete(self, messages, *, max_tokens=1024) -> str:
        return self._payload


class BoomLLM:
    provider = "boom"

    def complete_json(self, *a, **k) -> str:
        raise RuntimeError("judge is down")

    def complete(self, *a, **k) -> str:
        raise RuntimeError("judge is down")


def _db(tmp_path: Path) -> Path:
    return tmp_path / "judge.db"


def test_add_new_memory(tmp_path: Path) -> None:
    db = _db(tmp_path)
    llm = StubLLM([{"index": 0, "action": "ADD"}])
    cand = Candidate(memory_type="preference", content="Prefers weekly index options")
    stored = judge_and_store([cand], db_path=db, llm=llm)
    assert len(stored) == 1
    assert stored[0]["action"] == "ADD"
    rows = ai_tables.list_memories(db_path=db)
    assert len(rows) == 1
    assert rows[0]["content"] == "Prefers weekly index options"


def test_noop_stores_nothing(tmp_path: Path) -> None:
    db = _db(tmp_path)
    ai_tables.create_memory(
        memory_type="preference", content="Prefers weekly index options", db_path=db
    )
    llm = StubLLM([{"index": 0, "action": "NOOP", "reason": "paraphrase"}])
    cand = Candidate(memory_type="preference", content="Likes weekly options on indexes")
    stored = judge_and_store([cand], db_path=db, llm=llm)
    assert stored == []
    assert len(ai_tables.list_memories(db_path=db)) == 1


def test_update_merges_and_never_raises_authority(tmp_path: Path) -> None:
    db = _db(tmp_path)
    row = ai_tables.create_memory(
        memory_type="fact",
        content="Has Schwab accounts",
        authority="agent_inferred",
        db_path=db,
    )
    llm = StubLLM(
        [
            {
                "index": 0,
                "action": "UPDATE",
                "target_id": row["id"],
                "content": "Has 4 real Schwab accounts, read-only",
            }
        ]
    )
    cand = Candidate(
        memory_type="fact",
        content="Has 4 real Schwab accounts, read-only",
        # Even if a candidate claimed higher authority, an agent UPDATE can't raise it.
        authority="user_authored",
    )
    stored = judge_and_store([cand], db_path=db, llm=llm)
    assert len(stored) == 1
    assert stored[0]["action"] == "UPDATE"
    updated = ai_tables.get_memory(row["id"], db_path=db)
    assert updated["content"] == "Has 4 real Schwab accounts, read-only"
    assert updated["authority"] == "agent_inferred"  # unchanged
    assert len(ai_tables.list_memories(db_path=db)) == 1


def test_supersede_links_and_retires_old_row(tmp_path: Path) -> None:
    """Regression for the correction-swallowing bug (§0.1): 25% cap must SUPERSEDE
    the stored 20% cap, not be discarded."""
    db = _db(tmp_path)
    old = ai_tables.create_memory(
        memory_type="directive",
        content="Cap any single name at 20% of portfolio",
        authority="user_authored",
        db_path=db,
    )
    llm = StubLLM([{"index": 0, "action": "SUPERSEDE", "target_id": old["id"]}])
    # The founder's own correction — user_explicit source clears the containment guard.
    cand = Candidate(
        memory_type="directive",
        content="Cap any single name at 25% of portfolio",
        source="user_explicit",
        authority="user_authored",
    )
    stored = judge_and_store([cand], db_path=db, llm=llm)
    assert len(stored) == 1
    new_row = ai_tables.get_memory(stored[0]["id"], db_path=db)
    assert new_row["content"] == "Cap any single name at 25% of portfolio"
    assert new_row["status"] == "active"
    assert new_row["supersedes_id"] == old["id"]
    retired = ai_tables.get_memory(old["id"], db_path=db)
    assert retired["status"] == "superseded"
    active = ai_tables.list_memories(db_path=db)
    assert [m["content"] for m in active] == [
        "Cap any single name at 25% of portfolio"
    ]


def test_agent_cannot_supersede_user_authored_rule(tmp_path: Path) -> None:
    """Containment: a reflection/agent candidate can't override the founder's own
    directive — the judge's SUPERSEDE is downgraded to a plain ADD."""
    db = _db(tmp_path)
    protected = ai_tables.create_memory(
        memory_type="directive",
        content="Never propose shorts",
        authority="user_authored",
        db_path=db,
    )
    llm = StubLLM(
        [{"index": 0, "action": "SUPERSEDE", "target_id": protected["id"]}]
    )
    cand = Candidate(
        memory_type="directive",
        content="Shorting is fine sometimes",
        source="reflection",
        authority="agent_inferred",
    )
    stored = judge_and_store([cand], db_path=db, llm=llm)
    assert len(stored) == 1
    assert stored[0]["action"] == "ADD"
    still = ai_tables.get_memory(protected["id"], db_path=db)
    assert still["status"] == "active"  # untouched
    assert len(ai_tables.list_memories(db_path=db)) == 2


def test_user_explicit_never_noops(tmp_path: Path) -> None:
    db = _db(tmp_path)
    llm = StubLLM([{"index": 0, "action": "NOOP"}])
    cand = Candidate(
        memory_type="directive",
        content="Never trade the first 15 minutes",
        quote="never trade the first 15 minutes",
        source="user_explicit",
        authority="user_authored",
    )
    stored = judge_and_store([cand], db_path=db, llm=llm)
    assert len(stored) == 1
    assert stored[0]["action"] == "ADD"
    rows = ai_tables.list_memories(db_path=db)
    assert rows[0]["authority"] == "user_authored"


def test_hallucinated_target_degrades_to_add(tmp_path: Path) -> None:
    db = _db(tmp_path)
    existing = ai_tables.create_memory(
        memory_type="fact", content="Trades from his phone", db_path=db
    )
    llm = StubLLM([{"index": 0, "action": "UPDATE", "target_id": 99999}])
    cand = Candidate(memory_type="fact", content="Works a day job")
    stored = judge_and_store([cand], db_path=db, llm=llm)
    assert stored[0]["action"] == "ADD"
    assert (
        ai_tables.get_memory(existing["id"], db_path=db)["content"]
        == "Trades from his phone"
    )
    assert len(ai_tables.list_memories(db_path=db)) == 2


def test_judge_failure_drops_agent_but_honors_user_explicit(tmp_path: Path) -> None:
    db = _db(tmp_path)
    agent = Candidate(
        memory_type="lesson", content="Tends to cut winners early", source="reflection"
    )
    explicit = Candidate(
        memory_type="directive",
        content="Always state the stop before the target",
        quote="always tell me the stop first",
        source="user_explicit",
        authority="user_authored",
    )
    stored = judge_and_store([agent, explicit], db_path=db, llm=BoomLLM())
    # Prefer forgetting for the agent candidate; still honour the explicit save.
    assert len(stored) == 1
    assert stored[0]["content"] == "Always state the stop before the target"
    contents = {m["content"] for m in ai_tables.list_memories(db_path=db)}
    assert contents == {"Always state the stop before the target"}


def test_empty_batch_returns_empty(tmp_path: Path) -> None:
    assert judge_and_store([], db_path=_db(tmp_path), llm=StubLLM([])) == []
