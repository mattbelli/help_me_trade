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
    _VIX_SPIKE_DEFAULT_PARAMS,
    get_strategy,
    init_strategy_tables,
    list_strategies,
    seed_default_strategies,
    update_strategy,
)
from signals.strategy_kinds import is_portfolio_kind, strategy_kind


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
    assert {s["id"] for s in first} == {
        "dip_buy",
        "dip_refined",
        "steepyc_dip",
        "vix_spike",
        "earnings_drift",
        "session_node_spy",
        "momentum_sp500",
        "generals_dip",
        "vol_event",
        "book_deepseek",
        "book_fable",
        "book_panel",
    }

    seed_default_strategies(db_path=db)
    assert {s["id"] for s in list_strategies(db_path=db)} == {
        "dip_buy",
        "dip_refined",
        "steepyc_dip",
        "vix_spike",
        "earnings_drift",
        "session_node_spy",
        "momentum_sp500",
        "generals_dip",
        "vol_event",
        "book_deepseek",
        "book_fable",
        "book_panel",
    }


def test_seed_momentum_sp500_defaults(tmp_path: Path, monkeypatch) -> None:
    db = _migrated_db(tmp_path)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    seed_default_strategies(db_path=db)
    row = get_strategy("momentum_sp500", db_path=db)
    assert row is not None
    assert row["enabled"] is False
    assert row["account_id"] is None
    assert row["notional_usd"] == 50000.0
    assert row["exit_mode"] == "propose"
    assert row["entry_mode"] == "auto"
    assert row["alert_style"] == "summary"
    assert row["last_rebalanced_month"] is None
    assert row["universe"] == "sp500"
    assert row["params"] == {}
    assert strategy_kind("momentum_sp500") == "momentum"
    assert is_portfolio_kind("momentum_sp500") is True


def test_seed_steepyc_dip_defaults(tmp_path: Path, monkeypatch) -> None:
    db = _migrated_db(tmp_path)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    seed_default_strategies(db_path=db)
    row = get_strategy("steepyc_dip", db_path=db)
    assert row is not None
    assert row["enabled"] is False
    assert row["account_id"] is None
    assert row["notional_usd"] == 15000.0
    assert row["exit_mode"] == "auto"
    assert row["entry_mode"] == "auto"
    assert row["alert_style"] == "per_trade"
    assert row["universe"] == "sp500"
    assert row["params"] == {
        "side": "long",
        "healthy_required": True,
        "oversold_3d_max": -5.0,
        "mkt_down_3d_max": -1.5,
        "yc_min_spread": 0.5,
        "stop_pct": None,
        "first_target_pct": None,
        "trail_pct": None,
        "max_holding_days": 10,
    }
    assert strategy_kind("steepyc_dip") == "dip"
    assert is_portfolio_kind("steepyc_dip") is False


def test_seed_vix_spike_defaults(tmp_path: Path, monkeypatch) -> None:
    db = _migrated_db(tmp_path)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    seed_default_strategies(db_path=db)
    row = get_strategy("vix_spike", db_path=db)
    assert row is not None
    assert row["enabled"] is False
    assert row["account_id"] is None
    assert row["notional_usd"] == 7500.0
    assert row["exit_mode"] == "auto"
    assert row["entry_mode"] == "auto"
    assert row["alert_style"] == "per_trade"
    assert row["universe"] == "spy"
    assert row["params"] == _VIX_SPIKE_DEFAULT_PARAMS
    assert strategy_kind("vix_spike") == "vix_spike"
    assert is_portfolio_kind("vix_spike") is False


