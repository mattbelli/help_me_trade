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
    "earnings_release",
    "regime_refresh",
    "momentum_bars_refresh",
    "volume_profile_refresh",
    "fusion_day_volume_refresh",
    "rvol_alert",
    "attention_ledger",
    "attention_ledger_eod",
    "attention_investigator",
    "options_flow",
    "options_flow_intraday",
    "earnings_alert",
    "schwab_health",
    "schwab_snapshot",
    "schwab_trade_sync",
    "db_retention",
    "reports_scheduler",
    "equity_tape_flush",
    "filings_edgar",
    "filings_congress",
    "setup_scan_nightly",
    "fusion_intake",
    "fusion_atr_refresh",
    "fusion_levels_refresh",
    "fusion_reason",
    "fusion_lifecycle",
    "fusion_lifecycle_eod",
    "pipeline_audit",
    "regime_sleeves",
    "social_buzz",
    "news_stream",
    # LLM paper books (research/book_driver.py) — nightly analyst cycles + dead-man.
    "book_deepseek",
    "book_matt",
    "book_deadman",
]


@pytest.fixture(autouse=True)
def _reset_jobs_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import api.background_jobs as bg_jobs

    jobs.JOBS.clear()
    jobs._name_index.clear()
    jobs._watchdog_stale_last_date.clear()
    jobs._watchdog_backup_last_date = None
    jobs._watchdog_off_box_last_date = None
    jobs._watchdog_last_closed_mono = None
    bg_jobs._background_jobs_registered = False
    # The watchdog reads BACKUP_DIR off the real filesystem. Without this, every
    # test here passes or fails on whatever /home/mbelli/backups happens to hold
    # on the machine running it — green on serv, red on CI. Default everything
    # to fresh so a watchdog test only pages when the test itself arranges it:
    # last_offsite silences the off-site block, trading-*.db the backup-stale block.
    isolated = tmp_path / "_jobs_backups"
    isolated.mkdir()
    (isolated / "last_offsite").touch()
    (isolated / "trading-isolated.db").touch()
    monkeypatch.setattr(jobs, "BACKUP_DIR", isolated)


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
async def test_watchdog_market_hours_job_not_paged_at_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The overnight gap is not lag: last success yesterday + market just
    opened must NOT page (the 07-20/07-23 false CRITICALs)."""
    job = register_job(
        Job(name="mh_job", fn=AsyncMock(), interval_s=60.0, market_hours_only=True)
    )
    register_job(Job(name="watchdog", fn=jobs._watchdog_once, interval_s=600.0))
    job.last_success = time.monotonic() - 35_000
    # Watchdog saw the market closed one tick ago; it is open now.
    jobs._watchdog_last_closed_mono = time.monotonic() - 300
    monkeypatch.setattr(jobs, "is_market_open", lambda: True)

    notify_calls: list[dict] = []

    async def fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "none"}

    monkeypatch.setattr("signals.notify.notify", fake_notify)
    await jobs._watchdog_once()
    assert notify_calls == []


@pytest.mark.asyncio
async def test_watchdog_market_hours_job_pages_when_stale_intra_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A job dead well INTO the open session still pages."""
    job = register_job(
        Job(name="mh_job", fn=AsyncMock(), interval_s=60.0, market_hours_only=True)
    )
    register_job(Job(name="watchdog", fn=jobs._watchdog_once, interval_s=600.0))
    job.last_success = time.monotonic() - 5_000
    jobs._watchdog_last_closed_mono = time.monotonic() - 5_000
    monkeypatch.setattr(jobs, "is_market_open", lambda: True)

    notify_calls: list[dict] = []

    async def fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "page"}

    monkeypatch.setattr("signals.notify.notify", fake_notify)
    await jobs._watchdog_once()
    assert len(notify_calls) == 1
    assert notify_calls[0]["group_key"] == "job_stale_mh_job"


