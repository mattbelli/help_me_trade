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


# --- the session gate: an ET date is scanned only once ITS session is history ---
#
# The old gate admitted market_session()'s "overnight" label, which spans ET
# midnight. The 00:1x ET tick scanned the ET day that had just begun, wrote zero
# rows, and consumed the day's dedup slot, so the real post-close run was skipped
# as already-ran — options_flow_daily froze at 2026-07-17 for five sessions.


def _at(monkeypatch: pytest.MonkeyPatch, when: datetime) -> None:
    """Pin rj's ET wall clock to `when`."""

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return when.astimezone(tz) if tz else when

    monkeypatch.setattr(rj, "datetime", _Clock)


def _stub_scan(monkeypatch: pytest.MonkeyPatch, summary: dict) -> list[str]:
    """Stub the broker + EOD scan; returns the list of scanned event dates."""
    scanned: list[str] = []

    def fake_scan(_broker, *, symbols, event_date):
        scanned.append(event_date)
        return summary

    broker = type("Broker", (), {"get_option_bars": lambda *_a, **_k: {}})()
    monkeypatch.setattr(rj, "get_broker", lambda _name: broker)
    monkeypatch.setattr(rj, "all_watchlist_symbols", lambda: ["AAPL"])
    monkeypatch.setattr("signals.options_flow.scan.run_eod_scan", fake_scan)
    monkeypatch.setattr("signals.options_flow.outcomes.fill_outcomes", lambda _b: {})
    return scanned


@pytest.mark.asyncio
async def test_flow_eod_overnight_tick_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """00:19 ET on a trading day: scan nothing AND leave the day's slot unspent."""
    scanned = _stub_scan(monkeypatch, {"underlyings": 40, "events": 0, "daily_rows": 0})
    _at(monkeypatch, datetime(2026, 7, 23, 0, 19, tzinfo=_ET))  # Thursday, pre-open

    await rj._options_flow_eod_once()

    assert scanned == []
    # The slot must still be available for the real post-close run.
    assert rj._should_options_flow_scan(date(2026, 7, 23)) is True


@pytest.mark.asyncio
async def test_flow_eod_runs_after_the_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same day, after the close: the scan runs and marks the day. Pins that the
    gate — not a missing broker — is what stopped the overnight tick above."""
    scanned = _stub_scan(monkeypatch, {"underlyings": 40, "events": 900, "daily_rows": 13_000})
    _at(monkeypatch, datetime(2026, 7, 23, 16, 5, tzinfo=_ET))

    await rj._options_flow_eod_once()

    assert scanned == ["2026-07-23"]
    assert rj._should_options_flow_scan(date(2026, 7, 23)) is False


@pytest.mark.asyncio
async def test_flow_eod_empty_result_does_not_burn_the_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run that writes no baseline rows must retry, not mark the day done."""
    _stub_scan(monkeypatch, {"underlyings": 40, "events": 0, "daily_rows": 0})
    _at(monkeypatch, datetime(2026, 7, 23, 16, 5, tzinfo=_ET))

    await rj._options_flow_eod_once()

    assert rj._should_options_flow_scan(date(2026, 7, 23)) is True


@pytest.mark.asyncio
async def test_flow_eod_skips_the_weekend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Saturday is after-close by definition but never had a session to scan."""
    scanned = _stub_scan(monkeypatch, {"underlyings": 0, "events": 0, "daily_rows": 0})
    _at(monkeypatch, datetime(2026, 7, 25, 18, 0, tzinfo=_ET))  # Saturday

    await rj._options_flow_eod_once()

    assert scanned == []


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
