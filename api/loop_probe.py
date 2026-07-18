"""Event-loop stall probe — catches the blocker red-handed instead of via journal archaeology.

Two heartbeats, one watchdog thread:

- An on-loop heartbeat task stamps a monotonic timestamp 4x/s. A daemon
  watchdog THREAD (a stalled loop cannot observe itself) checks staleness;
  past the threshold it logs every thread's stack, marking the loop thread.
  A sync call blocking the loop shows up as the culprit frame under
  ``[EVENT LOOP]``; a GIL convoy shows the loop parked in epoll while worker
  threads burn CPU; a DB-lock pileup shows workers inside sqlite3.
- An executor heartbeat awaits a no-op on the default executor (the same
  pool ``asyncio.to_thread`` uses) and logs when the round-trip lags —
  pool saturation is invisible to the loop heartbeat.

Grep keys: ``loop_probe: event-loop stall``, ``loop_probe: executor lag``.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

logger = logging.getLogger("api.loop_probe")

_BEAT_INTERVAL_S = 0.25
_WATCH_INTERVAL_S = 0.5
_REDUMP_COOLDOWN_S = 60.0
_EXEC_BEAT_INTERVAL_S = 5.0
_EXEC_LAG_THRESHOLD_S = 3.0
_STACK_FRAMES = 10


def _noop() -> None:
    return None


class LoopProbe:
    def __init__(self, *, stall_threshold_s: float = 2.0) -> None:
        self._stall_threshold_s = stall_threshold_s
        self._last_beat = time.monotonic()
        self._loop_thread_id: int | None = None
        self._stall_started: float | None = None
        self._last_dump = 0.0
        self._stopping = threading.Event()

    def start(self) -> list[asyncio.Task]:
        """Arm the probe. Must be called on the running event loop; returns the
        heartbeat tasks so the caller's shutdown path cancels them."""
        asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._last_beat = time.monotonic()
        tasks = [
            asyncio.create_task(self._heartbeat(), name="loop_probe_beat"),
            asyncio.create_task(self._executor_beat(), name="loop_probe_exec"),
        ]
        threading.Thread(
            target=self._watch, name="loop-probe-watchdog", daemon=True
        ).start()
        logger.info(
            "loop_probe armed (stall threshold %.1fs)", self._stall_threshold_s
        )
        return tasks

    def stop(self) -> None:
        self._stopping.set()

    async def _heartbeat(self) -> None:
        while True:
            self._last_beat = time.monotonic()
            await asyncio.sleep(_BEAT_INTERVAL_S)

    async def _executor_beat(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(_EXEC_BEAT_INTERVAL_S)
            started = time.monotonic()
            await loop.run_in_executor(None, _noop)
            lag = time.monotonic() - started
            if lag >= _EXEC_LAG_THRESHOLD_S:
                logger.warning(
                    "loop_probe: executor lag %.1fs — to_thread pool saturated "
                    "or GIL convoy",
                    lag,
                )

    def _watch(self) -> None:
        while not self._stopping.wait(_WATCH_INTERVAL_S):
            now = time.monotonic()
            stale = now - self._last_beat
            if stale >= self._stall_threshold_s:
                if self._stall_started is None:
                    # Backdate to the last good beat so duration is honest.
                    self._stall_started = now - stale
                if now - self._last_dump >= _REDUMP_COOLDOWN_S:
                    self._last_dump = now
                    logger.warning(
                        "loop_probe: event-loop stall %.1fs and counting — "
                        "thread stacks:\n%s",
                        stale,
                        self._format_stacks(),
                    )
            elif self._stall_started is not None:
                duration = now - self._stall_started
                self._stall_started = None
                self._last_dump = 0.0
                logger.warning(
                    "loop_probe: event-loop stall RECOVERED after %.1fs", duration
                )

    def _format_stacks(self) -> str:
        frames = sys._current_frames()
        names = {t.ident: t.name for t in threading.enumerate()}
        chunks: list[str] = []
        for ident, frame in frames.items():
            if ident == threading.get_ident():
                continue
            marker = " [EVENT LOOP]" if ident == self._loop_thread_id else ""
            stack = "".join(traceback.format_stack(frame, limit=_STACK_FRAMES))
            chunks.append(
                f"--- {names.get(ident, '?')} (tid {ident}){marker} ---\n{stack}"
            )
        return "\n".join(chunks)
