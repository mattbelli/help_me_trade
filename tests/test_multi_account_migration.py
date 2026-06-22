"""Multi-account schema migration (sub-step 1)."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from db.base import connect
from db.multi_account import migrate_multi_account
from db.order_executions import init_execution_tables
from db.paper_orders import init_paper_order_tables
from db.schema_helpers import _add_column_if_missing
from db.sqlite import get_latest_snapshot, init_db
from db.trades import init_trade_tables


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _init_base_tables(db_path: Path) -> None:
    init_db(db_path)
    init_trade_tables(db_path)
    init_execution_tables(db_path)
    init_paper_order_tables(db_path)


def test_fresh_db_migration_creates_schema_and_default_alpaca_account(
    tmp_path: Path,
) -> None:
    db = tmp_path / "fresh.db"
    migrate_multi_account(db)

    with connect(db) as conn:
        assert _table_exists(conn, "broker_connections")
        assert "connection_id" in _columns(conn, "trade_accounts")
        assert "enabled" in _columns(conn, "trade_accounts")
        assert "execution_capable" in _columns(conn, "trade_accounts")
        assert "is_default" in _columns(conn, "trade_accounts")
        assert "account_id" in _columns(conn, "poll_runs")
        assert "account_id" in _columns(conn, "order_executions")
        assert "account_id" in _columns(conn, "paper_order_proposals")
        assert _index_exists(conn, "idx_poll_runs_account_captured")
        assert _index_exists(conn, "idx_order_exec_account_created")
        assert _index_exists(conn, "idx_proposals_account_status")

        default = conn.execute(
            """
            SELECT id, broker, is_default, execution_capable
            FROM trade_accounts
            WHERE broker = 'alpaca' AND is_default = 1
            """
        ).fetchone()
        assert default is not None
        assert int(default["is_default"]) == 1
        assert int(default["execution_capable"]) == 1

        alpaca_conn = conn.execute(
            "SELECT id, broker, label FROM broker_connections WHERE broker = 'alpaca'"
        ).fetchone()
        assert alpaca_conn is not None
        assert alpaca_conn["label"] == "Alpaca"


def test_migration_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "db.multi_account.discover_alpaca_credential_suffixes",
        lambda: [],
    )
    db = tmp_path / "idempotent.db"
    migrate_multi_account(db)
    migrate_multi_account(db)

    with connect(db) as conn:
        conn_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM broker_connections WHERE broker = 'alpaca'"
        ).fetchone()["cnt"]
        default_count = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM trade_accounts
            WHERE broker = 'alpaca' AND is_default = 1
            """
        ).fetchone()["cnt"]
    assert int(conn_count) == 1
    assert int(default_count) == 1


