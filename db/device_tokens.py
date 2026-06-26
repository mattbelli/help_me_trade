"""Device push-token registry — Expo now, APNs later."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now


def init_device_token_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS device_tokens (
                token TEXT PRIMARY KEY,
                platform TEXT NOT NULL DEFAULT 'ios',
                token_type TEXT NOT NULL DEFAULT 'expo',
                account_id TEXT,
                app_version TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_seen TEXT
            );
            """
        )
        conn.commit()


def register_device_token(
    *,
    token: str,
    platform: str = "ios",
    token_type: str = "expo",
    account_id: str | None = None,
    app_version: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    clean = str(token or "").strip()
    if not clean:
        return
    init_device_token_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO device_tokens (
                token, platform, token_type, account_id, app_version,
                active, created_at, updated_at, last_seen
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(token) DO UPDATE SET
                platform = excluded.platform,
                token_type = excluded.token_type,
                account_id = excluded.account_id,
                app_version = excluded.app_version,
                active = 1,
                updated_at = excluded.updated_at,
                last_seen = excluded.last_seen
            """,
            (
                clean,
                platform,
                token_type,
                account_id,
                app_version,
                now,
                now,
                now,
            ),
        )
        conn.commit()


def deactivate_device_token(
    token: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    clean = str(token or "").strip()
    if not clean:
        return
    init_device_token_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE device_tokens SET active = 0, updated_at = ? WHERE token = ?",
            (now, clean),
        )
        conn.commit()


def list_active_device_tokens(
    *,
    token_type: str = "expo",
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_device_token_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM device_tokens WHERE active = 1 AND token_type = ? "
            "ORDER BY updated_at DESC",
            (token_type,),
        ).fetchall()
    return [dict(row) for row in rows]
