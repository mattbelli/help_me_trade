"""Paper order proposals (recorded intents — not broker execution)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("expires_at", "TEXT"),
    ("payload_hash", "TEXT"),
    ("delivery", "TEXT"),
    ("decided_at", "TEXT"),
    ("execution_id", "TEXT"),
    ("last_error", "TEXT"),
    ("source", "TEXT"),
    ("rule_id", "TEXT"),
    ("score_json", "TEXT"),
    ("thesis", "TEXT"),
    ("account_id", "TEXT"),
)


def canonical_json(obj: dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def init_paper_order_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS paper_order_proposals (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty REAL NOT NULL,
                order_type TEXT NOT NULL DEFAULT 'market',
                limit_price REAL,
                status TEXT NOT NULL DEFAULT 'recorded',
                proposal_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_paper_orders_created
                ON paper_order_proposals(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_paper_orders_session
                ON paper_order_proposals(session_id, created_at DESC);
            """
        )
        for column, col_type in _NEW_COLUMNS:
            try:
                conn.execute(
                    f"ALTER TABLE paper_order_proposals ADD COLUMN {column} {col_type}"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_proposals_account_status
                ON paper_order_proposals(account_id, status)
            """
        )
        conn.commit()


def _row_to_proposal(row: sqlite3.Row) -> dict[str, Any]:
    proposal = json.loads(row["proposal_json"] or "{}")
    out: dict[str, Any] = {
        "id": row["id"],
        "session_id": row["session_id"],
        "symbol": row["symbol"],
        "side": row["side"],
        "qty": row["qty"],
        "order_type": row["order_type"],
        "limit_price": row["limit_price"],
        "status": row["status"],
        "proposal": proposal,
        "created_at": row["created_at"],
    }
    for column, _ in _NEW_COLUMNS:
        if column in row.keys():
            out[column] = row[column]
    raw_score = row["score_json"] if "score_json" in row.keys() else None
    if raw_score:
        try:
            out["score"] = json.loads(raw_score)
        except json.JSONDecodeError:
            out["score"] = None
    else:
        out["score"] = None
    if "summary" not in out and isinstance(proposal, dict):
        out["summary"] = proposal.get("summary")
    return out


def record_paper_order_proposal(
    *,
    proposal: dict[str, Any],
    session_id: str | None = None,
    status: str = "recorded",
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_paper_order_tables(db_path)
    symbol = str(proposal.get("symbol", "")).strip().upper()
    side = str(proposal.get("side", "buy")).strip().lower()
    qty = float(proposal.get("qty", 0))
    order_type = str(proposal.get("order_type", "market")).strip().lower()
    limit_price = proposal.get("limit_price")
    pid = f"paper_{uuid.uuid4().hex[:12]}"
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO paper_order_proposals (
                id, session_id, symbol, side, qty, order_type,
                limit_price, status, proposal_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                session_id,
                symbol,
                side,
                qty,
                order_type,
                float(limit_price) if limit_price is not None else None,
                status,
                json.dumps(proposal),
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM paper_order_proposals WHERE id = ?", (pid,)
        ).fetchone()
    return _row_to_proposal(row)


def create_pending_proposal(
    *,
    proposal: dict[str, Any],
    intent_fields: dict[str, Any],
    session_id: str | None,
    ttl_seconds: int,
    summary: str,
    source: str | None = None,
    rule_id: str | None = None,
    score: dict[str, Any] | None = None,
    thesis: str | None = None,
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_paper_order_tables(db_path)
    payload_hash = hashlib.sha256(
        canonical_json(intent_fields).encode("utf-8")
    ).hexdigest()
    expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
    stored_payload = {
        **intent_fields,
        "summary": summary,
        "display": proposal,
    }
    symbol = str(intent_fields["symbol"]).strip().upper()
    side = str(intent_fields["side"]).strip().lower()
    qty = float(intent_fields["qty"])
    order_type = str(intent_fields["order_type"]).strip().lower()
    limit_raw = intent_fields.get("limit_price")
    limit_price = float(limit_raw) if limit_raw is not None else None
    pid = f"paper_{uuid.uuid4().hex[:12]}"
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO paper_order_proposals (
                id, session_id, symbol, side, qty, order_type, limit_price,
                status, proposal_json, created_at, expires_at, payload_hash, delivery,
                source, rule_id, score_json, thesis, account_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                session_id,
                symbol,
                side,
                qty,
                order_type,
                limit_price,
                "pending_phone",
                canonical_json(stored_payload),
                now,
                expires_at,
                payload_hash,
                "phone",
                source,
                rule_id,
                json.dumps(score) if score is not None else None,
                thesis,
                account_id,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM paper_order_proposals WHERE id = ?", (pid,)
        ).fetchone()
    return _row_to_proposal(row)


def get_proposal(
    proposal_id: str, *, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    init_paper_order_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM paper_order_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
    return _row_to_proposal(row) if row else None


def list_pending_proposals(
    *,
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_paper_order_tables(db_path)
    now = datetime.now(UTC).isoformat()
    with _connect(db_path) as conn:
        if account_id is not None:
            rows = conn.execute(
                """
                SELECT * FROM paper_order_proposals
                WHERE status = 'pending_phone'
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND account_id = ?
                ORDER BY created_at DESC
                """,
                (now, account_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM paper_order_proposals
                WHERE status = 'pending_phone'
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at DESC
                """,
                (now,),
            ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        parsed = _row_to_proposal(row)
        out.append(
            {
                "id": parsed["id"],
                "summary": parsed.get("summary") or parsed["proposal"].get("summary"),
                "symbol": parsed["symbol"],
                "side": parsed["side"],
                "qty": parsed["qty"],
                "account_id": parsed.get("account_id"),
                "expires_at": parsed.get("expires_at"),
                "payload_hash": parsed.get("payload_hash"),
                "score": parsed.get("score"),
            }
        )
    return out


def list_paper_order_proposals(
    *,
    session_id: str | None = None,
    status: str | None = None,
    since: str | None = None,
    limit: int = 50,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_paper_order_tables(db_path)
    lim = max(1, min(limit, 200))
    conditions: list[str] = []
    params: list[Any] = []
    if session_id:
        conditions.append("session_id = ?")
        params.append(session_id)
    if status:
        conditions.append("status = ?")
        params.append(status)
    if since:
        conditions.append("created_at > ?")
        params.append(since)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(lim)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM paper_order_proposals
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_row_to_proposal(r) for r in rows]


def list_book_entries_needing_stop(
    *,
    limit: int = 200,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Executed book BUY proposals whose entry execution has begun/finished
    filling but which have no live protective-stop execution row yet.

    Deliberately time-unbounded: a GTC book entry (limit/ext styles) can rest
    for weeks before filling, so any created_at window eventually misses it
    (entry-type review H2, 2026-07-24). The NOT EXISTS keeps the working set
    tiny — once a stop row exists in a non-failed state, the entry drops out.
    """
    from db.order_executions import init_execution_tables

    init_paper_order_tables(db_path)
    init_execution_tables(db_path)
    lim = max(1, min(limit, 200))
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT p.* FROM paper_order_proposals AS p
            JOIN order_executions AS e ON e.proposal_id = p.id
            WHERE p.status = 'executed'
              AND p.source LIKE 'book_%'
              AND p.side = 'buy'
              AND e.status IN ('filled', 'canceled', 'expired', 'partially_filled')
              AND NOT EXISTS (
                SELECT 1 FROM order_executions AS s
                WHERE s.proposal_id = 'stop|' || p.id
                  AND s.status NOT IN ('rejected', 'error')
              )
            ORDER BY p.created_at DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
    return [_row_to_proposal(r) for r in rows]


def claim_proposal_for_approval(
    proposal_id: str, *, db_path: Path = DEFAULT_DB_PATH
) -> str:
    """Atomically move pending_phone -> approving, only if still unexpired.

    Folding the status guard AND the expiry check into one UPDATE closes the
    check-then-act race: a proposal cannot be claimed after it expires, and two
    concurrent approvals can never both win. A null/malformed expires_at fails
    the ``expires_at > now`` test, so it is treated as expired (fail-safe).

    Returns one of: "claimed" | "not_found" | "expired" | "not_pending".
    """
    init_paper_order_tables(db_path)
    now = datetime.now(UTC).isoformat()
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE paper_order_proposals
            SET status = 'approving'
            WHERE id = ?
              AND status = 'pending_phone'
              AND expires_at IS NOT NULL
              AND expires_at > ?
            """,
            (proposal_id, now),
        )
        conn.commit()
        if cursor.rowcount == 1:
            return "claimed"
        row = conn.execute(
            "SELECT status FROM paper_order_proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
    if row is None:
        return "not_found"
    if row["status"] != "pending_phone":
        return "not_pending"
    return "expired"


def transition_proposal_status(
    proposal_id: str,
    *,
    expect: str,
    to: str,
    db_path: Path = DEFAULT_DB_PATH,
    **extra: Any,
) -> bool:
    init_paper_order_tables(db_path)
    fields = ["status = ?"]
    values: list[Any] = [to]
    if to in {"rejected", "expired", "executed", "blocked", "failed"}:
        fields.append("decided_at = ?")
        values.append(_now())
    for key, value in extra.items():
        fields.append(f"{key} = ?")
        values.append(value)
    values.extend([proposal_id, expect])
    with _connect(db_path) as conn:
        cursor = conn.execute(
            f"""
            UPDATE paper_order_proposals
            SET {", ".join(fields)}
            WHERE id = ? AND status = ?
            """,
            values,
        )
        conn.commit()
        return cursor.rowcount == 1
