"""Job runner supervisor, watchdog, and /health v2 registry tests."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from api import jobs
from api.jobs import Job, mark_alive, register_job, run_job

_ET = ZoneInfo("America/New_York")

AUDIT_LOOP_NAMES = [
    "poll",
    "signal_scan",
    "session_node",
    "alert_symbol",
    "reflection",
    "trade_sync",
    "proposal_sweeper",
    "idea_tracker",
    "earnings_refresh",
    "regime_refresh",
    "momentum_bars_refresh",
    "volume_profile_refresh",
    "rvol_alert",
    "options_flow",
    "options_flow_intraday",
    "earnings_alert",
    "schwab_health",
    "schwab_snapshot",
    "db_retention",
    "reports_scheduler",
    "bars_prewarm",
    "equity_tape_flush",
]


@pytest.fixture(autouse=True)
def _reset_jobs_registry() -> None:
    import api.main as main_mod

    jobs.JOBS.clear()
    jobs._name_index.clear()
    jobs._watchdog_stale_last_date.clear()
    jobs._watchdog_backup_last_date = None
    main_mod._background_jobs_registered = False


@pytest.mark.asyncio
async def test_supervisor_marks_success_and_resets_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def ok_fn() -> None:
        nonlocal calls
        calls += 1

    job = register_job(Job(name="ok", fn=ok_fn, interval_s=1.0))
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if calls >= 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(jobs.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(jobs.random, "uniform", lambda _a, _b: 1.0)

    with pytest.raises(asyncio.CancelledError):
        await run_job(job)

    assert calls == 1
    assert job.last_success is not None
    assert job.last_success_at is not None
    assert job.consecutive_failures == 0
    assert job.last_error is None


@pytest.mark.asyncio
async def test_supervisor_counts_failures_and_stays_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fail_fn() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError(f"boom {calls}")

    job = register_job(Job(name="fail", fn=fail_fn, interval_s=2.0))
    sleep_calls = 0

    async def fake_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 4:
            raise asyncio.CancelledError()

    monkeypatch.setattr(jobs.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(jobs.random, "uniform", lambda _a, _b: 1.0)

    with pytest.raises(asyncio.CancelledError):
        await run_job(job)

    assert calls == 3
    assert job.consecutive_failures == 3
    assert job.last_error == "boom 3"
    assert job.last_success is None


@pytest.mark.asyncio
async def test_supervisor_skips_fn_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1

    job = register_job(
        Job(
            name="gated",
            fn=fn,
            interval_s=1.0,
            enabled=lambda: False,
        )
    )
    sleep_calls = 0

    async def fake_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(jobs.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_job(job)

    assert calls == 0


@pytest.mark.asyncio
async def test_supervisor_backoff_grows_and_resets_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("not yet")

    job = register_job(Job(name="backoff", fn=fn, interval_s=10.0))
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) >= 4:
            raise asyncio.CancelledError()

    monkeypatch.setattr(jobs.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(jobs.random, "uniform", lambda _a, _b: 1.0)

    with pytest.raises(asyncio.CancelledError):
        await run_job(job)

    assert delays[0] == pytest.approx(10.0)
    assert delays[1] == pytest.approx(15.0)
    assert delays[2] == pytest.approx(20.0)
    assert delays[3] == pytest.approx(10.0)
    assert job.consecutive_failures == 0


@pytest.mark.asyncio
async def test_watchdog_notifies_once_per_stale_job_per_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = register_job(
        Job(name="stale_job", fn=AsyncMock(), interval_s=60.0, enabled=lambda: True)
    )
    register_job(
        Job(name="watchdog", fn=jobs._watchdog_once, interval_s=600.0)
    )
    stale.last_success = time.monotonic() - 5000

    notify_calls: list[dict] = []

    async def fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "watchdog-test"}

    fixed_now = datetime(2026, 6, 24, 12, 0, tzinfo=_ET)

    monkeypatch.setattr("signals.notify.notify", fake_notify)
    monkeypatch.setattr(jobs, "datetime", type("DT", (), {"now": staticmethod(lambda tz=None: fixed_now)}))

    await jobs._watchdog_once()
    await jobs._watchdog_once()

    assert len(notify_calls) == 1
    assert notify_calls[0]["kind"] == "job_watchdog"
    assert notify_calls[0]["severity"] == "critical"
    assert notify_calls[0]["group_key"] == "job_stale_stale_job"


@pytest.mark.asyncio
async def test_watchdog_skips_fresh_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh = register_job(
        Job(name="fresh_job", fn=AsyncMock(), interval_s=60.0, enabled=lambda: True)
    )
    register_job(
        Job(name="watchdog", fn=jobs._watchdog_once, interval_s=600.0)
    )
    mark_alive("fresh_job")
    fresh.last_success = time.monotonic()

    notify_calls: list[dict] = []

    async def fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "none"}

    monkeypatch.setattr("signals.notify.notify", fake_notify)
    await jobs._watchdog_once()
    assert notify_calls == []


@pytest.mark.asyncio
async def test_watchdog_notifies_on_stale_backup_once_per_day(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    register_job(
        Job(name="watchdog", fn=jobs._watchdog_once, interval_s=600.0)
    )
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old = backup_dir / "trading-old.db"
    old.write_bytes(b"x")
    old_mtime = time.time() - (27 * 3600)
    import os

    os.utime(old, (old_mtime, old_mtime))

    notify_calls: list[dict] = []

    async def fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "backup-test"}

    fixed_now = datetime(2026, 6, 24, 12, 0, tzinfo=_ET)

    monkeypatch.setattr(jobs, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr("signals.notify.notify", fake_notify)
    monkeypatch.setattr(jobs, "datetime", type("DT", (), {"now": staticmethod(lambda tz=None: fixed_now)}))

    await jobs._watchdog_once()
    await jobs._watchdog_once()

    assert len(notify_calls) == 1
    assert notify_calls[0]["group_key"] == "backup_stale"
    assert "backup is stale" in notify_calls[0]["title"].lower()


def test_health_v2_includes_jobs_and_db_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "trading.db"
    db_path.write_bytes(b"12345")

    monkeypatch.setattr("api.main.DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr("api.main.newest_backup_age_s", lambda: None)

    from api.main import app

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "poll_interval_seconds" in body
    assert "last_poll_age_seconds" in body
    assert "poll_healthy" in body
    assert body["db_size_bytes"] == 5
    assert body["newest_backup_age_s"] is None
    job_names = {row["name"] for row in body["jobs"]}
    assert AUDIT_LOOP_NAMES[0] in job_names
    assert "watchdog" in job_names


def test_registry_includes_all_audit_loop_names() -> None:
    from api.main import _register_background_jobs

    _register_background_jobs()
    registered = {job.name for job in jobs.JOBS if job.name != "watchdog"}
    assert registered == set(AUDIT_LOOP_NAMES)
