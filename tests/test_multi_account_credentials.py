"""Per-connection Alpaca credentials, registration, factory, and poll (Phase B sub-step 1)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brokers import factory as broker_factory
from brokers.base import Account
from config.settings import (
    alpaca_config,
    alpaca_config_for_suffix,
    clear_settings_cache,
    discover_alpaca_credential_suffixes,
)
from db.accounts import (
    get_account,
    get_connection,
    get_default_account,
    list_accounts,
    reconcile_account_external_id,
)
from db.base import connect
from db.multi_account import migrate_multi_account
from db.order_executions import init_execution_tables
from db.paper_orders import init_paper_order_tables
from db.sqlite import init_db
from db.trades import init_trade_tables


def _account(*, account_id: str = "broker-acct-1") -> Account:
    return Account(
        id=account_id,
        status="ACTIVE",
        cash=10_000.0,
        buying_power=10_000.0,
        portfolio_value=20_000.0,
    )


def _init_db(tmp_path: Path) -> Path:
    db = tmp_path / "multi_cred.db"
    init_db(db)
    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    return db


@pytest.fixture(autouse=True)
def _reset_broker_cache() -> None:
    broker_factory.clear_broker_cache()
    yield
    broker_factory.clear_broker_cache()


def test_alpaca_config_for_suffix_uses_primary_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APCA_API_KEY_ID", "primary-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "primary-secret")
    monkeypatch.setenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    clear_settings_cache()

    primary = alpaca_config()
    assert alpaca_config_for_suffix(None) == primary
    assert alpaca_config_for_suffix("") == primary


def test_alpaca_config_for_suffix_reads_suffixed_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APCA_API_KEY_ID", "primary-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "primary-secret")
    monkeypatch.setenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("APCA_DATA_FEED", "iex")
    monkeypatch.setenv("APCA_API_KEY_ID_STRAT1", "strat1-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY_STRAT1", "strat1-secret")
    monkeypatch.setenv("APCA_API_BASE_URL_STRAT1", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("APCA_DATA_FEED_STRAT1", "sip")
    clear_settings_cache()

    cfg = alpaca_config_for_suffix("STRAT1")
    assert cfg["api_key_id"] == "strat1-key"
    assert cfg["api_secret_key"] == "strat1-secret"
    assert cfg["api_base_url"] == "https://paper-api.alpaca.markets"
    assert cfg["data_feed"] == "sip"


def test_discover_alpaca_credential_suffixes_detects_complete_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APCA_API_KEY_ID_STRAT1", "k1")
    monkeypatch.setenv("APCA_API_SECRET_KEY_STRAT1", "s1")
    monkeypatch.setenv("APCA_API_BASE_URL_STRAT1", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("APCA_API_KEY_ID_PARTIAL", "k2")
    clear_settings_cache()

    assert discover_alpaca_credential_suffixes() == ["STRAT1"]


def test_migration_registers_suffixed_connection_and_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APCA_API_KEY_ID_STRAT1", "k1")
    monkeypatch.setenv("APCA_API_SECRET_KEY_STRAT1", "s1")
    monkeypatch.setenv("APCA_API_BASE_URL_STRAT1", "https://paper-api.alpaca.markets")
    clear_settings_cache()

    db = tmp_path / "register.db"
    migrate_multi_account(db)

    default_before = get_default_account("alpaca", db_path=db)
    assert default_before is not None

    conn = get_connection("conn_alpaca_strat1", db_path=db)
    assert conn is not None
    assert conn["broker"] == "alpaca"
    assert conn["label"] == "STRAT1"
    assert json.loads(conn["credentials_json"]) == {"env_suffix": "STRAT1"}

    acct = get_account("acct_alpaca_strat1", db_path=db)
    assert acct is not None
    assert acct["connection_id"] == "conn_alpaca_strat1"
    assert acct["execution_capable"] is True
    assert acct["is_default"] is False
    assert acct["broker_external_id"] is None

    default_after = get_default_account("alpaca", db_path=db)
    assert default_after["id"] == default_before["id"]

    migrate_multi_account(db)
    alpaca_accounts = list_accounts(broker="alpaca", db_path=db)
    assert len(alpaca_accounts) == 2


def test_get_broker_for_connection_and_account_use_suffix_creds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("config.settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setenv("APCA_API_KEY_ID", "primary-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "primary-secret")
    monkeypatch.setenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("APCA_API_KEY_ID_STRAT1", "strat1-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY_STRAT1", "strat1-secret")
    monkeypatch.setenv("APCA_API_BASE_URL_STRAT1", "https://paper-api.alpaca.markets")
    clear_settings_cache()

    db = _init_db(tmp_path)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None

    captured: list[dict[str, str] | None] = []

    def fake_from_settings(cfg: dict[str, str] | None = None) -> MagicMock:
        captured.append(cfg)
        return MagicMock()

    monkeypatch.setattr(
        "brokers.factory.AlpacaAdapter.from_settings",
        fake_from_settings,
    )
    monkeypatch.setattr(
        "db.accounts.get_account",
        lambda account_id, db_path=None: get_account(account_id, db_path=db),
    )
    monkeypatch.setattr(
        "db.accounts.get_connection",
        lambda connection_id, db_path=None: get_connection(connection_id, db_path=db),
    )

    default_adapter = broker_factory.get_broker_for_account(default["id"])
    strat1_adapter = broker_factory.get_broker_for_account("acct_alpaca_strat1")
    cached_again = broker_factory.get_broker_for_account("acct_alpaca_strat1")

    assert strat1_adapter is cached_again
    assert broker_factory.get_broker_for_account(default["id"]) is default_adapter
    assert len(captured) == 2
    assert captured[0]["api_key_id"] == "primary-key"
    assert captured[1]["api_key_id"] == "strat1-key"

    conn = get_connection(default["connection_id"], db_path=db)
    assert conn is not None
    again = broker_factory.get_broker_for_connection(conn)
    assert again is default_adapter


def _poll_run_rows(db: Path) -> list[str | None]:
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT account_id FROM poll_runs ORDER BY id"
        ).fetchall()
    return [row["account_id"] for row in rows]


@pytest.mark.asyncio
async def test_poll_per_account_reconciles_and_guards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from api import main
    from db import accounts as accounts_mod
    from db import base as db_base
    from db import sqlite as sqlite_mod

    monkeypatch.setenv("APCA_API_KEY_ID", "primary-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "primary-secret")
    monkeypatch.setenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("APCA_API_KEY_ID_STRAT1", "strat1-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY_STRAT1", "strat1-secret")
    monkeypatch.setenv("APCA_API_BASE_URL_STRAT1", "https://paper-api.alpaca.markets")
    clear_settings_cache()

    db = _init_db(tmp_path)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(accounts_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(
        main,
        "list_accounts",
        lambda enabled_only=False: list_accounts(enabled_only=enabled_only, db_path=db),
    )
    monkeypatch.setattr(
        main,
        "get_connection",
        lambda connection_id: get_connection(connection_id, db_path=db),
    )
    monkeypatch.setattr(
        main,
        "reconcile_account_external_id",
        lambda account_id, external_id: reconcile_account_external_id(
            account_id, external_id, db_path=db
        ),
    )

    adapters: dict[str, MagicMock] = {}

    def fake_get_broker_for_account(account_id: str) -> MagicMock:
        if account_id not in adapters:
            mock = MagicMock()
            if account_id == default["id"]:
                mock.afetch_snapshot = AsyncMock(
                    return_value=(_account(account_id="default-broker-id"), [], [])
                )
            else:
                mock.afetch_snapshot = AsyncMock(
                    return_value=(_account(account_id="strat1-broker-id"), [], [])
                )
            adapters[account_id] = mock
        return adapters[account_id]

    def save_with_test_db(*args, **kwargs):
        kwargs["db_path"] = db
        return sqlite_mod.save_snapshot(*args, **kwargs)

    reconcile_open = AsyncMock()
    reconcile_pending = AsyncMock()

    with (
        patch.object(main, "get_broker_for_account", side_effect=fake_get_broker_for_account),
        patch.object(main, "connection_is_configured", return_value=True),
        patch.object(main, "save_snapshot", side_effect=save_with_test_db),
        patch.object(main, "compute_and_save_snapshot_analytics") as analytics_mock,
        patch.object(main, "count_pending_paper_executions", return_value=1),
        patch.object(main, "count_submitted_executions", return_value=1),
        patch.object(main, "reconcile_open_executions", reconcile_open),
        patch.object(main, "reconcile_pending_executions", reconcile_pending),
        patch.object(main, "get_latest_snapshot", return_value=None),
        patch.object(main, "get_enabled_broker_names", return_value=["alpaca"]),
    ):
        await main._poll_once()

    assert {call.kwargs["account_id"] for call in reconcile_pending.await_args_list} == {
        default["id"],
        "acct_alpaca_strat1",
    }
    assert {call.kwargs["account_id"] for call in reconcile_open.await_args_list} == {
        default["id"],
        "acct_alpaca_strat1",
    }
    assert all(call.args == ("alpaca",) for call in reconcile_pending.await_args_list)
    assert all(call.args == ("alpaca",) for call in reconcile_open.await_args_list)
    analytics_mock.assert_called_once_with("alpaca")

    # _poll_once polls accounts concurrently (asyncio.gather) → order is
    # non-deterministic; compare as a multiset.
    assert sorted(_poll_run_rows(db)) == sorted([default["id"], "acct_alpaca_strat1"])
    assert (
        get_account(default["id"], db_path=db)["broker_external_id"]
        == "default-broker-id"
    )
    assert (
        get_account("acct_alpaca_strat1", db_path=db)["broker_external_id"]
        == "strat1-broker-id"
    )

    with connect(db) as conn:
        conn.execute(
            """
            UPDATE trade_accounts
            SET broker_external_id = 'expected-id'
            WHERE id = ?
            """,
            ("acct_alpaca_strat1",),
        )
        conn.commit()

    caplog.clear()
    with (
        patch.object(main, "get_broker_for_account", side_effect=fake_get_broker_for_account),
        patch.object(main, "connection_is_configured", return_value=True),
        patch.object(main, "save_snapshot", side_effect=save_with_test_db) as save_mock,
        patch.object(main, "compute_and_save_snapshot_analytics"),
        patch.object(main, "count_pending_paper_executions", return_value=0),
        patch.object(main, "count_submitted_executions", return_value=0),
        patch.object(main, "get_latest_snapshot", return_value=None),
        patch.object(main, "get_enabled_broker_names", return_value=["alpaca"]),
    ):
        await main._poll_once()

    assert "Snapshot account id mismatch" in caplog.text
    # STRAT1's id-guard mismatch skips its 2nd save → 2 default rows + 1 strat1,
    # in non-deterministic order (concurrent poll); compare as a multiset.
    assert sorted(_poll_run_rows(db)) == sorted([
        default["id"],
        "acct_alpaca_strat1",
        default["id"],
    ])
    assert save_mock.call_count == 1