def test_alert_style_defaults_and_migrates(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    seed_default_strategies(db_path=db)
    row = get_strategy("dip_buy", db_path=db)
    assert row is not None
    assert row["alert_style"] == "per_trade"
    assert row["last_rebalanced_month"] is None

    updated = update_strategy("dip_buy", alert_style="summary", db_path=db)
    assert updated["alert_style"] == "summary"
    assert get_strategy("dip_buy", db_path=db)["alert_style"] == "summary"

    init_strategy_tables(db)
    with connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(strategies)")}
    assert "alert_style" in cols
    assert "last_rebalanced_month" in cols


def test_seed_session_node_spy_defaults(tmp_path: Path, monkeypatch) -> None:
    db = _migrated_db(tmp_path)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    seed_default_strategies(db_path=db)
    row = get_strategy("session_node_spy", db_path=db)
    assert row is not None
    assert row["id"] == "session_node_spy"
    assert row["enabled"] is False
    assert row["account_id"] is None
    assert row["notional_usd"] == 500.0
    assert row["exit_mode"] == "propose"
    assert row["entry_mode"] == "propose"
    assert row["params"] == {"symbol": "SPY"}


def test_seed_earnings_drift_defaults(tmp_path: Path, monkeypatch) -> None:
    db = _migrated_db(tmp_path)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    seed_default_strategies(db_path=db)
    row = get_strategy("earnings_drift", db_path=db)
    assert row is not None
    assert row["enabled"] is False
    assert row["account_id"] is None
    assert row["exit_mode"] == "propose"
    assert row["params"]["exit_lead_td"] == 1


def test_seed_backfills_earnings_when_dips_exist(tmp_path: Path, monkeypatch) -> None:
    """Regression: pre-existing dip rows must not block earnings_drift back-fill."""
    db = _migrated_db(tmp_path)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    init_strategy_tables(db)
    now = datetime.now(UTC).isoformat()
    with connect(db) as conn:
        for sid, name in (("dip_buy", "Dip-Buy"), ("dip_refined", "Dip-Buy + S/R filter")):
            conn.execute(
                """
                INSERT INTO strategies (
                    id, name, enabled, account_id, notional_usd, exit_mode,
                    params_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sid, name, 0, None, 500, "auto", "{}", now, now),
            )
        conn.commit()

    assert get_strategy("earnings_drift", db_path=db) is None
    seed_default_strategies(db_path=db)
    row = get_strategy("earnings_drift", db_path=db)
    assert row is not None
    assert row["enabled"] is False
    assert row["exit_mode"] == "propose"


def test_seed_backfills_earnings_after_dips_only_seed(tmp_path: Path, monkeypatch) -> None:
    """Regression: dip rows pre-existing must not block earnings_drift back-fill."""
    db = _migrated_db(tmp_path)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    seed_default_strategies(db_path=db)
    assert get_strategy("earnings_drift", db_path=db) is not None

    with connect(db) as conn:
        conn.execute("DELETE FROM strategies WHERE id = 'earnings_drift'")
        conn.commit()

    assert get_strategy("earnings_drift", db_path=db) is None
    seed_default_strategies(db_path=db)
    row = get_strategy("earnings_drift", db_path=db)
    assert row is not None
    assert row["exit_mode"] == "propose"


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

    with pytest.raises(ValueError, match="entry_mode"):
        update_strategy("dip_buy", entry_mode="invalid", db_path=db)

    with pytest.raises(ValueError, match="alert_style"):
        update_strategy("dip_buy", alert_style="invalid", db_path=db)

    with pytest.raises(ValueError, match="notional_usd"):
        update_strategy("dip_buy", notional_usd=-1, db_path=db)

    with pytest.raises(ValueError, match="unknown strategy"):
        update_strategy(f"missing_{uuid.uuid4().hex[:8]}", enabled=True, db_path=db)


def test_entry_mode_defaults_and_migrates(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    seed_default_strategies(db_path=db)
    row = get_strategy("dip_buy", db_path=db)
    assert row is not None
    assert row["entry_mode"] == "propose"

    updated = update_strategy("dip_buy", entry_mode="auto", db_path=db)
    assert updated["entry_mode"] == "auto"
    assert get_strategy("dip_buy", db_path=db)["entry_mode"] == "auto"

    init_strategy_tables(db)
    with connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(strategies)")}
    assert "entry_mode" in cols


def test_notional_pct_defaults_null_and_migrates(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    seed_default_strategies(db_path=db)

    # Seeded rows size off notional_usd — percent sizing is strictly opt-in.
    row = get_strategy("dip_buy", db_path=db)
    assert row is not None
    assert row["notional_pct"] is None

    init_strategy_tables(db)
    with connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(strategies)")}
    assert "notional_pct" in cols


def test_notional_pct_round_trips_and_clears(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    seed_default_strategies(db_path=db)

    updated = update_strategy("dip_buy", notional_pct=2.5, db_path=db)
    assert updated["notional_pct"] == 2.5
    assert get_strategy("dip_buy", db_path=db)["notional_pct"] == 2.5
    # list_strategies must surface it too (its own SELECT column list).
    listed = next(s for s in list_strategies(db_path=db) if s["id"] == "dip_buy")
    assert listed["notional_pct"] == 2.5

    # Omitting the field leaves it alone (sentinel default, not None).
    untouched = update_strategy("dip_buy", notional_usd=600, db_path=db)
    assert untouched["notional_pct"] == 2.5

    # An explicit None clears it — back to static-dollar sizing.
    cleared = update_strategy("dip_buy", notional_pct=None, db_path=db)
    assert cleared["notional_pct"] is None


def test_notional_pct_validation(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    seed_default_strategies(db_path=db)

    for bad in (0, -1, 100.1, 1000):
        with pytest.raises(ValueError, match="notional_pct"):
            update_strategy("dip_buy", notional_pct=bad, db_path=db)

    # Boundary: 100% of equity is allowed (all-in is a legitimate config).
    assert update_strategy("dip_buy", notional_pct=100, db_path=db)["notional_pct"] == 100.0
