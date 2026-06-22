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

# Refined dip = the validated dip edge PLUS a "far-from-support" filter
# (far_support_min: stock must be >5% above its nearest swing-support level).
# Backtest (research/decision-engine-data-layer): the filter roughly doubled the
# RECENT excess over SPY (+1.6% -> +3%/trade). NOTE: this refinement is NOT
# block-bootstrap-validated to the same standard as the base dip — it is a live
# forward-test candidate, not a calibrated promise. Seeded DISABLED + UNBOUND.
_DIP_REFINED_DEFAULT_PARAMS: dict[str, Any] = {
    **_DIP_BUY_DEFAULT_PARAMS,
    "far_support_min": 5.0,
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
        # Idempotent column adds (per-strategy risk caps; NULL = uncapped).
        existing = {row[1] for row in conn.execute("PRAGMA table_info(strategies)")}
        if "max_concurrent_positions" not in existing:
            conn.execute("ALTER TABLE strategies ADD COLUMN max_concurrent_positions INTEGER")
        if "max_deployed_usd" not in existing:
            conn.execute("ALTER TABLE strategies ADD COLUMN max_deployed_usd REAL")
        conn.commit()


def _row_to_strategy(row: sqlite3.Row) -> dict[str, Any]:
    try:
        params = json.loads(row["params_json"] or "{}")
    except json.JSONDecodeError:
        params = {}
    if not isinstance(params, dict):
        params = {}
    max_concurrent = row["max_concurrent_positions"]
    max_deployed = row["max_deployed_usd"]
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "account_id": row["account_id"],
        "notional_usd": float(row["notional_usd"]),
        "exit_mode": row["exit_mode"],
        "params": params,
        "max_concurrent_positions": (
            int(max_concurrent) if max_concurrent is not None else None
        ),
        "max_deployed_usd": (
            float(max_deployed) if max_deployed is not None else None
        ),
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
                       params_json, max_concurrent_positions, max_deployed_usd,
                       created_at, updated_at
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
                       params_json, max_concurrent_positions, max_deployed_usd,
                       created_at, updated_at
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
    max_concurrent_positions: int | None = ...,  # type: ignore[assignment]
    max_deployed_usd: float | None = ...,  # type: ignore[assignment]
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

    if (
        max_concurrent_positions is not ...
        and max_concurrent_positions is not None
        and max_concurrent_positions < 0
    ):
        raise ValueError("max_concurrent_positions must be >= 0")

    if (
        max_deployed_usd is not ...
        and max_deployed_usd is not None
        and max_deployed_usd < 0
    ):
        raise ValueError("max_deployed_usd must be >= 0")

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
    if max_concurrent_positions is not ...:
        fields.append("max_concurrent_positions = ?")
        values.append(
            int(max_concurrent_positions)
            if max_concurrent_positions is not None
            else None
        )
    if max_deployed_usd is not ...:
        fields.append("max_deployed_usd = ?")
        values.append(
            float(max_deployed_usd) if max_deployed_usd is not None else None
        )

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


def _insert_strategy(
    conn: sqlite3.Connection,
    *,
    strategy_id: str,
    name: str,
    enabled: bool,
    account_id: str | None,
    notional: float,
    params: dict[str, Any],
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO strategies (
            id, name, enabled, account_id, notional_usd, exit_mode,
            params_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            strategy_id,
            name,
            1 if enabled else 0,
            account_id,
            notional,
            "auto",
            json.dumps(params),
            now,
            now,
        ),
    )


def seed_default_strategies(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Idempotent seed — registers each built-in strategy when missing.

    Each strategy is seeded independently (checked by id), so adding a new
    built-in here back-fills it on the next boot without disturbing existing
    rows or their user-edited enable/account/notional settings.
    """
    init_strategy_tables(db_path)
    cfg = trading_config()
    now = _now()

    need_dip_buy = get_strategy("dip_buy", db_path=db_path) is None
    need_dip_refined = get_strategy("dip_refined", db_path=db_path) is None
    if not need_dip_buy and not need_dip_refined:
        return

    seed_account_id = _resolve_seed_account_id(db_path=db_path) if need_dip_buy else None

    with _connect(db_path) as conn:
        if need_dip_buy:
            _insert_strategy(
                conn,
                strategy_id="dip_buy",
                name="Dip-Buy",
                enabled=bool(cfg.get("dip_strategy_enabled", False)),
                account_id=seed_account_id,
                notional=float(cfg.get("dip_strategy_notional_usd", 500) or 500),
                params=_DIP_BUY_DEFAULT_PARAMS,
                now=now,
            )

        if need_dip_refined:
            # Disabled + UNBOUND by default — the user binds it to a dedicated
            # forward-test account in the UI (ideally separate from dip_buy's so
            # the two edges don't commingle). Never auto-arms.
            _insert_strategy(
                conn,
                strategy_id="dip_refined",
                name="Dip-Buy + S/R filter",
                enabled=False,
                account_id=None,
                notional=500.0,
                params=_DIP_REFINED_DEFAULT_PARAMS,
                now=now,
            )

        conn.commit()
