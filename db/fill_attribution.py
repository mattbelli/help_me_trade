"""Durable quarantine for fills whose strategy attribution failed.

Safety net for the exit module: when a strategy BUY fills at the broker but
``record_strategy_fill`` cannot open its managed-position row (transient DB
failure, a missing strategy record, no bound account), the fill would otherwise
become an ORPHAN — real (paper) shares held with NO stop-loss / exit rules
attached, because the exit engine only walks ``managed_positions``. Rather than
swallow that, we durably record the orphan here and page the founder (a critical
notification) so it can be repaired by hand. Deny-by-default: when in doubt about
a position's management state, record loudly.

Bookkeeping only — this never touches the broker or the execution record.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

logger = logging.getLogger(__name__)


def init_fill_attribution_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS fill_attribution_orphans (
                id TEXT PRIMARY KEY,
                execution_id TEXT,
                account_id TEXT,
                strategy_id TEXT,
                proposal_id TEXT,
                symbol TEXT,
                qty TEXT,
                reason TEXT NOT NULL,
                detail TEXT,
                -- open = needs to page the founder; notified = paged, awaiting
                -- manual repair; repaired = a managed row now exists.
                status TEXT NOT NULL DEFAULT 'open',
                notified_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fill_orphan_status
                ON fill_attribution_orphans (status);
            -- At most one live orphan per execution — poll re-entry can't dup-page.
            CREATE UNIQUE INDEX IF NOT EXISTS uq_fill_orphan_live_exec
                ON fill_attribution_orphans (execution_id)
                WHERE status IN ('open', 'notified') AND execution_id IS NOT NULL;
            """
        )
        conn.commit()


def record_orphan(
    *,
    execution_id: str | None,
    reason: str,
    symbol: str | None = None,
    account_id: str | None = None,
    strategy_id: str | None = None,
    proposal_id: str | None = None,
    qty: str | None = None,
    detail: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """Durably record an unattributable fill. Idempotent per execution while unrepaired.

    Returns the orphan id (existing or new), or None if even this safety write failed —
    in which case we log CRITICAL, since the DB itself is the problem and a fill may be
    held with no exit management and no durable trace.
    """
    try:
        init_fill_attribution_tables(db_path)
        now = _now()
        with _connect(db_path) as conn:
            if execution_id:
                existing = conn.execute(
                    "SELECT id FROM fill_attribution_orphans "
                    "WHERE execution_id = ? AND status IN ('open', 'notified')",
                    (execution_id,),
                ).fetchone()
                if existing is not None:
                    return str(existing["id"])
            new_id = f"orphan_{uuid.uuid4().hex[:16]}"
            try:
                conn.execute(
                    """
                    INSERT INTO fill_attribution_orphans (
                        id, execution_id, account_id, strategy_id, proposal_id,
                        symbol, qty, reason, detail, status, notified_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', NULL, ?, ?)
                    """,
                    (
                        new_id, execution_id, account_id, strategy_id, proposal_id,
                        symbol, qty, reason, detail, now, now,
                    ),
                )
            except sqlite3.IntegrityError:
                # Concurrent pass won the unique live-orphan index — the
                # quarantine EXISTS, so this is a benign race, not a CRITICAL.
                if execution_id:
                    existing = conn.execute(
                        "SELECT id FROM fill_attribution_orphans "
                        "WHERE execution_id = ? AND status IN ('open', 'notified')",
                        (execution_id,),
                    ).fetchone()
                    if existing is not None:
                        return str(existing["id"])
                raise
            conn.commit()
            return new_id
    except Exception:
        logger.critical(
            "UNMANAGED FILL and could not even quarantine it — execution=%s symbol=%s "
            "strategy=%s reason=%s: a strategy BUY may be filled with NO exit management",
            execution_id, symbol, strategy_id, reason, exc_info=True,
        )
        return None


def orphan_exists_for_execution(
    execution_id: str, *, db_path: Path = DEFAULT_DB_PATH
) -> bool:
    """True if ANY orphan row exists for this execution, regardless of status.

    The unmanaged-fill sweep uses this to stay idempotent across restarts: an execution
    already quarantined — even one since marked 'repaired' (which the live-orphan unique
    index no longer covers) — must never be re-quarantined."""
    init_fill_attribution_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM fill_attribution_orphans WHERE execution_id = ? LIMIT 1",
            (execution_id,),
        ).fetchone()
    return row is not None


def list_open_orphans(*, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Orphans that still need to page the founder (status='open')."""
    init_fill_attribution_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM fill_attribution_orphans WHERE status = 'open' "
            "ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_orphan_notified(orphan_id: str, *, db_path: Path = DEFAULT_DB_PATH) -> None:
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE fill_attribution_orphans SET status = 'notified', "
            "notified_at = ?, updated_at = ? WHERE id = ? AND status = 'open'",
            (now, now, orphan_id),
        )
        conn.commit()
