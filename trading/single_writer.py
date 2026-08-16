"""Process-level fence for the execution chokepoint.

``execution_service._submit_lock()`` is a per-event-loop asyncio.Lock: it
serializes validate→submit inside ONE process so two orders can't both read a
notional sum that excludes the other and slip under the cap. A second backend
process on the same ledger (``uvicorn --workers 2``, a stray dev server pointed
at data/trading.db) gets its own lock and reintroduces that race silently.

This turns the "single-process assumption" from a comment into an invariant:
the backend holds a kernel ``flock`` on ``<ledger>.writer.lock`` for its whole
life, and a second holder refuses to boot. Advisory only — it fences the
backend, not ad-hoc tools (``tools/fire_test_entry.py`` deliberately submits
from a second process against paper while the backend runs).
"""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path

from db.base import DEFAULT_DB_PATH

logger = logging.getLogger(__name__)

_handle = None  # kept open for the life of the process; closing releases the lock


def lock_path(db_path: os.PathLike | str | None = None) -> Path:
    return Path(db_path or DEFAULT_DB_PATH).with_suffix(".writer.lock")


def acquire(db_path: os.PathLike | str | None = None) -> Path:
    """Take the exclusive writer fence or raise RuntimeError if another
    process already holds it. Idempotent within one process."""
    global _handle
    path = lock_path(db_path)
    if _handle is not None:
        return path
    fh = open(path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        other = fh.read().strip() or "?"
        fh.close()
        raise RuntimeError(
            f"another backend (pid {other}) already holds {path} — refusing to "
            "start a second writer on the same ledger (see trading/single_writer.py)"
        ) from None
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    _handle = fh
    logger.info("single-writer fence held: %s", path)
    return path


def release() -> None:
    global _handle
    if _handle is not None:
        _handle.close()  # closing the fd drops the flock
        _handle = None
