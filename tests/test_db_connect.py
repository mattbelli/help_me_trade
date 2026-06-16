"""SQLite connection hardening (WAL, busy timeout)."""

from __future__ import annotations

from pathlib import Path

from db.base import BUSY_TIMEOUT_MS, connect


def test_connect_enables_wal_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


def test_connect_sets_busy_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == BUSY_TIMEOUT_MS
