"""Paper order proposals (recorded intents — not broker execution)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now


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


def _row_to_proposal(row: sqlite3.Row) -> dict[str, Any]:
    proposal = json.loads(row["proposal_json"] or "{}")
    return {
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


def list_paper_order_proposals(
    *,
    session_id: str | None = None,
    limit: int = 50,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_paper_order_tables(db_path)
    lim = max(1, min(limit, 200))
    with _connect(db_path) as conn:
        if session_id:
            rows = conn.execute(
                """
                SELECT * FROM paper_order_proposals
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, lim),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM paper_order_proposals
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
    return [_row_to_proposal(r) for r in rows]
