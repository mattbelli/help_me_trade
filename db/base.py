"""Shared SQLite connection helpers."""

from __future__ import annotations

import logging
import os
import sqlite3
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(
    os.environ.get("TRADING_DB_PATH", str(ROOT / "data" / "trading.db"))
)

BUSY_TIMEOUT_MS = 5000

# Write transactions holding the lock longer than this are logged with the
# caller's stack. WAL allows exactly ONE writer: a single slow transaction
# convoys every other process on the file (measured 2026-07-27: sweep-event
# inserts failing at 5s busy_timeout behind an unidentified ~7s holder).
# 0 disables. Diagnostic cost when quiet: two time.monotonic() calls per txn.
SLOW_TXN_MS = int(os.environ.get("HMT_SLOW_TXN_MS", "1500"))


class _TimedConnection(sqlite3.Connection):
    """sqlite3.Connection that logs write transactions held past SLOW_TXN_MS."""

    _txn_started: float | None = None

    def _stamp(self) -> None:
        # in_transaction only flips True on DML (legacy isolation), so read-only
        # work never stamps; stamping AFTER execute returns measures the HOLD,
        # not the wait for the lock.
        if self._txn_started is None and self.in_transaction:
            self._txn_started = time.monotonic()

    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        cur = super().execute(*args, **kwargs)
        self._stamp()
        return cur

    def executemany(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        cur = super().executemany(*args, **kwargs)
        self._stamp()
        return cur

    def executescript(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        # executescript commits implicitly; time it directly.
        t0 = time.monotonic()
        cur = super().executescript(*args, **kwargs)
        self._report(t0)
        return cur

    def commit(self) -> None:
        started = self._txn_started
        super().commit()
        if started is not None:
            self._txn_started = None
            self._report(started)

    def rollback(self) -> None:
        self._txn_started = None
        super().rollback()

    def _report(self, started: float) -> None:
        if not SLOW_TXN_MS:
            return
        held_ms = (time.monotonic() - started) * 1000.0
        if held_ms < SLOW_TXN_MS:
            return
        # Innermost non-db/base frames identify the owner without a full dump.
        frames = [
            f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno}:{f.name}"
            for f in traceback.extract_stack(limit=12)
            if "db/base.py" not in f.filename
        ]
        logger.warning(
            "slow write txn: held %.0fms (>%dms) — %s",
            held_ms,
            SLOW_TXN_MS,
            " <- ".join(reversed(frames[-6:])),
        )


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open SQLite with WAL journaling and a busy timeout for concurrent writers."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0, factory=_TimedConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
