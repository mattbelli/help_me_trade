"""Multi-account schema migration (connections → accounts)."""

from __future__ import annotations

import uuid
from pathlib import Path

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now
from db.order_executions import init_execution_tables
from db.paper_orders import init_paper_order_tables
from db.schema_helpers import _add_column_if_missing
from db.sqlite import init_db
from db.trades import init_trade_tables

_ALPACA_CONNECTION_ID = "conn_alpaca"
_SCHWAB_CONNECTION_ID = "conn_schwab"


def _ensure_multi_account_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS broker_connections (
            id TEXT PRIMARY KEY,
            broker TEXT NOT NULL,
            label TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            credentials_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        """
    )
    for column, decl in (
        ("connection_id", "TEXT"),
        ("enabled", "INTEGER NOT NULL DEFAULT 1"),
        ("execution_capable", "INTEGER NOT NULL DEFAULT 0"),
        ("is_default", "INTEGER NOT NULL DEFAULT 0"),
    ):
        _add_column_if_missing(conn, "trade_accounts", column, decl)

    _add_column_if_missing(conn, "poll_runs", "account_id", "TEXT")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_poll_runs_account_captured
            ON poll_runs(account_id, captured_at)
        """
    )

    _add_column_if_missing(conn, "order_executions", "account_id", "TEXT")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_order_exec_account_created
            ON order_executions(account_id, created_at, status)
        """
    )

    _add_column_if_missing(conn, "paper_order_proposals", "account_id", "TEXT")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_proposals_account_status
            ON paper_order_proposals(account_id, status)
        """
    )


def _latest_alpaca_snapshot_account_id(conn) -> str | None:
    row = conn.execute(
        """
        SELECT ac.account_id
        FROM account_snapshots ac
        JOIN poll_runs pr ON pr.id = ac.poll_run_id
        WHERE pr.broker = 'alpaca'
        ORDER BY pr.captured_at DESC
        LIMIT 1
        """
    ).fetchone()
    return str(row["account_id"]) if row else None


def _get_or_create_alpaca_connection(conn) -> str:
    row = conn.execute(
        """
        SELECT id FROM broker_connections
        WHERE broker = 'alpaca'
        ORDER BY created_at
        LIMIT 1
        """
    ).fetchone()
    if row:
        return str(row["id"])
    now = _now()
    conn.execute(
        """
        INSERT INTO broker_connections (
            id, broker, label, enabled, credentials_json, created_at
        ) VALUES (?, 'alpaca', 'Alpaca', 1, '{}', ?)
        """,
        (_ALPACA_CONNECTION_ID, now),
    )
    return _ALPACA_CONNECTION_ID


def _get_or_create_schwab_connection(conn) -> str:
    row = conn.execute(
        """
        SELECT id FROM broker_connections
        WHERE broker = 'schwab'
        ORDER BY created_at
        LIMIT 1
        """
    ).fetchone()
    if row:
        return str(row["id"])
    now = _now()
    conn.execute(
        """
        INSERT INTO broker_connections (
            id, broker, label, enabled, credentials_json, created_at
        ) VALUES (?, 'schwab', 'Schwab', 1, '{}', ?)
        """,
        (_SCHWAB_CONNECTION_ID, now),
    )
    return _SCHWAB_CONNECTION_ID


def _get_or_create_default_alpaca_account(
    conn, connection_id: str
) -> str:
    default_row = conn.execute(
        """
        SELECT id FROM trade_accounts
        WHERE broker = 'alpaca' AND is_default = 1
        LIMIT 1
        """
    ).fetchone()
    if default_row:
        account_id = str(default_row["id"])
    else:
        existing = conn.execute(
            """
            SELECT id FROM trade_accounts
            WHERE broker = 'alpaca'
            ORDER BY created_at
            LIMIT 1
            """
        ).fetchone()
        if existing:
            account_id = str(existing["id"])
        else:
            account_id = f"acct_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO trade_accounts (
                    id, label, broker, created_at, connection_id,
                    enabled, execution_capable, is_default, broker_external_id
                ) VALUES (?, 'Alpaca', 'alpaca', ?, ?, 1, 1, 1, NULL)
                """,
                (account_id, _now(), connection_id),
            )
            external_id = _latest_alpaca_snapshot_account_id(conn)
            if external_id:
                conn.execute(
                    """
                    UPDATE trade_accounts
                    SET broker_external_id = ?
                    WHERE id = ? AND broker_external_id IS NULL
                    """,
                    (external_id, account_id),
                )
            return account_id

    external_id = _latest_alpaca_snapshot_account_id(conn)
    conn.execute(
        """
        UPDATE trade_accounts
        SET connection_id = ?,
            enabled = 1,
            execution_capable = 1,
            is_default = 1,
            broker_external_id = COALESCE(broker_external_id, ?)
        WHERE id = ?
        """,
        (connection_id, external_id, account_id),
    )
    return account_id


def _link_schwab_accounts(conn, connection_id: str) -> None:
    conn.execute(
        """
        UPDATE trade_accounts
        SET connection_id = ?,
            enabled = 1,
            execution_capable = 0,
            is_default = 0
        WHERE broker = 'schwab'
          AND (connection_id IS NULL OR connection_id = '')
        """,
        (connection_id,),
    )


def _backfill_account_id(
    conn, table: str, default_account_id: str, *, broker: str | None = None
) -> None:
    # Only stamp rows that belong to the default (alpaca) account. Tables with a
    # `broker` column are scoped to broker='alpaca' so a future non-alpaca row
    # (e.g. a schwab poll_run once snapshot sync is account-aware) is left NULL
    # rather than mislabeled — it gets mapped correctly in the per-account sync step.
    if broker is not None:
        conn.execute(
            f"""
            UPDATE {table}
            SET account_id = ?
            WHERE account_id IS NULL AND broker = ?
            """,
            (default_account_id, broker),
        )
    else:
        conn.execute(
            f"""
            UPDATE {table}
            SET account_id = ?
            WHERE account_id IS NULL
            """,
            (default_account_id,),
        )


def migrate_multi_account(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Idempotent migration: connections, default account, account_id backfill."""
    init_db(db_path)
    init_trade_tables(db_path)
    init_execution_tables(db_path)
    init_paper_order_tables(db_path)

    with _connect(db_path) as conn:
        _ensure_multi_account_schema(conn)

        alpaca_connection_id = _get_or_create_alpaca_connection(conn)
        default_account_id = _get_or_create_default_alpaca_account(
            conn, alpaca_connection_id
        )

        schwab_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM trade_accounts WHERE broker = 'schwab'"
        ).fetchone()["cnt"]
        if int(schwab_count) > 0:
            schwab_connection_id = _get_or_create_schwab_connection(conn)
            _link_schwab_accounts(conn, schwab_connection_id)

        # poll_runs + order_executions carry a `broker` column → scope to alpaca.
        # paper_order_proposals has no broker column (broker lives in proposal_json)
        # and all legacy proposals are alpaca; new proposals will carry account_id
        # at creation (sub-step 3), so the NULL-backfill only ever touches legacy rows.
        _backfill_account_id(conn, "poll_runs", default_account_id, broker="alpaca")
        _backfill_account_id(
            conn, "order_executions", default_account_id, broker="alpaca"
        )
        _backfill_account_id(conn, "paper_order_proposals", default_account_id)

        conn.commit()
