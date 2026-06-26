"""Durable momentum rebalance plans — idempotent/resumable monthly execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now


def init_rebalance_table(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rebalance_runs (
                strategy_id TEXT NOT NULL,
                month TEXT NOT NULL,
                status TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (strategy_id, month)
            );
            """
        )
        conn.commit()


def _row_to_run(row: Any) -> dict[str, Any]:
    out = dict(row)
    try:
        out["plan"] = json.loads(out.pop("plan_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        out["plan"] = {}
    return out


def start_run(
    strategy_id: str,
    month: str,
    plan: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Upsert a row with status='executing' and the serialized plan."""
    init_rebalance_table(db_path)
    now = _now()
    plan_json = json.dumps(plan)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO rebalance_runs (
                strategy_id, month, status, plan_json, created_at, updated_at
            ) VALUES (?, ?, 'executing', ?, ?, ?)
            ON CONFLICT(strategy_id, month) DO UPDATE SET
                status = 'executing',
                plan_json = excluded.plan_json,
                updated_at = excluded.updated_at
            """,
            (strategy_id, month, plan_json, now, now),
        )
        conn.commit()
    run = get_run(strategy_id, month, db_path=db_path)
    assert run is not None
    return run


def get_run(
    strategy_id: str,
    month: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    init_rebalance_table(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT strategy_id, month, status, plan_json, created_at, updated_at
            FROM rebalance_runs
            WHERE strategy_id = ? AND month = ?
            """,
            (strategy_id, month),
        ).fetchone()
    return _row_to_run(row) if row else None


def complete_run(
    strategy_id: str,
    month: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    init_rebalance_table(db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE rebalance_runs
            SET status = 'complete', updated_at = ?
            WHERE strategy_id = ? AND month = ?
            """,
            (now, strategy_id, month),
        )
        conn.commit()
