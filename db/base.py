"""Shared SQLite connection helpers."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(
    os.environ.get("TRADING_DB_PATH", str(ROOT / "data" / "trading.db"))
)

BUSY_TIMEOUT_MS = 5000


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open SQLite with WAL journaling and a busy timeout for concurrent writers."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
