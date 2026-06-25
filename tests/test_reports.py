"""Morning levels reports — db storage, generation, API, and schedule predicate."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

_ET = ZoneInfo("America/New_York")
_SAMPLE_LEVELS = {
    "symbol": "SPY",
    "session_date": "2026-06-24",
    "levels": [
        {"name": "POC", "price": 732.25, "kind": "poc"},
        {"name": "VAH", "price": 736.21, "kind": "resistance"},
        {"name": "VAL", "price": 730.93, "kind": "support"},
        {"name": "PDH", "price": 739.95, "kind": "prior_high"},
        {"name": "PDL", "price": 730.84, "kind": "prior_low"},
    ],
}


def _init_db(tmp_path: Path) -> Path:
    from db.multi_account import migrate_multi_account
    from db.order_executions import init_execution_tables
    from db.paper_orders import init_paper_order_tables
    from db.reports import init_reports_tables
    from db.sqlite import init_db
    from db.strategies import init_strategy_tables, seed_default_strategies
    from db.trades import init_trade_tables

    db = tmp_path / "reports.db"
    init_db(db)
    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    init_strategy_tables(db)
    seed_default_strategies(db_path=db)
    init_reports_tables(db)
    return db


@pytest.fixture
def reports_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = _init_db(tmp_path)
    monkeypatch.setattr("db.base.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.reports.DEFAULT_DB_PATH", db)
    monkeypatch.setenv("TRADING_DB_PATH", str(db))
    return db


def _patch_db_path(monkeypatch: pytest.MonkeyPatch, db: Path) -> None:
    monkeypatch.setattr("db.base.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.reports.DEFAULT_DB_PATH", db)
    import signals.reports as signals_reports

    monkeypatch.setattr(signals_reports, "DEFAULT_DB_PATH", db)


@pytest.fixture
def reports_client(
    reports_db: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    _patch_db_path(monkeypatch, reports_db)
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def test_save_list_get_round_trip(reports_db: Path) -> None:
    from db.reports import get_report, list_reports, save_report

    row = save_report(
        "morning_levels",
        "SPY",
        "Morning Levels · SPY",
        "PDH 739.95 / POC 732.25 ★",
        {"et_date": "2026-06-25", **_SAMPLE_LEVELS},
        db_path=reports_db,
    )
    assert row["id"]
    assert row["kind"] == "morning_levels"
    assert row["symbol"] == "SPY"

    listed = list_reports(limit=10, db_path=reports_db)
    assert len(listed) == 1
    assert listed[0]["id"] == row["id"]

    fetched = get_report(row["id"], db_path=reports_db)
    assert fetched is not None
    assert fetched["title"] == "Morning Levels · SPY"
    assert fetched["payload"]["session_date"] == "2026-06-24"


def test_morning_levels_one_per_et_day(reports_db: Path) -> None:
    from db.reports import morning_levels_exists_for_day, save_report

    et_day = date(2026, 6, 25)
    assert morning_levels_exists_for_day("SPY", et_day, db_path=reports_db) is False

    save_report(
        "morning_levels",
        "SPY",
        "Morning Levels · SPY",
        "body",
        {"et_date": et_day.isoformat(), **_SAMPLE_LEVELS},
        db_path=reports_db,
    )
    assert morning_levels_exists_for_day("SPY", et_day, db_path=reports_db) is True
    assert morning_levels_exists_for_day("SPY", date(2026, 6, 26), db_path=reports_db) is False


@pytest.mark.asyncio
async def test_generate_morning_levels_report_builds_body(
    reports_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "signals.reports.compute_session_levels",
        AsyncMock(return_value=_SAMPLE_LEVELS),
    )
    monkeypatch.setattr(
        "signals.reports.short_selling_allowed",
        lambda: False,
    )
    monkeypatch.setattr(
        "signals.reports.get_strategy",
        lambda *_a, **_k: {"enabled": True},
    )

    from signals.reports import generate_morning_levels_report

    report = await generate_morning_levels_report(
        "SPY", db_path=reports_db, et_date=date(2026, 6, 25)
    )
    assert report is not None
    assert report["title"] == "Morning Levels · SPY"
    assert "PDH 739.95" in report["body"]
    assert "POC 732.25 ★" in report["body"]
    assert "Session-Node: armed · long-only" in report["body"]
    assert report["payload"]["strategy_enabled"] is True
    assert report["payload"]["shorts_on"] is False
    assert report["payload"]["et_date"] == "2026-06-25"


@pytest.mark.asyncio
async def test_generate_morning_levels_report_none_when_no_levels(
    reports_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "signals.reports.compute_session_levels",
        AsyncMock(return_value=None),
    )

    from signals.reports import generate_morning_levels_report

    report = await generate_morning_levels_report("SPY", db_path=reports_db)
    assert report is None


def test_api_generate_list_get(
    reports_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "signals.reports.compute_session_levels",
        AsyncMock(return_value=_SAMPLE_LEVELS),
    )
    monkeypatch.setattr(
        "signals.reports.short_selling_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "signals.reports.get_strategy",
        lambda *_a, **_k: {"enabled": False},
    )

    bad = reports_client.post(
        "/api/reports/generate",
        json={"kind": "weekly_summary"},
    )
    assert bad.status_code == 400

    created = reports_client.post(
        "/api/reports/generate",
        json={"kind": "morning_levels", "symbol": "SPY"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["kind"] == "morning_levels"
    assert body["symbol"] == "SPY"
    assert "disarmed" in body["body"]
    assert "long+short" in body["body"]

    listed = reports_client.get("/api/reports")
    assert listed.status_code == 200
    reports = listed.json()["reports"]
    assert len(reports) >= 1
    assert reports[0]["id"] == body["id"]

    fetched = reports_client.get(f"/api/reports/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == body["title"]

    missing = reports_client.get("/api/reports/does-not-exist")
    assert missing.status_code == 404


def test_morning_report_window_predicate() -> None:
    from api.main import _morning_report_window

    inside = datetime(2026, 6, 25, 8, 10, tzinfo=_ET)
    edge = datetime(2026, 6, 25, 8, 14, tzinfo=_ET)
    outside = datetime(2026, 6, 25, 8, 15, tzinfo=_ET)
    wrong_hour = datetime(2026, 6, 25, 9, 5, tzinfo=_ET)

    assert _morning_report_window(inside, 8) is True
    assert _morning_report_window(edge, 8) is True
    assert _morning_report_window(outside, 8) is False
    assert _morning_report_window(wrong_hour, 8) is False


@pytest.mark.asyncio
async def test_morning_levels_once_dedups_same_day(
    reports_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_db_path(monkeypatch, reports_db)
    fixed = datetime(2026, 6, 25, 8, 5, tzinfo=_ET)

    class _FixedDatetime:
        @staticmethod
        def now(tz=None):
            if tz is not None:
                return fixed.astimezone(tz)
            return fixed.replace(tzinfo=None)

    monkeypatch.setattr("api.main.datetime", _FixedDatetime)
    monkeypatch.setattr(
        "signals.reports.compute_session_levels",
        AsyncMock(return_value=_SAMPLE_LEVELS),
    )
    monkeypatch.setattr(
        "signals.reports.get_strategy",
        lambda *_a, **_k: {"enabled": True},
    )
    monkeypatch.setattr(
        "signals.reports.short_selling_allowed",
        lambda: False,
    )
    monkeypatch.setattr("api.main._MORNING_REPORT_ENV_ENABLED", True)
    monkeypatch.setattr(
        "api.main.reports_config",
        lambda: {"morning_levels_enabled": True, "hour_et": 8},
    )

    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("signals.push.send_alert_push", push_mock)

    from api.main import _morning_levels_report_once

    await _morning_levels_report_once()
    push_mock.assert_awaited_once()

    push_mock.reset_mock()
    await _morning_levels_report_once()
    push_mock.assert_not_awaited()