def test_migration_backfills_account_id_on_legacy_rows(tmp_path: Path) -> None:
    db = tmp_path / "backfill.db"
    _init_base_tables(db)

    captured_at = datetime.now(UTC).isoformat()
    with connect(db) as conn:
        cursor = conn.execute(
            "INSERT INTO poll_runs (captured_at, broker, account_id) VALUES (?, 'alpaca', NULL)",
            (captured_at,),
        )
        poll_run_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO account_snapshots (
                poll_run_id, account_id, status, cash, buying_power, portfolio_value
            ) VALUES (?, 'alpaca-live-123', 'ACTIVE', 1000, 1000, 5000)
            """,
            (poll_run_id,),
        )
        conn.execute(
            """
            INSERT INTO order_executions (
                id, proposal_id, session_id, symbol, side, qty, order_type,
                limit_price, execution_mode, status, broker_order_id, broker,
                risk_checks_json, submitted_at, created_at, updated_at, account_id
            ) VALUES (
                'exec_legacy01', NULL, NULL, 'AAPL', 'buy', '1', 'market',
                NULL, 'paper', 'submitted', NULL, 'alpaca', '{}', NULL, ?, ?, NULL
            )
            """,
            (captured_at, captured_at),
        )
        conn.execute(
            """
            INSERT INTO paper_order_proposals (
                id, session_id, symbol, side, qty, order_type, limit_price,
                status, proposal_json, created_at, account_id
            ) VALUES (
                'paper_legacy01', NULL, 'AAPL', 'buy', 1, 'market', NULL,
                'pending_phone', '{}', ?, NULL
            )
            """,
            (captured_at,),
        )
        conn.commit()

    migrate_multi_account(db)

    with connect(db) as conn:
        default_id = conn.execute(
            """
            SELECT id FROM trade_accounts
            WHERE broker = 'alpaca' AND is_default = 1
            """
        ).fetchone()["id"]
        poll_account_id = conn.execute(
            "SELECT account_id FROM poll_runs WHERE id = ?", (poll_run_id,)
        ).fetchone()["account_id"]
        exec_account_id = conn.execute(
            "SELECT account_id FROM order_executions WHERE id = 'exec_legacy01'"
        ).fetchone()["account_id"]
        proposal_account_id = conn.execute(
            "SELECT account_id FROM paper_order_proposals WHERE id = 'paper_legacy01'"
        ).fetchone()["account_id"]
        external_id = conn.execute(
            "SELECT broker_external_id FROM trade_accounts WHERE id = ?",
            (default_id,),
        ).fetchone()["broker_external_id"]

    assert poll_account_id == default_id
    assert exec_account_id == default_id
    assert proposal_account_id == default_id
    assert external_id == "alpaca-live-123"


def test_backfill_leaves_non_alpaca_pollrun_null(tmp_path: Path) -> None:
    # A schwab poll_run must NOT be stamped with the default alpaca account; it
    # stays NULL until per-account snapshot sync maps it correctly.
    db = tmp_path / "schwab_pollrun.db"
    _init_base_tables(db)

    captured_at = datetime.now(UTC).isoformat()
    with connect(db) as conn:
        cursor = conn.execute(
            "INSERT INTO poll_runs (captured_at, broker, account_id) VALUES (?, 'schwab', NULL)",
            (captured_at,),
        )
        schwab_poll_id = int(cursor.lastrowid)
        conn.commit()

    migrate_multi_account(db)

    with connect(db) as conn:
        account_id = conn.execute(
            "SELECT account_id FROM poll_runs WHERE id = ?", (schwab_poll_id,)
        ).fetchone()["account_id"]

    assert account_id is None


def test_migration_links_existing_schwab_trade_accounts(tmp_path: Path) -> None:
    db = tmp_path / "schwab.db"
    _init_base_tables(db)
    roth_id = f"acct_{uuid.uuid4().hex[:12]}"
    joint_id = f"acct_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    with connect(db) as conn:
        conn.executemany(
            """
            INSERT INTO trade_accounts (id, label, broker, created_at)
            VALUES (?, ?, 'schwab', ?)
            """,
            [
                (roth_id, "Roth IRA", now),
                (joint_id, "Joint", now),
            ],
        )
        conn.commit()

    migrate_multi_account(db)

    with connect(db) as conn:
        schwab_conn = conn.execute(
            "SELECT id FROM broker_connections WHERE broker = 'schwab'"
        ).fetchone()
        assert schwab_conn is not None
        rows = conn.execute(
            """
            SELECT id, connection_id, execution_capable, is_default, enabled
            FROM trade_accounts
            WHERE broker = 'schwab'
            ORDER BY label
            """
        ).fetchall()
    assert len(rows) == 2
    for row in rows:
        assert row["connection_id"] == schwab_conn["id"]
        assert int(row["execution_capable"]) == 0
        assert int(row["is_default"]) == 0
        assert int(row["enabled"]) == 1


def test_add_column_if_missing_is_guarded(tmp_path: Path) -> None:
    db = tmp_path / "columns.db"
    with connect(db) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
        conn.commit()

    with connect(db) as conn:
        assert _add_column_if_missing(conn, "sample", "note", "TEXT") is True
        conn.commit()
    with connect(db) as conn:
        assert _add_column_if_missing(conn, "sample", "note", "TEXT") is False
        columns = _columns(conn, "sample")
    assert "note" in columns


def test_migration_preserves_snapshot_reads(tmp_path: Path) -> None:
    db = tmp_path / "snapshot.db"
    _init_base_tables(db)
    captured_at = datetime.now(UTC).isoformat()
    with connect(db) as conn:
        cursor = conn.execute(
            "INSERT INTO poll_runs (captured_at, broker) VALUES (?, 'alpaca')",
            (captured_at,),
        )
        run_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO account_snapshots (
                poll_run_id, account_id, status, cash, buying_power, portfolio_value
            ) VALUES (?, 'snap-acct-1', 'ACTIVE', 2500, 2500, 10000)
            """,
            (run_id,),
        )
        conn.execute(
            """
            INSERT INTO position_snapshots (
                poll_run_id, symbol, qty, side, market_value, unrealized_pl,
                avg_entry_price, current_price
            ) VALUES (?, 'AAPL', 10, 'long', 1500, 50, 145, 150)
            """,
            (run_id,),
        )
        conn.commit()

    migrate_multi_account(db)

    snapshot = get_latest_snapshot("alpaca", db_path=db)
    assert snapshot is not None
    assert snapshot["account"]["id"] == "snap-acct-1"
    assert snapshot["account"]["cash"] == 2500
    assert snapshot["position_count"] == 1
    assert snapshot["positions"][0]["symbol"] == "AAPL"
