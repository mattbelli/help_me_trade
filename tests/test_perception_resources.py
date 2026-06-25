"""Perception layer — generic resource query."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai.perception.resources import list_resources, query
from db import base as db_base
from db.chart_features import create_price_alert, init_chart_features
from db.paper_orders import create_pending_proposal, init_paper_order_tables
from db.reports import init_reports_tables, save_report
from db.strategies import init_strategy_tables, seed_default_strategies


@pytest.fixture
def resources_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "resources.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.chart_features.DEFAULT_DB_PATH", db)
    init_chart_features(db)
    init_strategy_tables(db)
    seed_default_strategies(db_path=db)
    init_paper_order_tables(db)
    init_reports_tables(db)
    create_price_alert(symbol="AAPL", price=200.0, note="test")
    create_pending_proposal(
        proposal={"symbol": "NVDA", "side": "buy", "qty": 1, "order_type": "limit"},
        intent_fields={
            "symbol": "NVDA",
            "side": "buy",
            "qty": 1,
            "order_type": "limit",
            "limit_price": 900.0,
            "broker": "alpaca",
            "asset_class": "us_equity",
            "multiplier": "1",
        },
        session_id=None,
        ttl_seconds=3600,
        summary="Buy NVDA limit",
        db_path=db,
    )
    save_report(
        "morning_levels",
        "SPY",
        "SPY morning levels",
        "Body text for preview testing.",
        {"et_date": "2026-06-25"},
        db_path=db,
    )
    return db


def test_list_resources_catalog() -> None:
    catalog = list_resources()
    assert set(catalog) == {
        "watchlists",
        "strategies",
        "price_alerts",
        "proposals",
        "reports",
    }
    assert "description" in catalog["strategies"]
    assert "filters" in catalog["strategies"]


@pytest.mark.asyncio
async def test_query_unknown_resource() -> None:
    result = await query("positions")
    assert result["error"] == "unknown resource"
    assert "strategies" in result["available"]


@pytest.mark.asyncio
async def test_query_strategies(
    resources_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", resources_db)
    result = await query("strategies", db_path=resources_db)
    assert result["resource"] == "strategies"
    assert result["count"] >= 1
    assert "enabled" in result["rows"][0]


@pytest.mark.asyncio
async def test_query_watchlists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ai.perception.resources.get_watchlists_state",
        lambda: {
            "active_id": "main",
            "lists": [{"id": "main", "name": "Main", "symbols": ["AAPL", "NVDA"]}],
        },
    )
    result = await query("watchlists")
    assert result["count"] == 1
    assert result["rows"][0]["symbols"] == ["AAPL", "NVDA"]


@pytest.mark.asyncio
async def test_query_price_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ai.perception.resources.list_price_alerts",
        lambda **kw: [
            {
                "id": 1,
                "symbol": "AAPL",
                "price": 200.0,
                "direction": "cross",
                "note": "test",
                "repeat": 0,
                "triggered": 0,
            }
        ]
        if kw.get("symbol") == "AAPL"
        else [],
    )
    result = await query("price_alerts", {"symbol": "AAPL"})
    assert result["count"] == 1
    assert result["rows"][0]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_query_proposals(
    resources_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", resources_db)
    result = await query("proposals", db_path=resources_db)
    assert result["count"] == 1
    assert result["rows"][0]["symbol"] == "NVDA"
    assert result["rows"][0]["status"] == "pending_phone"


@pytest.mark.asyncio
async def test_query_reports(
    resources_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", resources_db)
    result = await query("reports", {"limit": 5}, db_path=resources_db)
    assert result["count"] == 1
    assert result["rows"][0]["kind"] == "morning_levels"
    assert "preview" in result["rows"][0]


@pytest.mark.asyncio
async def test_query_fail_soft_on_reader_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = {
        "description": "broken",
        "filters": {},
        "read": MagicMock(side_effect=RuntimeError("boom")),
    }
    monkeypatch.setitem(
        __import__("ai.perception.resources", fromlist=["RESOURCES"]).RESOURCES,
        "watchlists",
        entry,
    )
    result = await query("watchlists")
    assert result["resource"] == "watchlists"
    assert "boom" in result["error"]
    assert result["rows"] == []
