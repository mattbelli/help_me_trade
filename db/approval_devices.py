"""Approval-device registry — the phones whose Ed25519 key may approve a proposal.

One row per enrolled device: the PUBLIC key only (the private half never leaves
the phone's keychain). A revoked row stays on disk so the audit trail and the
"has any device ever been enrolled" ratchet survive.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now


def init_approval_device_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS approval_devices (
                device_id TEXT PRIMARY KEY,
                public_key TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                enrolled_at TEXT NOT NULL,
                revoked_at TEXT,
                last_used_at TEXT
            );
            """
        )
        conn.commit()


def enroll_device(
    *,
    device_id: str,
    public_key: str,
    label: str = "",
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Register (or re-register) a device. Re-enrolling an existing id replaces
    its key and clears any revocation — the phone rotated, the trust is renewed."""
    init_approval_device_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO approval_devices (device_id, public_key, label, enrolled_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                public_key = excluded.public_key,
                label = excluded.label,
                enrolled_at = excluded.enrolled_at,
                revoked_at = NULL
            """,
            (device_id, public_key, label, _now()),
        )
        conn.commit()


def get_device(device_id: str, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    init_approval_device_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM approval_devices WHERE device_id = ?", (device_id,)
        ).fetchone()
    return dict(row) if row else None


def list_devices(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    init_approval_device_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM approval_devices ORDER BY enrolled_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_device(device_id: str, db_path: Path = DEFAULT_DB_PATH) -> bool:
    init_approval_device_tables(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE approval_devices SET revoked_at = ? "
            "WHERE device_id = ? AND revoked_at IS NULL",
            (_now(), device_id),
        )
        conn.commit()
        return cur.rowcount > 0


def touch_device(device_id: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE approval_devices SET last_used_at = ? WHERE device_id = ?",
            (_now(), device_id),
        )
        conn.commit()


def any_device_enrolled(db_path: Path = DEFAULT_DB_PATH) -> bool:
    """True once ANY device has ever been enrolled (revoked rows count): the
    moment a real device exists, the shared-token approval path is closed."""
    init_approval_device_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT 1 FROM approval_devices LIMIT 1").fetchone()
    return row is not None
