"""The memory-write judge — ADD / UPDATE / SUPERSEDE / NOOP (design §4.2).

Every agent-side memory candidate (chat extraction, reflection, retrospective,
journal review, the ``save_memory`` tool) flows through here. The judge sees each
candidate together with its nearest existing memories (lexical FTS5 neighbours in
Phase 1; semantic in Phase 2) and decides, with meaning rather than token sets:

- ADD        — genuinely new; insert as active.
- UPDATE id  — a compatible refinement of an existing memory; rewrite that row's
               content. An agent-side UPDATE NEVER raises the row's authority.
- SUPERSEDE  — contradicts an existing memory (the user changed his mind, a thesis
               is replaced); insert the new row linked via ``supersedes_id`` and
               mark the old row ``status='superseded'``. This is the correction path
               the old Jaccard dedup used to silently destroy.
- NOOP       — already known, or below the admission bar; store nothing.

Authority containment (poisoning defence): the judge may never target a ``pinned``
or ``user_authored`` row with UPDATE/SUPERSEDE unless the candidate's own source is
``user_explicit`` — only the founder's own words can override the founder's own
rules. NOOP is disabled for ``user_explicit`` candidates (an explicit "remember
this" is always honoured).

Fail-soft: a judge LLM/parse failure drops agent candidates (prefer forgetting to
fabricating) but still honours ``user_explicit`` ones with a plain ADD. It never
crashes and never writes an unjudged agent memory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm
from ai.llm.structured import complete_for_extraction, parse_json_object
from db.ai_tables import create_memory, nearest_memories, update_memory

logger = logging.getLogger(__name__)

_NEIGHBORS = 8
_JUDGE_MAX_TOKENS = 800
_VALID_ACTIONS = frozenset({"ADD", "UPDATE", "SUPERSEDE", "NOOP"})

# Unique marker so the deterministic MockLLM (offline/tests) recognises the judge
# prompt and defaults to ADD-all rather than falling through to another branch.
_JUDGE_MARKER = "memory-write judge"

_SYSTEM = (
    f"You are the {_JUDGE_MARKER} for a trading copilot's long-term store. "
    "For each candidate memory below, decide exactly ONE action against the "
    "existing memories shown for it:\n"
    "- ADD: genuinely new knowledge worth keeping; store it.\n"
    "- UPDATE <id>: a compatible refinement of that existing memory (adds detail, "
    "no conflict) — supply the merged content in \"content\".\n"
    "- SUPERSEDE <id>: conflicts with that existing memory (the user changed his "
    "mind, or a thesis is replaced) — the candidate replaces it.\n"
    "- NOOP: already known (a paraphrase of an existing memory) or not worth "
    "writing down.\n"
    "Prefer NOOP over storing near-duplicates. Only ADD when a great human "
    "assistant would write it down. \"target_id\" must be one of the existing-memory "
    "ids listed for that candidate, or null. "
    "Return JSON only: "
    '{"decisions": [{"index": 0, "action": "ADD|UPDATE|SUPERSEDE|NOOP", '
    '"target_id": null, "content": null, "reason": "..."}]} '
    "with exactly one entry per candidate index."
)


@dataclass
class Candidate:
    """One proposed memory awaiting a judge decision."""

    memory_type: str
    content: str
    quote: str | None = None
    symbols: list[str] = field(default_factory=list)
    source: str = "chat_extract"
    authority: str = "agent_inferred"
    valid_until: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _format_neighbor(mem: dict[str, Any]) -> str:
    pin = " pinned" if mem.get("pinned") else ""
    return (
        f"    [{mem['id']}] ({mem.get('type')}, {mem.get('status', 'active')}, "
        f"{mem.get('authority', 'agent_inferred')}{pin}) {mem.get('content', '')}"
    )


def _build_user_prompt(
    candidates: list[Candidate], neighbors: list[list[dict[str, Any]]]
) -> str:
    blocks: list[str] = []
    for i, cand in enumerate(candidates):
        lines = [
            f"Candidate {i}:",
            f"  type: {cand.memory_type}",
            f"  content: {cand.content}",
        ]
        if cand.quote:
            lines.append(f"  quote: {cand.quote}")
        if cand.symbols:
            lines.append(f"  symbols: {', '.join(cand.symbols)}")
        nb = neighbors[i]
        if nb:
            lines.append("  Existing memories:")
            lines.extend(_format_neighbor(m) for m in nb)
        else:
            lines.append("  Existing memories: (none)")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _parse_decisions(raw: str, n: int) -> dict[int, dict[str, Any]]:
    parsed = parse_json_object(raw, prefer_keys={"decisions"})
    if not parsed:
        return {}
    items = parsed.get("decisions")
    if not isinstance(items, list):
        return {}
    out: dict[int, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < n:
            out[idx] = item
    return out


def _apply(
    cand: Candidate,
    decision: dict[str, Any] | None,
    neighbors: list[dict[str, Any]],
    *,
    db_path: Path | None,
) -> dict[str, Any] | None:
    """Apply one judged decision to the store; return the stored/updated record
    (or None for NOOP / dropped)."""
    by_id = {int(m["id"]): m for m in neighbors}
    is_explicit = cand.source == "user_explicit"

    action = str(decision.get("action", "")).strip().upper() if decision else ""
    if action not in _VALID_ACTIONS:
        action = ""
    # An explicit "remember this" is always honoured: never NOOP, never dropped.
    if is_explicit and action in ("", "NOOP"):
        action = "ADD"
    if action == "":
        logger.warning(
            "Judge returned no usable decision for candidate (%s) — dropped",
            cand.memory_type,
        )
        return None
    if action == "NOOP":
        return None

    target_id: int | None = None
    if action in ("UPDATE", "SUPERSEDE"):
        raw_target = (decision or {}).get("target_id")
        try:
            target_id = int(raw_target) if raw_target is not None else None
        except (TypeError, ValueError):
            target_id = None
        target = by_id.get(target_id) if target_id is not None else None
        if target is None:
            # Hallucinated / missing target — degrade to a safe ADD.
            action = "ADD"
        elif (
            target.get("pinned") or target.get("authority") == "user_authored"
        ) and not is_explicit:
            # Containment: an agent-side writer can't override the founder's own
            # rule. Keep the candidate as a fresh ADD instead of touching the row.
            logger.info(
                "Judge blocked %s of protected memory %s by agent source %s — ADD",
                action,
                target_id,
                cand.source,
            )
            action = "ADD"

    record: dict[str, Any] | None = None
    if action == "ADD":
        record = create_memory(
            memory_type=cand.memory_type,
            content=cand.content,
            metadata=cand.metadata,
            source=cand.source,
            authority=cand.authority,
            quote=cand.quote,
            symbols=cand.symbols,
            valid_until=cand.valid_until,
            db_path=db_path,
        )
    elif action == "UPDATE":
        merged = str((decision or {}).get("content") or "").strip() or cand.content
        # Authority is deliberately NOT passed — an agent UPDATE never raises it.
        record = update_memory(target_id, content=merged, db_path=_resolve(db_path))
    elif action == "SUPERSEDE":
        record = create_memory(
            memory_type=cand.memory_type,
            content=cand.content,
            metadata=cand.metadata,
            source=cand.source,
            authority=cand.authority,
            quote=cand.quote,
            symbols=cand.symbols,
            supersedes_id=target_id,
            valid_until=cand.valid_until,
            db_path=db_path,
        )
        update_memory(target_id, status="superseded", db_path=_resolve(db_path))
    if record is not None:
        return {**record, "action": action}
    return None


def _resolve(db_path: Path | None) -> Path:
    if db_path is not None:
        return db_path
    import db.base as db_base

    return db_base.DEFAULT_DB_PATH


def judge_and_store(
    candidates: list[Candidate],
    *,
    db_path: Path | None = None,
    llm: Any | None = None,
) -> list[dict[str, Any]]:
    """Route candidates through the judge and apply the decisions. Returns the
    records that were stored or updated (NOOP / dropped candidates contribute
    nothing). One LLM call per batch."""
    if not candidates:
        return []

    neighbors = [
        nearest_memories(
            f"{c.content} {c.quote or ''}",
            symbols=c.symbols,
            limit=_NEIGHBORS,
            db_path=db_path,
        )
        for c in candidates
    ]

    decisions: dict[int, dict[str, Any]] = {}
    judged = False
    try:
        client = llm if llm is not None else get_llm()
        messages = [
            LLMMessage(role="system", content=_SYSTEM),
            LLMMessage(
                role="user", content=_build_user_prompt(candidates, neighbors)
            ),
        ]
        raw = complete_for_extraction(
            client, messages, max_tokens=_JUDGE_MAX_TOKENS
        )
        decisions = _parse_decisions(raw, len(candidates))
        judged = bool(decisions)
        if not judged:
            logger.warning("Judge produced no parseable decisions")
    except Exception as exc:  # noqa: BLE001 — fail-soft is the contract
        logger.warning("Memory judge failed: %s", exc)

    stored: list[dict[str, Any]] = []
    for i, cand in enumerate(candidates):
        if not judged and cand.source != "user_explicit":
            # Judge unavailable: prefer forgetting for agent candidates.
            continue
        record = _apply(cand, decisions.get(i), neighbors[i], db_path=db_path)
        if record is not None:
            stored.append(record)
    return stored