@pytest.mark.asyncio
async def test_watchdog_closed_pass_stamps_clock_and_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = register_job(
        Job(name="mh_job", fn=AsyncMock(), interval_s=60.0, market_hours_only=True)
    )
    register_job(Job(name="watchdog", fn=jobs._watchdog_once, interval_s=600.0))
    job.last_success = time.monotonic() - 35_000
    monkeypatch.setattr(jobs, "is_market_open", lambda: False)

    notify_calls: list[dict] = []

    async def fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "none"}

    monkeypatch.setattr("signals.notify.notify", fake_notify)
    await jobs._watchdog_once()
    assert notify_calls == []
    assert jobs._watchdog_last_closed_mono is not None


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
    # Fresh off-site marker: isolates this test to the local-staleness block, so
    # the strict count below stays a real assertion about backup_stale alone.
    (backup_dir / "last_offsite").write_text("2026-06-24T03:30:00\n")

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


@pytest.mark.asyncio
async def test_watchdog_pages_when_off_site_marker_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    register_job(Job(name="watchdog", fn=jobs._watchdog_once, interval_s=600.0))
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()  # no last_offsite marker
    # A fresh local snapshot, so the (now fail-closed) backup-age block stays
    # quiet and the strict count below isolates the off-site block alone.
    (backup_dir / "trading-fresh.db").touch()

    notify_calls: list[dict] = []

    async def fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "off-box-test"}

    fixed_now = datetime(2026, 6, 24, 12, 0, tzinfo=_ET)
    monkeypatch.setattr(jobs, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr("signals.notify.notify", fake_notify)
    monkeypatch.setattr(jobs, "datetime", type("DT", (), {"now": staticmethod(lambda tz=None: fixed_now)}))

    await jobs._watchdog_once()

    assert len(notify_calls) == 1
    assert notify_calls[0]["group_key"] == "off_site_push_stale"
    assert notify_calls[0]["severity"] == "warning"
    assert "has not reported" in notify_calls[0]["title"]


@pytest.mark.asyncio
async def test_watchdog_pages_when_off_site_marker_older_than_48h(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    register_job(Job(name="watchdog", fn=jobs._watchdog_once, interval_s=600.0))
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    marker = backup_dir / "last_offsite"
    marker.write_text("2026-06-22T03:30:00\n")
    # Fresh local snapshot: isolates this to the off-site block, now that a
    # missing trading-*.db pages on its own (fail-closed).
    (backup_dir / "trading-fresh.db").touch()
    stale = time.time() - (61.2 * 3600)
    import os

    os.utime(marker, (stale, stale))

    notify_calls: list[dict] = []

    async def fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "off-box-test"}

    fixed_now = datetime(2026, 6, 24, 12, 0, tzinfo=_ET)
    monkeypatch.setattr(jobs, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr("signals.notify.notify", fake_notify)
    monkeypatch.setattr(jobs, "datetime", type("DT", (), {"now": staticmethod(lambda tz=None: fixed_now)}))

    await jobs._watchdog_once()

    assert len(notify_calls) == 1
    assert notify_calls[0]["group_key"] == "off_site_push_stale"
    assert notify_calls[0]["severity"] == "warning"
    assert "61.2h ago" in notify_calls[0]["body"]


@pytest.mark.asyncio
async def test_watchdog_silent_when_off_site_marker_fresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    register_job(Job(name="watchdog", fn=jobs._watchdog_once, interval_s=600.0))
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "last_offsite").write_text("2026-06-24T03:30:00\n")
    # A fresh local snapshot too, so a zero-notify result can only mean both
    # the backup-stale block and the off-site block stayed quiet.
    (backup_dir / "trading-fresh.db").write_bytes(b"x")

    notify_calls: list[dict] = []

    async def fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "off-box-test"}

    fixed_now = datetime(2026, 6, 24, 12, 0, tzinfo=_ET)
    monkeypatch.setattr(jobs, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr("signals.notify.notify", fake_notify)
    monkeypatch.setattr(jobs, "datetime", type("DT", (), {"now": staticmethod(lambda tz=None: fixed_now)}))

    await jobs._watchdog_once()

    assert notify_calls == []


@pytest.mark.asyncio
async def test_watchdog_pages_off_site_once_per_et_day(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    register_job(Job(name="watchdog", fn=jobs._watchdog_once, interval_s=600.0))
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    # Fresh local snapshot: the missing last_offsite marker is what this test is
    # about, so keep the (fail-closed) backup-age block out of the count.
    (backup_dir / "trading-fresh.db").touch()

    notify_calls: list[dict] = []

    async def fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "off-box-test"}

    fixed_now = datetime(2026, 6, 24, 12, 0, tzinfo=_ET)
    monkeypatch.setattr(jobs, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr("signals.notify.notify", fake_notify)
    monkeypatch.setattr(jobs, "datetime", type("DT", (), {"now": staticmethod(lambda tz=None: fixed_now)}))

    await jobs._watchdog_once()
    await jobs._watchdog_once()

    assert len(notify_calls) == 1
    assert notify_calls[0]["group_key"] == "off_site_push_stale"


def test_health_v2_includes_jobs_and_db_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "trading.db"
    db_path.write_bytes(b"12345")

    monkeypatch.setattr("api.main.DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr("api.main.newest_backup_age_s", lambda: None)
    monkeypatch.setattr("api.main.off_site_push_age_s", lambda: 3600.0)

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
    assert body["off_site_push_age_s"] == 3600.0
    job_names = {row["name"] for row in body["jobs"]}
    assert AUDIT_LOOP_NAMES[0] in job_names
    assert "watchdog" in job_names


def test_registry_includes_all_audit_loop_names() -> None:
    from api.background_jobs import _register_background_jobs

    _register_background_jobs()
    registered = {job.name for job in jobs.JOBS if job.name != "watchdog"}
    assert registered == set(AUDIT_LOOP_NAMES)


RESEARCH_JOB_NAMES = {
    "setup_scan_nightly",
    "options_flow",
    "momentum_bars_refresh",
    "volume_profile_refresh",
    "fusion_day_volume_refresh",
    "reflection",
}


def test_registry_skips_research_jobs_when_worker_external(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.background_jobs as bg_jobs

    monkeypatch.setattr(bg_jobs, "_RESEARCH_WORKER_EXTERNAL", True)
    bg_jobs._register_background_jobs()
    registered = {job.name for job in jobs.JOBS}
    assert registered & RESEARCH_JOB_NAMES == set()
    assert "poll" in registered  # the rest of the registry is intact
    assert "options_flow_intraday" in registered  # collector-seam job unaffected


def test_registry_has_no_duplicate_job_names() -> None:
    from api.background_jobs import _register_background_jobs

    _register_background_jobs()
    names = [job.name for job in jobs.JOBS]
    assert len(names) == len(set(names))


@pytest.mark.asyncio
async def test_run_on_start_executes_before_first_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1

    job = register_job(Job(name="boot", fn=fn, interval_s=3600.0, run_on_start=True))

    async def fake_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(jobs.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_job(job)

    assert calls == 1  # ran before any sleep


@pytest.mark.asyncio
async def test_default_job_sleeps_before_first_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1

    job = register_job(Job(name="sleep_first", fn=fn, interval_s=3600.0))

    async def fake_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(jobs.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_job(job)

    assert calls == 0


@pytest.mark.asyncio
async def test_watchdog_pages_when_local_backup_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty/missing backup dir must page, not pass.

    The old guard read `if backup_age is not None and backup_age > 26h`, so the
    one state worse than a stale backup — no readable trading-*.db at all —
    produced silence. Fresh off-site marker isolates this to the backup block.
    """
    register_job(Job(name="watchdog", fn=jobs._watchdog_once, interval_s=600.0))
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()  # deliberately EMPTY: no trading-*.db anywhere
    (backup_dir / "last_offsite").write_text("2026-06-24T03:30:00\n")

    notify_calls: list[dict] = []

    async def fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "unreadable-test"}

    fixed_now = datetime(2026, 6, 24, 12, 0, tzinfo=_ET)
    monkeypatch.setattr(jobs, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr("signals.notify.notify", fake_notify)
    monkeypatch.setattr(jobs, "datetime", type("DT", (), {"now": staticmethod(lambda tz=None: fixed_now)}))

    await jobs._watchdog_once()

    assert len(notify_calls) == 1
    assert notify_calls[0]["group_key"] == "backup_stale"
    assert notify_calls[0]["severity"] == "critical"
    assert "cannot be read" in notify_calls[0]["title"].lower()
