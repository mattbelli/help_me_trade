"""Server-side ledger for chat tool-approval requests (single-consume).

The web chat loop pauses on an approval tool (propose_order / create_rule) and
emits an Approve/Reject card. The resume turn arrives from the CLIENT — the
approval verdict and the tool input ride the re-POSTed message array — so
without a server-side record, a tampered client could replay an approval, mutate
the input after the card was shown, or forge an approval that was never issued.

This ledger closes that: every ApprovalRequest the loop emits is recorded here
(approval_id -> tool_call_id, tool_name, sha256 of the canonical input), and a
resume may execute only by atomically CLAIMING its row — same tool, same input
hash, never consumed before. DB-backed (not in-memory) so a server restart
between card and tap still validates. Replays and double-taps lose the
conditional-UPDATE race and get the denial path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

# claim_approval outcomes
CLAIMED = "claimed"
UNKNOWN = "unknown"
MISMATCH = "mismatch"
CONSUMED = "consumed"


def canonical_input_hash(tool_input: dict[str, Any]) -> str:
    """sha256 of the canonical JSON form — MUST be used on both emit and resume."""
    canonical = json.dumps(
        tool_input, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def init_chat_approvals(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_approvals (
                approval_id TEXT PRIMARY KEY,
                tool_call_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                consumed_at TEXT
            );
            """
        )
        conn.commit()


def record_approval_request(
    approval_id: str,
    *,
    tool_call_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Persist an issued approval card so the resume can be validated later."""
    init_chat_approvals(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO chat_approvals (
                approval_id, tool_call_id, tool_name, input_hash, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                tool_call_id,
                tool_name,
                canonical_input_hash(tool_input),
                _now(),
            ),
        )
        conn.commit()


def claim_approval(
    approval_id: str,
    *,
    tool_call_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    db_path: Path = DEFAULT_DB_PATH,
) -> str:
    """Atomically consume the approval row iff it matches and is unconsumed.

    Returns CLAIMED exactly once per approval (the conditional UPDATE's WHERE
    binds identity + tool + input-hash + unconsumed, so only one claimer wins —
    replays, double-taps, and concurrent tabs lose the race). Otherwise returns
    the reason: UNKNOWN (never issued), MISMATCH (tool or input differs from
    what the card showed), or CONSUMED (already decided once).
    """
    init_chat_approvals(db_path)
    input_hash = canonical_input_hash(tool_input)
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE chat_approvals SET consumed_at = ?
            WHERE approval_id = ? AND tool_call_id = ? AND tool_name = ?
              AND input_hash = ? AND consumed_at IS NULL
            """,
            (_now(), approval_id, tool_call_id, tool_name, input_hash),
        )
        conn.commit()
        if cur.rowcount == 1:
            return CLAIMED
        # Losing path: classify for a clear denial message (read-only).
        row = conn.execute(
            "SELECT tool_call_id, tool_name, input_hash, consumed_at"
            " FROM chat_approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
    if row is None:
        return UNKNOWN
    if (
        row["tool_call_id"] != tool_call_id
        or row["tool_name"] != tool_name
        or row["input_hash"] != input_hash
    ):
        return MISMATCH
    return CONSUMED
