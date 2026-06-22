"""Strategy registry API routes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db.base import connect
from db.multi_account import migrate_multi_account
from db.strategies import init_strategy_tables, seed_default_strategies


@pytest.fixture
def auth_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-secret-token"
    monkeypatch.setattr("api.auth.API_TOKEN", token)
    return token


def _migrated_db(tmp_path: Path) -> Path:
    from db.order_executions import init_execution_tables
    from db.paper_orders import init_paper_order_tables
    from db.sqlite import init_db
    from db.trades import init_trade_tables

    db = tmp_path / "strategy_api.db"
    init_db(db)
    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    init_strategy_tables(db)
    seed_default_strategies(db_path=db)
    return db


def _read_only_account(db: Path) -> str:
    now = datetime.now(UTC).isoformat()
    acct_id = "acct_readonly"
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO trade_accounts (
                id, label, broker, created_at,
                enabled, execution_capable, is_default
            ) VALUES (?, 'Read Only', 'schwab', ?, 1, 0, 0)
            """,
            (acct_id, now),
        )
        conn.commit()
    return acct_id


@pytest.fixture
def strategy_api_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, auth_token: str
) -> TestClient:
    db = _migrated_db(tmp_path)
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setenv("TRADING_DB_PATH", str(db))
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.headers.update({"X-API-Token": auth_token})
        yield client


def test_get_lists_strategies(strategy_api_client: TestClient) -> None:
    response = strategy_api_client.get("/api/strategies")
    assert response.status_code == 200
    data = response.json()
    assert len(data["strategies"]) == 1
    assert data["strategies"][0]["id"] == "dip_buy"
    assert "account_label" in data["strategies"][0]


def test_put_updates_strategy(strategy_api_client: TestClient) -> None:
    list_resp = strategy_api_client.get("/api/strategies")
    default_account_id = list_resp.json()["strategies"][0]["account_id"]

    response = strategy_api_client.put(
        "/api/strategies/dip_buy",
        json={
            "enabled": True,
            "account_id": default_account_id,
            "notional_usd": 900,
            "exit_mode": "propose",
            "params": {"side": "long", "oversold_3d_max": -4.0},
        },
    )
    assert response.status_code == 200
    updated = response.json()["strategy"]
    assert updated["enabled"] is True
    assert updated["notional_usd"] == 900.0
    assert updated["exit_mode"] == "propose"
    assert updated["params"]["oversold_3d_max"] == -4.0


def test_put_rejects_invalid_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, auth_token: str
) -> None:
    db = _migrated_db(tmp_path)
    readonly_id = _read_only_account(db)
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setenv("TRADING_DB_PATH", str(db))
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.headers.update({"X-API-Token": auth_token})
        response = client.put(
            "/api/strategies/dip_buy",
            json={"account_id": readonly_id},
        )
    assert response.status_code == 400
    assert "execution-capable" in response.json()["detail"]
