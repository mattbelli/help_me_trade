"""Strategy registry — db/strategies.py."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from config import settings as settings_mod
from db.accounts import default_execution_account
from db.base import connect
from db.multi_account import migrate_multi_account
from db.strategies import (
    get_strategy,
    init_strategy_tables,
    list_strategies,
    seed_default_strategies,
    update_strategy,
)


def _migrated_db(tmp_path: Path) -> Path:
    from db.order_executions import init_execution_tables
    from db.paper_orders import init_paper_order_tables
    from db.sqlite import init_db
    from db.trades import init_trade_tables

    db = tmp_path / "strategies.db"
    init_db(db)
    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    return db


def _seed_strat1_account(db: Path) -> str:
    now = datetime.now(UTC).isoformat()
    conn_id = "conn_alpaca_strat1"
    acct_id = "acct_alpaca_strat1"
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO broker_connections (
                id, broker, label, enabled, credentials_json, created_at
            ) VALUES (?, 'alpaca', 'STRAT1', 1, '{"env_suffix":"STRAT1"}', ?)
            """,
            (conn_id, now),
        )
        conn.execute(
            """
            INSERT INTO trade_accounts (
                id, label, broker, created_at, connection_id,
                enabled, execution_capable, is_default
            ) VALUES (?, 'STRAT1', 'alpaca', ?, ?, 1, 1, 0)
            """,
            (acct_id, now, conn_id),
        )
        conn.commit()
    return acct_id


def test_seed_idempotent(tmp_path: Path, monkeypatch) -> None:
    db = _migrated_db(tmp_path)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    seed_default_strategies(db_path=db)
    first = list_strategies(db_path=db)
    assert {s["id"] for s in first} == {"dip_buy", "dip_refined"}

    seed_default_strategies(db_path=db)
    assert {s["id"] for s in list_strategies(db_path=db)} == {"dip_buy", "dip_refined"}


def test_seed_carries_legacy_settings(tmp_path: Path, monkeypatch) -> None:
    db = _migrated_db(tmp_path)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    settings_mod.apply_settings_patch(
        {
            "trading": {
                "dip_strategy_enabled": True,
                "dip_strategy_notional_usd": 1200,
            }
        }
    )

    seed_default_strategies(db_path=db)
    row = get_strategy("dip_buy", db_path=db)
    assert row is not None
    assert row["enabled"] is True
    assert row["notional_usd"] == 1200.0
    assert row["exit_mode"] == "auto"
    assert row["params"]["oversold_3d_max"] == -5.0
    assert row["params"]["stop_pct"] == -3.0


def test_seed_binds_strat1_when_present(tmp_path: Path, monkeypatch) -> None:
    db = _migrated_db(tmp_path)
    strat1_id = _seed_strat1_account(db)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    seed_default_strategies(db_path=db)
    row = get_strategy("dip_buy", db_path=db)
    assert row is not None
    assert row["account_id"] == strat1_id


def test_seed_binds_default_without_strat1(tmp_path: Path, monkeypatch) -> None:
    db = _migrated_db(tmp_path)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    seed_default_strategies(db_path=db)
    default = default_execution_account(db_path=db)
    row = get_strategy("dip_buy", db_path=db)
    assert row is not None
    assert default is not None
    assert row["account_id"] == default["id"]


def test_list_get_update(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    init_strategy_tables(db)
    now = datetime.now(UTC).isoformat()
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO strategies (
                id, name, enabled, account_id, notional_usd, exit_mode,
                params_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "test_strat",
                "Test",
                0,
                None,
                500,
                "propose",
                json.dumps({"side": "long"}),
                now,
                now,
            ),
        )
        conn.commit()

    all_rows = list_strategies(db_path=db)
    assert len(all_rows) == 1
    assert all_rows[0]["enabled"] is False
    assert all_rows[0]["params"] == {"side": "long"}

    enabled_only = list_strategies(enabled_only=True, db_path=db)
    assert enabled_only == []

    row = get_strategy("test_strat", db_path=db)
    assert row is not None
    assert row["name"] == "Test"

    updated = update_strategy(
        "test_strat",
        enabled=True,
        notional_usd=800,
        exit_mode="auto",
        params={"side": "short"},
        db_path=db,
    )
    assert updated["enabled"] is True
    assert updated["notional_usd"] == 800.0
    assert updated["exit_mode"] == "auto"
    assert updated["params"] == {"side": "short"}
    assert updated["updated_at"] >= now


def test_update_strategy_validates(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    seed_default_strategies(db_path=db)

    with pytest.raises(ValueError, match="exit_mode"):
        update_strategy("dip_buy", exit_mode="invalid", db_path=db)

    with pytest.raises(ValueError, match="notional_usd"):
        update_strategy("dip_buy", notional_usd=-1, db_path=db)

    with pytest.raises(ValueError, match="unknown strategy"):
        update_strategy(f"missing_{uuid.uuid4().hex[:8]}", enabled=True, db_path=db)
