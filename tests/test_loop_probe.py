"""Loop-probe stall detection: a blocked event loop must produce a stack dump."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from api.loop_probe import LoopProbe


async def _wait_for(predicate, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return predicate()


@pytest.mark.asyncio
async def test_stall_dumps_loop_thread_stack_and_logs_recovery(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="api.loop_probe")
    probe = LoopProbe(stall_threshold_s=0.3)
    tasks = probe.start()
    try:
        await asyncio.sleep(0.4)  # let heartbeats establish a baseline
        time.sleep(1.2)  # block the loop — the exact failure mode under test

        def _seen() -> bool:
            text = caplog.text
            return "event-loop stall" in text and "RECOVERED" in text

        assert await _wait_for(_seen), caplog.text
        stall_text = caplog.text
        # The dump must finger this frame as the blocker on the loop thread.
        assert "[EVENT LOOP]" in stall_text
        assert "time.sleep(1.2)" in stall_text
    finally:
        probe.stop()
        for task in tasks:
            task.cancel()


@pytest.mark.asyncio
async def test_healthy_loop_stays_silent(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="api.loop_probe")
    probe = LoopProbe(stall_threshold_s=0.5)
    tasks = probe.start()
    try:
        deadline = time.monotonic() + 1.3
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert "loop_probe" not in caplog.text
    finally:
        probe.stop()
        for task in tasks:
            task.cancel()
