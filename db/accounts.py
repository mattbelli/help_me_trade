"""Read-model accessors for broker connections and trade accounts."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.trades import init_trade_tables

_ACCOUNT_COLUMNS = """
    id, label, broker, created_at, last_synced_at, broker_external_id,
    connection_id, enabled, execution_capable, is_default
"""


def _connection_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "broker": row["broker"],
        "label": row["label"],
        "enabled": bool(row["enabled"]),
        "credentials_json": row["credentials_json"],
        "created_at": row["created_at"],
    }


def _account_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "label": row["label"],
        "broker": row["broker"],
        "created_at": row["created_at"],
        "last_synced_at": row["last_synced_at"],
        "broker_external_id": row["broker_external_id"],
        "connection_id": row["connection_id"],
        "enabled": bool(row["enabled"]),
        "execution_capable": bool(row["execution_capable"]),
        "is_default": bool(row["is_default"]),
    }


def _safe_query_accounts(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    return [_account_row_to_dict(row) for row in rows]


def _safe_query_account(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> dict[str, Any] | None:
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return None
    return _account_row_to_dict(row) if row else None


def _safe_query_connections(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    return [_connection_row_to_dict(row) for row in rows]


def _safe_query_connection(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> dict[str, Any] | None:
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return None
    return _connection_row_to_dict(row) if row else None


def list_connections(
    *,
    broker: str | None = None,
    enabled_only: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_trade_tables(db_path)
    clauses: list[str] = []
    params: list[Any] = []
    if broker is not None:
        clauses.append("broker = ?")
        params.append(broker.strip().lower())
    if enabled_only:
        clauses.append("enabled = 1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT id, broker, label, enabled, credentials_json, created_at
        FROM broker_connections
        {where}
        ORDER BY broker, created_at
    """
    with _connect(db_path) as conn:
        return _safe_query_connections(conn, sql, tuple(params))


def get_connection(
    connection_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    init_trade_tables(db_path)
    with _connect(db_path) as conn:
        return _safe_query_connection(
            conn,
            """
            SELECT id, broker, label, enabled, credentials_json, created_at
            FROM broker_connections
            WHERE id = ?
            """,
            (connection_id,),
        )


def list_accounts(
    *,
    broker: str | None = None,
    enabled_only: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_trade_tables(db_path)
    clauses: list[str] = []
    params: list[Any] = []
    if broker is not None:
        clauses.append("broker = ?")
        params.append(broker.strip().lower())
    if enabled_only:
        clauses.append("enabled = 1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT {_ACCOUNT_COLUMNS}
        FROM trade_accounts
        {where}
        ORDER BY broker, label
    """
    with _connect(db_path) as conn:
        return _safe_query_accounts(conn, sql, tuple(params))


def get_account(
    account_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    init_trade_tables(db_path)
    with _connect(db_path) as conn:
        return _safe_query_account(
            conn,
            f"""
            SELECT {_ACCOUNT_COLUMNS}
            FROM trade_accounts
            WHERE id = ?
            """,
            (account_id,),
        )


def get_default_account(
    broker: str = "alpaca",
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    init_trade_tables(db_path)
    broker = broker.strip().lower()
    with _connect(db_path) as conn:
        return _safe_query_account(
            conn,
            f"""
            SELECT {_ACCOUNT_COLUMNS}
            FROM trade_accounts
            WHERE broker = ? AND is_default = 1
            LIMIT 1
            """,
            (broker,),
        )


def default_execution_account(
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    init_trade_tables(db_path)
    with _connect(db_path) as conn:
        return _safe_query_account(
            conn,
            f"""
            SELECT {_ACCOUNT_COLUMNS}
            FROM trade_accounts
            WHERE enabled = 1
              AND execution_capable = 1
              AND is_default = 1
            ORDER BY created_at
            LIMIT 1
            """,
        )


def account_is_execution_capable(
    account_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    account = get_account(account_id, db_path=db_path)
    if account is None:
        return False
    return bool(account["enabled"]) and bool(account["execution_capable"])


def resolve_account_broker(
    account_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    account = get_account(account_id, db_path=db_path)
    if account is None:
        return None
    return str(account["broker"]).strip().lower()


def reconcile_account_external_id(
    account_id: str,
    broker_external_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Stamp broker_external_id on first poll when still NULL."""
    init_trade_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE trade_accounts
            SET broker_external_id = ?
            WHERE id = ? AND broker_external_id IS NULL
            """,
            (broker_external_id, account_id),
        )
        conn.commit()
