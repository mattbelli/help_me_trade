"""Managed-position ledger — which strategy owns each open position + exit state.

The linchpin of the exit module: a position in the broker account isn't tagged
with the strategy that opened it, so the exit engine wouldn't know whose rules to
apply (dip vs earnings vs a manual trade). When a strategy-sourced BUY fills we
record an OPEN row here; the exit engine walks open rows, applies the owning
strategy's exit rules, and closes the row when the position is exited.

This table only RECORDS attribution + mutable exit state (high-water mark for
trailing, first-target-hit flag, optional exit context). The price basis (entry /
current) is read from the live account snapshot at evaluation time, so nothing
here is a source of truth for money — it's bookkeeping for *which rules apply*.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now
from db.schema_helpers import _add_column_if_missing

logger = logging.getLogger(__name__)


def init_managed_position_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS managed_positions (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                entry_execution_id TEXT,
                entry_date TEXT NOT NULL,
                -- ADVISORY qty only (float-derived). The exit engine must take the
                -- authoritative sell quantity from the LIVE broker position, never
                -- this column — using it as an order qty would bypass the Decimal
                -- seam + RiskEngine.
                qty TEXT,
                exit_mode TEXT NOT NULL DEFAULT 'propose',
                params_json TEXT NOT NULL DEFAULT '{}',
                exit_context_json TEXT NOT NULL DEFAULT '{}',
                high_water_mark TEXT,
                first_target_hit INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                closed_at TEXT,
                close_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_managed_open
                ON managed_positions (status, account_id);
            -- At most one OPEN managed row per (account, symbol, strategy).
            CREATE UNIQUE INDEX IF NOT EXISTS uq_managed_open
                ON managed_positions (account_id, symbol, strategy_id)
                WHERE status = 'open';
            """
        )
        # Decision Journal J0: link the SELL execution that closed this position, so the
        # journal can source the exit fill price (and grade the round-trip P&L).
        _add_column_if_missing(conn, "managed_positions", "exit_execution_id", "TEXT")
        conn.commit()


def _row_to_dict(row: Any) -> dict[str, Any]:
    out = dict(row)
    for key in ("params_json", "exit_context_json"):
        try:
            out[key.replace("_json", "")] = json.loads(out.pop(key) or "{}")
        except (json.JSONDecodeError, TypeError):
            out[key.replace("_json", "")] = {}
    out["first_target_hit"] = bool(out.get("first_target_hit"))
    return out


def record_open_position(
    *,
    account_id: str,
    symbol: str,
    strategy_id: str,
    qty: str | None,
    exit_mode: str,
    params: dict[str, Any],
    entry_execution_id: str | None = None,
    exit_context: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> str:
    """Insert an OPEN managed position. Idempotent per (account, symbol, strategy):
    if one is already open, refresh its qty/exit_mode/params and return its id."""
    init_managed_position_tables(db_path)
    symbol = symbol.strip().upper()
    now = _now()
    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM managed_positions "
            "WHERE account_id = ? AND symbol = ? AND strategy_id = ? AND status = 'open'",
            (account_id, symbol, strategy_id),
        ).fetchone()
        if existing is not None:
            conn.execute(
                "UPDATE managed_positions SET qty = ?, exit_mode = ?, "
                "params_json = ?, updated_at = ? WHERE id = ?",
                (qty, exit_mode, json.dumps(params), now, existing["id"]),
            )
            conn.commit()
            return str(existing["id"])

        new_id = f"mp_{uuid.uuid4().hex[:16]}"
        conn.execute(
            """
            INSERT INTO managed_positions (
                id, account_id, symbol, strategy_id, entry_execution_id,
                entry_date, qty, exit_mode, params_json, exit_context_json,
                high_water_mark, first_target_hit, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 'open', ?, ?)
            """,
            (
                new_id,
                account_id,
                symbol,
                strategy_id,
                entry_execution_id,
                now,
                qty,
                exit_mode,
                json.dumps(params),
                json.dumps(exit_context or {}),
                now,
                now,
            ),
        )
        conn.commit()
        return new_id


def list_open_positions(
    *,
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_managed_position_tables(db_path)
    with _connect(db_path) as conn:
        if account_id is not None:
            rows = conn.execute(
                "SELECT * FROM managed_positions "
                "WHERE status = 'open' AND account_id = ? ORDER BY created_at",
                (account_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM managed_positions WHERE status = 'open' "
                "ORDER BY created_at"
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_exit_state(
    position_id: str,
    *,
    high_water_mark: str | None = None,
    first_target_hit: bool | None = None,
    exit_context: dict[str, Any] | None = None,
    qty: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    fields: list[str] = []
    values: list[Any] = []
    if high_water_mark is not None:
        fields.append("high_water_mark = ?")
        values.append(high_water_mark)
    if first_target_hit is not None:
        fields.append("first_target_hit = ?")
        values.append(1 if first_target_hit else 0)
    if exit_context is not None:
        fields.append("exit_context_json = ?")
        values.append(json.dumps(exit_context))
    if qty is not None:
        fields.append("qty = ?")
        values.append(qty)
    if not fields:
        return
    fields.append("updated_at = ?")
    values.append(_now())
    values.append(position_id)
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE managed_positions SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        conn.commit()


def close_position(
    position_id: str,
    *,
    reason: str,
    exit_execution_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE managed_positions SET status = 'closed', closed_at = ?, "
            "close_reason = ?, exit_execution_id = ?, updated_at = ? WHERE id = ?",
            (now, reason, exit_execution_id, now, position_id),
        )
        conn.commit()
    # Decision Journal final OUTCOME (J1) — strictly observational and fail-soft: nothing
    # past the committed UPDATE above (the row re-fetch, the journal write) may EVER surface
    # into the position-close / exit path. The close has already happened.
    _record_close_outcome(
        position_id, exit_execution_id=exit_execution_id, reason=reason,
        closed_at=now, db_path=db_path,
    )


def _record_close_outcome(
    position_id: str,
    *,
    exit_execution_id: str | None,
    reason: str,
    closed_at: str,
    db_path: Path,
) -> None:
    """Resolve the decision-journal OUTCOME row for a just-closed position. Never raises.

    Re-fetches the closed row (inside this try, so even the SELECT can't escape), recovers
    the ENTRY proposal via the entry execution, sources the exit fill price from the linked
    SELL execution (null for a reconcile-closure with no agent sell → the journal records
    outcome='closed_incomplete'), and delegates the P&L/R computation to
    ``decision_journal.resolve_outcome``.
    """
    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT * FROM managed_positions WHERE id = ?", (position_id,)
            ).fetchone()
        if row is None:
            return
        from db.decision_journal import resolve_outcome
        from db.order_executions import get_execution

        entry_exec_id = row["entry_execution_id"]
        if not entry_exec_id:
            return
        entry_exec = get_execution(str(entry_exec_id), db_path=db_path)
        proposal_id = (entry_exec or {}).get("proposal_id")
        if not proposal_id:
            return

        exit_fill_price = None
        if exit_execution_id:
            exit_exec = get_execution(str(exit_execution_id), db_path=db_path)
            exit_fill_price = (exit_exec or {}).get("avg_fill_price")

        resolve_outcome(
            str(proposal_id),
            exit_execution_id=exit_execution_id,
            exit_fill_price=exit_fill_price,
            close_reason=reason,
            entry_time=row["entry_date"],
            close_time=closed_at,
            db_path=db_path,
        )
    except Exception:
        logger.warning(
            "decision_journal close-outcome hook failed for %s",
            position_id,
            exc_info=True,
        )
