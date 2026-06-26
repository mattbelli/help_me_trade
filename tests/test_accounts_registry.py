"""Account registry read-model and get_broker_for_account (sub-step 2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from brokers import factory as broker_factory
from brokers.errors import BrokerError
from db.accounts import (
    account_is_execution_capable,
    default_execution_account,
    get_account,
    get_connection,
    get_default_account,
    list_accounts,
    list_connections,
    resolve_account_broker,
)
from db.base import connect
from db.multi_account import migrate_multi_account


def _seed_schwab_accounts(db: Path, *, count: int = 3) -> list[str]:
    labels = ["Individual", "Roth IRA", "Joint"][:count]
    now = datetime.now(UTC).isoformat()
    ids: list[str] = []
    with connect(db) as conn:
        for label in labels:
            account_id = f"acct_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO trade_accounts (id, label, broker, created_at)
                VALUES (?, ?, 'schwab', ?)
                """,
                (account_id, label, now),
            )
            ids.append(account_id)
        conn.commit()
    return ids


def _migrated_db(tmp_path: Path, *, schwab_count: int = 0) -> Path:
    db = tmp_path / "accounts.db"
    if schwab_count:
        from db.order_executions import init_execution_tables
        from db.paper_orders import init_paper_order_tables
        from db.sqlite import init_db
        from db.trades import init_trade_tables

        init_db(db)
        init_trade_tables(db)
        init_execution_tables(db)
        init_paper_order_tables(db)
        _seed_schwab_accounts(db, count=schwab_count)
    migrate_multi_account(db)
    return db


def test_default_account_after_migration(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)

    default = get_default_account("alpaca", db_path=db)
    assert default is not None
    assert default["is_default"] is True
    assert default["execution_capable"] is True
    assert default["enabled"] is True

    execution_default = default_execution_account(db_path=db)
    assert execution_default is not None
    assert execution_default["id"] == default["id"]


def test_get_account_known_and_unknown(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None

    row = get_account(default["id"], db_path=db)
    assert row is not None
    assert row["id"] == default["id"]
    assert row["broker"] == "alpaca"

    assert get_account("acct_nonexistent", db_path=db) is None


def test_list_accounts_includes_default_and_filters_schwab(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path, schwab_count=3)

    all_accounts = list_accounts(db_path=db)
    alpaca_defaults = [a for a in all_accounts if a["broker"] == "alpaca"]
    assert len(alpaca_defaults) >= 1
    assert any(a["is_default"] for a in alpaca_defaults)

    schwab_accounts = list_accounts(broker="schwab", db_path=db)
    assert len(schwab_accounts) == 3
    for account in schwab_accounts:
        assert account["broker"] == "schwab"
        assert account["execution_capable"] is False


def test_account_is_execution_capable(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path, schwab_count=1)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None

    schwab = list_accounts(broker="schwab", db_path=db)[0]

    assert account_is_execution_capable(default["id"], db_path=db) is True
    assert account_is_execution_capable(schwab["id"], db_path=db) is False
    assert account_is_execution_capable("acct_missing", db_path=db) is False

    disabled_id = f"acct_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO trade_accounts (
                id, label, broker, created_at, connection_id,
                enabled, execution_capable, is_default
            ) VALUES (?, 'Disabled Alpaca', 'alpaca', ?, ?, 0, 1, 0)
            """,
            (
                disabled_id,
                now,
                default["connection_id"],
            ),
        )
        conn.commit()

    assert account_is_execution_capable(disabled_id, db_path=db) is False


def test_list_connections_and_enabled_filter(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path, schwab_count=1)

    connections = list_connections(db_path=db)
    brokers = {c["broker"] for c in connections}
    assert "alpaca" in brokers
    assert "schwab" in brokers

    with connect(db) as conn:
        conn.execute(
            "UPDATE broker_connections SET enabled = 0 WHERE broker = 'schwab'"
        )
        conn.commit()

    enabled_only = list_connections(enabled_only=True, db_path=db)
    assert all(c["enabled"] for c in enabled_only)
    assert all(c["broker"] != "schwab" for c in enabled_only)


def test_get_connection_by_id(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None

    connection = get_connection(default["connection_id"], db_path=db)
    assert connection is not None
    assert connection["broker"] == "alpaca"
    assert connection["enabled"] is True

    assert get_connection("conn_missing", db_path=db) is None


def test_resolve_account_broker(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None

    assert resolve_account_broker(default["id"], db_path=db) == "alpaca"
    assert resolve_account_broker("acct_missing", db_path=db) is None


def test_get_broker_for_account_resolves_via_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _migrated_db(tmp_path)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None

    sentinel = object()
    connection_calls: list[str] = []
    broker_calls: list[str] = []

    def fake_get_broker_for_connection(connection: dict):
        connection_calls.append(connection["id"])
        return sentinel

    def fake_get_broker(name: str | None = None):
        broker_calls.append(name or "")
        return sentinel

    monkeypatch.setattr(
        broker_factory, "get_broker_for_connection", fake_get_broker_for_connection
    )
    monkeypatch.setattr(broker_factory, "get_broker", fake_get_broker)
    monkeypatch.setattr(
        "db.accounts.get_account",
        lambda account_id, db_path=None: get_account(account_id, db_path=db),
    )
    monkeypatch.setattr(
        "db.accounts.get_connection",
        lambda connection_id, db_path=None: get_connection(connection_id, db_path=db),
    )

    result = broker_factory.get_broker_for_account(default["id"])
    assert result is sentinel
    assert connection_calls == [default["connection_id"]]
    assert broker_calls == []

    with pytest.raises(BrokerError, match="unknown account"):
        broker_factory.get_broker_for_account("acct_nonexistent")
