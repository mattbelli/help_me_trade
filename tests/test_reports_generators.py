"""LLM-authored + generalized reports — generators, scheduler jobs, routes.

The LLM is always mocked (deterministic JSON) — these tests never call a
provider. They cover: the ``levels`` generalization, the brief generators
(morning/evening/on_request), fail-soft error reports, the generic per-day
dedup, the scheduler job runner, config job normalization, and the routes.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

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

_BRIEF_JSON = json.dumps(
    {
        "title": "Morning Brief",
        "summary": "Quiet overnight tape; watch SPY POC.",
        "metrics": [{"label": "Bias", "value": "Neutral"}],
        "sections": [
            {"heading": "Overnight", "body": "Futures flat into the open."},
            {"heading": "Plan", "body": "Wait for a reclaim of POC."},
        ],
        "risks": ["CPI print at 8:30"],
        "next_steps": ["Watch the open"],
    }
)


class _FakeLLM:
    provider = "mock"

    def __init__(self, payload: str) -> None:
        self._payload = payload

    async def acomplete(self, messages, *, max_tokens: int = 1024) -> str:
        return self._payload


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


def _patch_db_path(monkeypatch: pytest.MonkeyPatch, db: Path) -> None:
    monkeypatch.setattr("db.base.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.reports.DEFAULT_DB_PATH", db)
    import signals.reports as signals_reports

    monkeypatch.setattr(signals_reports, "DEFAULT_DB_PATH", db)


@pytest.fixture
def reports_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = _init_db(tmp_path)
    _patch_db_path(monkeypatch, db)
    monkeypatch.setenv("TRADING_DB_PATH", str(db))
    return db


@pytest.fixture
def reports_client(reports_db: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("api.auth.API_TOKEN", "pytest-test-token")
    from api.main import app

    with TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"X-API-Token": "pytest-test-token"},
    ) as client:
        yield client


def _mock_llm(monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
    monkeypatch.setattr("signals.reports.get_llm", lambda *_a, **_k: _FakeLLM(payload))
    # Keep symbol-context gathering hermetic (no provider / network).
    monkeypatch.setattr(
        "ai.perception.context.build_symbol_context",
        AsyncMock(return_value={"symbol": "SPY", "quote": {"price": 732.0}}),
    )


# --- levels generalization -----------------------------------------------------


@pytest.mark.asyncio
async def test_generate_levels_report_multi_symbol(
    reports_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "signals.reports.compute_session_levels",
        AsyncMock(return_value=_SAMPLE_LEVELS),
    )
    from signals.reports import generate_levels_report

    report = await generate_levels_report(
        ["spy", "qqq"], db_path=reports_db, et_date=date(2026, 6, 25)
    )
    assert report is not None
    assert report["kind"] == "levels"
    assert report["format"] == "text"
    assert "SPY:" in report["body"] and "QQQ:" in report["body"]
    assert report["payload"]["dedup_key"] == "SPY,QQQ"


@pytest.mark.asyncio
async def test_generate_levels_report_none_when_no_data(
    reports_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "signals.reports.compute_session_levels",
        AsyncMock(return_value=None),
    )
    from signals.reports import generate_levels_report

    report = await generate_levels_report(["SPY"], db_path=reports_db)
    assert report is None


# --- LLM briefs ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_morning_brief_stores_html(
    reports_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_llm(monkeypatch, _BRIEF_JSON)
    from signals.reports import generate_morning_brief_report

    report = await generate_morning_brief_report(
        ["SPY"], db_path=reports_db, et_date=date(2026, 6, 25)
    )
    assert report["kind"] == "morning_brief"
    assert report["format"] == "html"
    assert "<html" in report["body"].lower()
    assert "Futures flat into the open." in report["body"]
    assert report["payload"]["error"] is False
    assert report["payload"]["et_date"] == "2026-06-25"
    assert report["payload"]["dedup_key"] == "morning_brief"


@pytest.mark.asyncio
async def test_generate_evening_brief_stores_html(
    reports_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_llm(monkeypatch, _BRIEF_JSON)
    from signals.reports import generate_evening_brief_report

    report = await generate_evening_brief_report(["SPY"], db_path=reports_db)
    assert report["kind"] == "evening_brief"
    assert report["format"] == "html"
    assert report["symbol"] == "PORTFOLIO"


@pytest.mark.asyncio
async def test_on_request_report_uses_topic(
    reports_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_llm(monkeypatch, _BRIEF_JSON)
    from signals.reports import generate_on_request_report

    report = await generate_on_request_report(
        "How is NVDA set up?", ["NVDA"], db_path=reports_db
    )
    assert report["kind"] == "on_request"
    assert report["symbol"] == "NVDA"
    assert report["format"] == "html"


@pytest.mark.asyncio
async def test_brief_failsoft_on_llm_error(
    reports_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _BoomLLM:
        provider = "mock"

        async def acomplete(self, *_a, **_k):
            raise RuntimeError("provider down")

    monkeypatch.setattr("signals.reports.get_llm", lambda *_a, **_k: _BoomLLM())
    monkeypatch.setattr(
        "ai.perception.context.build_symbol_context",
        AsyncMock(return_value={"symbol": "SPY"}),
    )
    from signals.reports import generate_morning_brief_report

    report = await generate_morning_brief_report(["SPY"], db_path=reports_db)
    # Never raises — stores an error-state report instead.
    assert report["format"] == "html"
    assert report["payload"]["error"] is True
    assert "could not be generated" in report["body"]


# --- generic dedup -------------------------------------------------------------


def test_report_exists_for_day_generic(reports_db: Path) -> None:
    from db.reports import report_exists_for_day, save_report

    day = date(2026, 6, 25)
    assert report_exists_for_day("morning_brief", day, db_path=reports_db) is False

    save_report(
        "morning_brief",
        "MARKET",
        "Morning Brief",
        "<html></html>",
        {"et_date": day.isoformat(), "dedup_key": "morning_brief"},
        format="html",
        db_path=reports_db,
    )
    assert report_exists_for_day("morning_brief", day, db_path=reports_db) is True
    assert (
        report_exists_for_day(
            "morning_brief", day, dedup_key="morning_brief", db_path=reports_db
        )
        is True
    )
    assert (
        report_exists_for_day(
            "levels", day, dedup_key="SPY,QQQ", db_path=reports_db
        )
        is False
    )


# --- scheduler job runner ------------------------------------------------------


@pytest.mark.asyncio
async def test_run_scheduled_report_generates_and_dedups(
    reports_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_llm(monkeypatch, _BRIEF_JSON)
    monkeypatch.setattr("api.reports_routes._morning_report_window", lambda *_a, **_k: True)
    notify_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("signals.notify.notify", notify_mock)

    from api.reports_routes import _run_scheduled_report

    job = {"kind": "morning_brief", "hour_et": 8, "symbols": ["SPY"], "enabled": True}
    await _run_scheduled_report(job)
    notify_mock.assert_awaited_once()

    notify_mock.reset_mock()
    await _run_scheduled_report(job)  # same ET day → deduped, no push
    notify_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_scheduled_report_skips_disabled(
    reports_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("api.reports_routes._morning_report_window", lambda *_a, **_k: True)
    notify_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("signals.notify.notify", notify_mock)

    from api.reports_routes import _run_scheduled_report

    await _run_scheduled_report(
        {"kind": "morning_brief", "hour_et": 8, "symbols": [], "enabled": False}
    )
    notify_mock.assert_not_awaited()


# --- config job normalization --------------------------------------------------


def test_normalize_report_jobs_filters_unknown_kinds() -> None:
    from config.settings import _normalize_report_jobs

    jobs = _normalize_report_jobs(
        [
            {"kind": "morning_brief", "hour_et": 30, "symbols": ["spy"], "enabled": 1},
            {"kind": "bogus_kind", "hour_et": 8},
            "not-a-dict",
        ]
    )
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "morning_brief"
    assert jobs[0]["hour_et"] == 23  # clamped
    assert jobs[0]["symbols"] == ["SPY"]
    assert jobs[0]["enabled"] is True


def test_reports_config_has_jobs_list() -> None:
    from config.settings import reports_config

    cfg = reports_config()
    assert "jobs" in cfg
    assert isinstance(cfg["jobs"], list)


# --- routes --------------------------------------------------------------------


def test_route_generate_levels_and_get_format(
    reports_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "signals.reports.compute_session_levels",
        AsyncMock(return_value=_SAMPLE_LEVELS),
    )
    created = reports_client.post(
        "/api/reports/generate", json={"kind": "levels", "symbols": ["SPY", "QQQ"]}
    )
    assert created.status_code == 200
    body = created.json()
    assert body["kind"] == "levels"
    assert body["format"] == "text"

    fetched = reports_client.get(f"/api/reports/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["format"] == "text"

    listed = reports_client.get("/api/reports").json()["reports"]
    assert listed[0]["id"] == body["id"]
    assert "format" in listed[0]


def test_route_generate_morning_brief(
    reports_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_llm(monkeypatch, _BRIEF_JSON)
    created = reports_client.post(
        "/api/reports/generate", json={"kind": "morning_brief"}
    )
    assert created.status_code == 200
    body = created.json()
    assert body["kind"] == "morning_brief"
    assert body["format"] == "html"


def test_route_on_request_requires_topic(reports_client: TestClient) -> None:
    missing = reports_client.post(
        "/api/reports/generate", json={"kind": "on_request"}
    )
    assert missing.status_code == 400


def test_route_unsupported_kind(reports_client: TestClient) -> None:
    bad = reports_client.post(
        "/api/reports/generate", json={"kind": "weekly_summary"}
    )
    assert bad.status_code == 400
    assert "weekly_summary" in bad.json()["error"]
