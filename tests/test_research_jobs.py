"""R1a research-jobs module — registration parity + restart-safe dedup."""

from __future__ import annotations

import functools
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from api import jobs
from api import research_jobs as rj
from db import lane_stats

_ET = ZoneInfo("America/New_York")

RESEARCH_JOB_NAMES = {
    "setup_scan_nightly",
    "options_flow",
    "momentum_bars_refresh",
    "volume_profile_refresh",
    "reflection",
}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    jobs.JOBS.clear()
    jobs._name_index.clear()
    db = tmp_path / "lane_stats.db"
    monkeypatch.setattr(rj, "bump", functools.partial(lane_stats.bump, db_path=db))
    monkeypatch.setattr(rj, "day", functools.partial(lane_stats.day, db_path=db))
    monkeypatch.setattr(rj, "_options_flow_ran_dates", set())


def test_register_research_jobs_registers_exactly_five() -> None:
    rj.register_research_jobs()
    names = [j.name for j in jobs.JOBS]
    assert sorted(names) == sorted(RESEARCH_JOB_NAMES)

    by_name = {j.name: j for j in jobs.JOBS}
    # Boot-first semantics preserved for the two 24h jobs (design §4b): without
    # run_on_start, run_job's sleep-first ordering means a 24h job never runs
    # on a box that restarts more often than daily.
    assert by_name["momentum_bars_refresh"].run_on_start is True
    assert by_name["volume_profile_refresh"].run_on_start is True
    assert by_name["reflection"].run_on_start is False
    assert by_name["setup_scan_nightly"].run_on_start is False
    assert by_name["setup_scan_nightly"].interval_s == 900.0


def test_flow_eod_dedup_survives_restart() -> None:
    today = datetime.now(_ET).date()
    assert rj._should_options_flow_scan(today) is True
    rj._mark_options_flow_scanned(today)
    assert rj._should_options_flow_scan(today) is False

    # Simulate a restart: the in-memory set is gone, the lane_stats day counter
    # survives — the pre-R1a in-memory-only dedup re-scanned here.
    rj._options_flow_ran_dates.clear()
    assert rj._should_options_flow_scan(today) is False


def test_flow_eod_dedup_scans_on_a_fresh_day() -> None:
    rj._mark_options_flow_scanned(date(2026, 7, 17))
    assert rj._should_options_flow_scan(date(2026, 7, 18)) is True


@pytest.mark.asyncio
async def test_reflection_runs_at_most_once_per_et_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock = AsyncMock()
    monkeypatch.setattr(rj, "reflection_once", mock)
    await rj._reflection_once_daily()
    await rj._reflection_once_daily()
    assert mock.await_count == 1


@pytest.mark.asyncio
async def test_reflection_failure_does_not_mark_the_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock = AsyncMock(side_effect=RuntimeError("llm down"))
    monkeypatch.setattr(rj, "reflection_once", mock)
    with pytest.raises(RuntimeError):
        await rj._reflection_once_daily()
    # The day is only marked after success — the next pass retries.
    monkeypatch.setattr(rj, "reflection_once", AsyncMock())
    await rj._reflection_once_daily()
    assert rj.reflection_once.await_count == 1
