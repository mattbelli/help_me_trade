"""Strategy registry — config/data for independently toggleable trading strategies."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from config.settings import trading_config
from db.accounts import default_execution_account, list_accounts
from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

_DIP_BUY_DEFAULT_PARAMS: dict[str, Any] = {
    "oversold_3d_max": -5.0,
    "mkt_down_3d_max": -1.5,
    "healthy_required": True,
    "side": "long",
    "stop_pct": -3.0,
    "first_target_pct": 3.0,
    "trail_pct": 11.0,
    "max_holding_days": 20,
}

_VALID_EXIT_MODES = frozenset({"auto", "propose"})


def init_strategy_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                account_id TEXT,
                notional_usd REAL NOT NULL DEFAULT 500,
                exit_mode TEXT NOT NULL DEFAULT 'propose',
                params_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()


def _row_to_strategy(row: sqlite3.Row) -> dict[str, Any]:
    try:
        params = json.loads(row["params_json"] or "{}")
    except json.JSONDecodeError:
        params = {}
    if not isinstance(params, dict):
        params = {}
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "account_id": row["account_id"],
        "notional_usd": float(row["notional_usd"]),
        "exit_mode": row["exit_mode"],
        "params": params,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_strategies(
    *,
    enabled_only: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_strategy_tables(db_path)
    where = "WHERE enabled = 1" if enabled_only else ""
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                f"""
                SELECT id, name, enabled, account_id, notional_usd, exit_mode,
                       params_json, created_at, updated_at
                FROM strategies
                {where}
                ORDER BY created_at
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [_row_to_strategy(row) for row in rows]


def get_strategy(
    strategy_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    init_strategy_tables(db_path)
    with _connect(db_path) as conn:
        try:
            row = conn.execute(
                """
                SELECT id, name, enabled, account_id, notional_usd, exit_mode,
                       params_json, created_at, updated_at
                FROM strategies
                WHERE id = ?
                """,
                (strategy_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return _row_to_strategy(row) if row else None


def update_strategy(
    strategy_id: str,
    *,
    enabled: bool | None = None,
    account_id: str | None = ...,  # type: ignore[assignment]
    notional_usd: float | None = None,
    exit_mode: str | None = None,
    params: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_strategy_tables(db_path)
    existing = get_strategy(strategy_id, db_path=db_path)
    if existing is None:
        raise ValueError(f"unknown strategy {strategy_id!r}")

    if exit_mode is not None and exit_mode not in _VALID_EXIT_MODES:
        raise ValueError(f"exit_mode must be one of {sorted(_VALID_EXIT_MODES)}")

    if notional_usd is not None and notional_usd < 0:
        raise ValueError("notional_usd must be >= 0")

    fields: list[str] = []
    values: list[Any] = []

    if enabled is not None:
        fields.append("enabled = ?")
        values.append(1 if enabled else 0)
    if account_id is not ...:
        fields.append("account_id = ?")
        values.append(account_id)
    if notional_usd is not None:
        fields.append("notional_usd = ?")
        values.append(float(notional_usd))
    if exit_mode is not None:
        fields.append("exit_mode = ?")
        values.append(exit_mode)
    if params is not None:
        fields.append("params_json = ?")
        values.append(json.dumps(params))

    if not fields:
        return existing

    fields.append("updated_at = ?")
    values.append(_now())
    values.append(strategy_id)

    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE strategies SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        conn.commit()

    updated = get_strategy(strategy_id, db_path=db_path)
    assert updated is not None
    return updated


def _resolve_seed_account_id(*, db_path: Path) -> str | None:
    """Non-default execution-capable account if exactly one; else default; else NULL."""
    exec_accounts = [
        a
        for a in list_accounts(db_path=db_path)
        if a.get("enabled")
        and a.get("execution_capable")
        and not a.get("is_default")
    ]
    if len(exec_accounts) == 1:
        return str(exec_accounts[0]["id"])
    default = default_execution_account(db_path=db_path)
    return str(default["id"]) if default else None


def seed_default_strategies(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Idempotent seed — creates dip_buy from legacy settings when missing."""
    init_strategy_tables(db_path)
    if get_strategy("dip_buy", db_path=db_path) is not None:
        return

    cfg = trading_config()
    enabled = bool(cfg.get("dip_strategy_enabled", False))
    notional = float(cfg.get("dip_strategy_notional_usd", 500) or 500)
    account_id = _resolve_seed_account_id(db_path=db_path)
    now = _now()

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO strategies (
                id, name, enabled, account_id, notional_usd, exit_mode,
                params_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "dip_buy",
                "Dip-Buy",
                1 if enabled else 0,
                account_id,
                notional,
                "auto",
                json.dumps(_DIP_BUY_DEFAULT_PARAMS),
                now,
                now,
            ),
        )
        conn.commit()
