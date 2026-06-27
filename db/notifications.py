"""Persistent notification inbox — server-side source of truth."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now


def init_notifications_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id           TEXT PRIMARY KEY,
                kind         TEXT NOT NULL,
                title        TEXT NOT NULL,
                body         TEXT NOT NULL DEFAULT '',
                severity     TEXT NOT NULL DEFAULT 'info',
                account_id   TEXT,
                strategy_id  TEXT,
                ref_type     TEXT,
                ref_id       TEXT,
                action_url   TEXT,
                group_key    TEXT,
                data_json    TEXT NOT NULL DEFAULT '{}',
                created_at   TEXT NOT NULL,
                read_at      TEXT,
                dismissed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_created
                ON notifications (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_notifications_read
                ON notifications (read_at);
            CREATE INDEX IF NOT EXISTS idx_notifications_account
                ON notifications (account_id);
            CREATE INDEX IF NOT EXISTS idx_notifications_strategy
                ON notifications (strategy_id);
            """
        )
        conn.commit()


def _row_to_notification(row: Any) -> dict[str, Any]:
    data_raw = row["data_json"]
    try:
        data = json.loads(data_raw) if data_raw else {}
    except (TypeError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    read_at = row["read_at"]
    dismissed_at = row["dismissed_at"]
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "body": row["body"],
        "severity": row["severity"],
        "account_id": row["account_id"],
        "strategy_id": row["strategy_id"],
        "ref_type": row["ref_type"],
        "ref_id": row["ref_id"],
        "action_url": row["action_url"],
        "group_key": row["group_key"],
        "data": data,
        "created_at": row["created_at"],
        "read_at": read_at,
        "dismissed_at": dismissed_at,
        "read": read_at is not None,
        "dismissed": dismissed_at is not None,
    }


def _get_notification_row(
    notification_id: str, *, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    init_notifications_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, kind, title, body, severity, account_id, strategy_id,
                   ref_type, ref_id, action_url, group_key, data_json,
                   created_at, read_at, dismissed_at
            FROM notifications
            WHERE id = ?
            """,
            (notification_id.strip(),),
        ).fetchone()
    return _row_to_notification(row) if row else None


def create_notification(
    *,
    kind: str,
    title: str,
    body: str = "",
    severity: str = "info",
    account_id: str | None = None,
    strategy_id: str | None = None,
    ref_type: str | None = None,
    ref_id: str | None = None,
    action_url: str | None = None,
    group_key: str | None = None,
    data: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_notifications_tables(db_path)
    notification_id = uuid.uuid4().hex[:12]
    now = _now()
    payload = dict(data or {})
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO notifications (
                id, kind, title, body, severity, account_id, strategy_id,
                ref_type, ref_id, action_url, group_key, data_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notification_id,
                kind,
                title,
                body,
                severity,
                account_id,
                strategy_id,
                ref_type,
                ref_id,
                action_url,
                group_key,
                json.dumps(payload),
                now,
            ),
        )
        conn.commit()
    return {
        "id": notification_id,
        "kind": kind,
        "title": title,
        "body": body,
        "severity": severity,
        "account_id": account_id,
        "strategy_id": strategy_id,
        "ref_type": ref_type,
        "ref_id": ref_id,
        "action_url": action_url,
        "group_key": group_key,
        "data": payload,
        "created_at": now,
        "read_at": None,
        "dismissed_at": None,
        "read": False,
        "dismissed": False,
    }


def list_notifications(
    *,
    limit: int = 50,
    before: str | None = None,
    account_id: str | None = None,
    strategy_id: str | None = None,
    include_dismissed: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_notifications_tables(db_path)
    cap = max(1, min(int(limit), 200))
    clauses = ["1=1"]
    params: list[Any] = []
    if not include_dismissed:
        clauses.append("dismissed_at IS NULL")
    if before:
        clauses.append("created_at < ?")
        params.append(before)
    if account_id is not None:
        clauses.append("account_id = ?")
        params.append(account_id)
    if strategy_id is not None:
        clauses.append("strategy_id = ?")
        params.append(strategy_id)
    where = " AND ".join(clauses)
    params.append(cap)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, kind, title, body, severity, account_id, strategy_id,
                   ref_type, ref_id, action_url, group_key, data_json,
                   created_at, read_at, dismissed_at
            FROM notifications
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_row_to_notification(row) for row in rows]


def mark_read(
    notification_id: str, *, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    init_notifications_tables(db_path)
    clean_id = notification_id.strip()
    now = _now()
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT read_at FROM notifications WHERE id = ?",
            (clean_id,),
        ).fetchone()
        if row is None:
            return None
        if row["read_at"] is None:
            conn.execute(
                "UPDATE notifications SET read_at = ? WHERE id = ?",
                (now, clean_id),
            )
            conn.commit()
    return _get_notification_row(clean_id, db_path=db_path)


def mark_all_read(
    *, account_id: str | None = None, db_path: Path = DEFAULT_DB_PATH
) -> int:
    init_notifications_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        if account_id is not None:
            cur = conn.execute(
                """
                UPDATE notifications
                SET read_at = ?
                WHERE read_at IS NULL AND account_id = ?
                """,
                (now, account_id),
            )
        else:
            cur = conn.execute(
                """
                UPDATE notifications
                SET read_at = ?
                WHERE read_at IS NULL
                """,
                (now,),
            )
        conn.commit()
        return int(cur.rowcount)


def dismiss(
    notification_id: str, *, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    init_notifications_tables(db_path)
    clean_id = notification_id.strip()
    now = _now()
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM notifications WHERE id = ?",
            (clean_id,),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE notifications SET dismissed_at = ? WHERE id = ?",
            (now, clean_id),
        )
        conn.commit()
    return _get_notification_row(clean_id, db_path=db_path)


def unread_count(*, account_id: str | None = None, db_path: Path = DEFAULT_DB_PATH) -> int:
    init_notifications_tables(db_path)
    with _connect(db_path) as conn:
        if account_id is not None:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM notifications
                WHERE read_at IS NULL AND dismissed_at IS NULL AND account_id = ?
                """,
                (account_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM notifications
                WHERE read_at IS NULL AND dismissed_at IS NULL
                """
            ).fetchone()
    return int(row["cnt"])
