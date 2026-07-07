"""Audited order execution records (dry_run, paper, live)."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now
from db.schema_helpers import _add_column_if_missing

logger = logging.getLogger(__name__)


def init_execution_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS order_executions (
                id TEXT PRIMARY KEY,
                proposal_id TEXT,
                session_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty TEXT NOT NULL,
                order_type TEXT NOT NULL,
                limit_price TEXT,
                execution_mode TEXT NOT NULL,
                status TEXT NOT NULL,
                broker_order_id TEXT,
                broker TEXT NOT NULL,
                risk_checks_json TEXT NOT NULL DEFAULT '{}',
                submitted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_order_executions_created
                ON order_executions(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_order_executions_proposal
                ON order_executions(proposal_id);
            CREATE INDEX IF NOT EXISTS idx_order_executions_status_created
                ON order_executions(status, created_at DESC);
            """
        )
        _add_column_if_missing(conn, "order_executions", "account_id", "TEXT")
        # Fill economics (Decision Journal J0): the broker's realized fill price + filled
        # qty, read off the order at reconcile time. Stored as TEXT (Decimal), matching the
        # money rule. Advisory/audit only — never read by the RiskEngine or chokepoint.
        _add_column_if_missing(conn, "order_executions", "avg_fill_price", "TEXT")
        _add_column_if_missing(conn, "order_executions", "filled_qty", "TEXT")
        _add_column_if_missing(conn, "order_executions", "notional_usd", "TEXT")
        _add_column_if_missing(conn, "order_executions", "short_open", "INTEGER DEFAULT 0")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_order_exec_account_created
                ON order_executions(account_id, created_at, status)
            """
        )


def _row_to_execution(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "proposal_id": row["proposal_id"],
        "session_id": row["session_id"],
        "symbol": row["symbol"],
        "side": row["side"],
        "qty": row["qty"],
        "order_type": row["order_type"],
        "limit_price": row["limit_price"],
        "execution_mode": row["execution_mode"],
        "status": row["status"],
        "broker_order_id": row["broker_order_id"],
        "broker": row["broker"],
        "risk_checks_json": json.loads(row["risk_checks_json"] or "{}"),
        "submitted_at": row["submitted_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "account_id": row["account_id"],
        "avg_fill_price": row["avg_fill_price"] if "avg_fill_price" in row.keys() else None,
        "filled_qty": row["filled_qty"] if "filled_qty" in row.keys() else None,
        "notional_usd": row["notional_usd"] if "notional_usd" in row.keys() else None,
        "short_open": int(row["short_open"]) if "short_open" in row.keys() else 0,
    }


def create_execution_record(
    *,
    execution_id: str,
    proposal_id: str | None,
    session_id: str | None,
    symbol: str,
    side: str,
    qty: Decimal,
    order_type: str,
    limit_price: Decimal | None,
    execution_mode: str,
    status: str,
    broker: str,
    risk_checks_json: dict[str, Any] | str | None = None,
    broker_order_id: str | None = None,
    submitted_at: str | None = None,
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_execution_tables(db_path)
    now = _now()
    checks_payload = (
        risk_checks_json
        if isinstance(risk_checks_json, str)
        else json.dumps(risk_checks_json or {})
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO order_executions (
                id, proposal_id, session_id, symbol, side, qty, order_type,
                limit_price, execution_mode, status, broker_order_id, broker,
                risk_checks_json, submitted_at, created_at, updated_at, account_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                execution_id,
                proposal_id,
                session_id,
                symbol,
                side,
                str(qty),
                order_type,
                str(limit_price) if limit_price is not None else None,
                execution_mode,
                status,
                broker_order_id,
                broker,
                checks_payload,
                submitted_at,
                now,
                now,
                account_id,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM order_executions WHERE id = ?", (execution_id,)
        ).fetchone()
    return _row_to_execution(row)


def update_execution_status(
    execution_id: str,
    status: str,
    *,
    broker_order_id: str | None = None,
    submitted_at: str | None = None,
    risk_checks_json: dict[str, Any] | str | None = None,
    avg_fill_price: Decimal | str | None = None,
    filled_qty: Decimal | str | None = None,
    notional_usd: Decimal | str | None = None,
    short_open: int | None = None,
    clear_notional: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    init_execution_tables(db_path)
    now = _now()
    fields: list[str] = ["status = ?", "updated_at = ?"]
    values: list[Any] = [status, now]
    if broker_order_id is not None:
        fields.append("broker_order_id = ?")
        values.append(broker_order_id)
    if submitted_at is not None:
        fields.append("submitted_at = ?")
        values.append(submitted_at)
    if avg_fill_price is not None:
        fields.append("avg_fill_price = ?")
        values.append(str(avg_fill_price))
    if filled_qty is not None:
        fields.append("filled_qty = ?")
        values.append(str(filled_qty))
    if clear_notional:
        fields.append("notional_usd = NULL")
    elif notional_usd is not None:
        fields.append("notional_usd = ?")
        values.append(str(notional_usd))
    if short_open is not None:
        fields.append("short_open = ?")
        values.append(int(short_open))
    if risk_checks_json is not None:
        checks_payload = (
            risk_checks_json
            if isinstance(risk_checks_json, str)
            else json.dumps(risk_checks_json)
        )
        fields.append("risk_checks_json = ?")
        values.append(checks_payload)
    values.append(execution_id)
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE order_executions SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM order_executions WHERE id = ?", (execution_id,)
        ).fetchone()
    return _row_to_execution(row) if row else None


def count_executions_today(
    *,
    account_id: str | None = None,
    exclude_statuses: frozenset[str] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    init_execution_tables(db_path)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    excluded = exclude_statuses or frozenset({"dry_run", "rejected", "error"})
    account_clause = " AND account_id = ?" if account_id is not None else ""
    with _connect(db_path) as conn:
        if excluded:
            placeholders = ", ".join("?" for _ in excluded)
            params: tuple[Any, ...] = (f"{today}T00:00:00", *excluded)
            if account_id is not None:
                params = (*params, account_id)
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS cnt FROM order_executions
                WHERE created_at >= ? AND status NOT IN ({placeholders}){account_clause}
                """,
                params,
            ).fetchone()
        else:
            params = (f"{today}T00:00:00",)
            if account_id is not None:
                params = (*params, account_id)
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS cnt FROM order_executions
                WHERE created_at >= ?{account_clause}
                """,
                params,
            ).fetchone()
    return int(row["cnt"]) if row else 0


def _sum_notional_rows(rows: list[sqlite3.Row], *, context: str) -> Decimal:
    total = Decimal("0")
    for row in rows:
        raw = row["notional_usd"]
        if raw is None or str(raw).strip() == "":
            # The current order's own pending row is always NULL here by design
            # (notional is written post-validate) — debug, not warning, to avoid
            # training log-blindness on every validate.
            logger.debug(
                "order_executions.%s: row %s has NULL notional_usd — counted as 0",
                context,
                row["id"],
            )
            continue
        total += Decimal(str(raw))
    return total


def sum_inflight_notional(
    *,
    symbol: str | None = None,
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> Decimal:
    """Sum notional of in-flight exposure-increasing orders (Decimal-safe).

    Counts pending/submitted AND partially_filled: a partially filled order is a
    LIVE working order (the unfilled remainder is still resting at the broker) whose
    notional_usd holds the full order notional, so the reservation must keep counting
    it until it reaches a terminal status (audit H2)."""
    init_execution_tables(db_path)
    clauses = [
        "status IN ('pending', 'submitted', 'partially_filled')",
        "(side = 'buy' OR (side = 'sell' AND short_open = 1))",
    ]
    params: list[Any] = []
    if symbol is not None:
        clauses.append("symbol = ?")
        params.append(symbol.strip().upper())
    if account_id is not None:
        clauses.append("account_id = ?")
        params.append(account_id)
    where = " AND ".join(clauses)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT id, notional_usd FROM order_executions WHERE {where}",
            params,
        ).fetchall()
    return _sum_notional_rows(rows, context="sum_inflight_notional")


def sum_today_notional(
    *,
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> Decimal:
    """Sum today's exposure-increasing notional (UTC day, matching count_executions_today bounds).

    Counts pending/submitted/partially_filled/filled: a partially_filled order's full
    notional counts toward the daily cap while it works (audit H2).

    KNOWN RESIDUAL (accepted, do not silently "fix"): a partial that later CANCELS
    lands on status 'canceled' and drops out of this sum entirely, even though its
    filled shares were really bought — under-counting the day by the filled portion.
    Counting it correctly needs a filled-portion notional (filled_qty x avg_fill_price)
    we do not cleanly have at cap time; the full-notional alternative over-counts.
    Adversarial-review F4 accepted this residual explicitly."""
    init_execution_tables(db_path)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    clauses = [
        "created_at >= ?",
        "status IN ('pending', 'submitted', 'partially_filled', 'filled')",
        "(side = 'buy' OR (side = 'sell' AND short_open = 1))",
    ]
    params: list[Any] = [f"{today}T00:00:00"]
    if account_id is not None:
        clauses.append("account_id = ?")
        params.append(account_id)
    where = " AND ".join(clauses)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT id, notional_usd FROM order_executions WHERE {where}",
            params,
        ).fetchall()
    return _sum_notional_rows(rows, context="sum_today_notional")


def get_execution(
    execution_id: str, *, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    init_execution_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM order_executions WHERE id = ?", (execution_id,)
        ).fetchone()
    return _row_to_execution(row) if row else None


def get_execution_by_proposal(
    proposal_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    """Most recent execution row for a proposal, if any."""
    init_execution_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM order_executions
            WHERE proposal_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (proposal_id,),
        ).fetchone()
    return _row_to_execution(row) if row else None


def list_submitted_executions(
    broker: str,
    *,
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_execution_tables(db_path)
    account_clause = " AND account_id = ?" if account_id is not None else ""
    params: tuple[Any, ...] = (broker,)
    if account_id is not None:
        params = (*params, account_id)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM order_executions
            WHERE status IN ('submitted', 'partially_filled') AND broker = ?{account_clause}
            ORDER BY created_at ASC
            """,
            params,
        ).fetchall()
    return [_row_to_execution(row) for row in rows]


def list_terminal_partial_buys(
    *,
    since_iso: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """BUY executions holding real filled shares that are no longer pre-fill — the
    unmanaged-fill sweep's candidate set (audit H1/M1).

    A partial fill that jumped straight to canceled/expired within one poll gap, or a
    fill whose attribution write was lost to a restart, holds real shares with no
    managed-position row. Such rows have side='buy', a non-empty filled_qty, and a
    status that is neither 'pending' nor 'submitted' (those are pre-fill / actively
    reconciling; 'partially_filled' is kept — a crash between its status write and
    attribution can strand it too). Rows that already have an orphan of ANY status are
    excluded here in SQL (adversarial F3) so already-quarantined executions don't re-run
    the per-row managed/orphan lookups on every poll cycle forever. filled_qty is TEXT
    (Decimal): the SQL only screens NULL/empty; the caller applies the Decimal ``> 0``
    test and the managed-row lookup. Bounded by created_at >= ``since_iso`` for query
    cost."""
    from db.fill_attribution import init_fill_attribution_tables

    init_execution_tables(db_path)
    init_fill_attribution_tables(db_path)  # the NOT EXISTS below needs the table
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM order_executions AS oe
            WHERE oe.side = 'buy'
              AND oe.status NOT IN ('pending', 'submitted')
              AND oe.filled_qty IS NOT NULL AND TRIM(oe.filled_qty) != ''
              AND oe.created_at >= ?
              AND NOT EXISTS (
                  SELECT 1 FROM fill_attribution_orphans AS fo
                  WHERE fo.execution_id = oe.id
              )
            ORDER BY oe.created_at ASC
            """,
            (since_iso,),
        ).fetchall()
    return [_row_to_execution(row) for row in rows]


def recent_open_buy_exists(
    symbol: str,
    *,
    account_id: str | None,
    within_seconds: float,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """True if a non-failed BUY execution for this symbol+account was created within the window.

    Entry-side double-submit guard for auto-entry: an in-flight or just-filled order is NOT yet
    a held position (snapshot lags) nor a pending phone proposal, so without this a re-scan could
    re-enter the same symbol before the first fill reconciles. Mirrors the propose path's
    pending-proposal dedup. Terminal-failed statuses are excluded so a rejected attempt can retry.
    (A partial-fill→canceled order ends 'canceled' and would pass here, but its partial fill has
    already opened a managed position + entered the held set, which blocks re-entry independently.)
    """
    init_execution_tables(db_path)
    cutoff = (datetime.now(UTC) - timedelta(seconds=within_seconds)).isoformat()
    account_clause = " AND account_id = ?" if account_id is not None else ""
    params: tuple[Any, ...] = (symbol.strip().upper(), cutoff)
    if account_id is not None:
        params = (*params, account_id)
    with _connect(db_path) as conn:
        row = conn.execute(
            f"""
            SELECT 1 FROM order_executions
            WHERE symbol = ? AND side = 'buy'
              AND status NOT IN ('rejected', 'canceled', 'cancelled', 'expired', 'failed')
              AND created_at >= ?{account_clause}
            LIMIT 1
            """,
            params,
        ).fetchone()
    return row is not None


def recent_open_sell_exists(
    symbol: str,
    *,
    account_id: str | None,
    within_seconds: float,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """True if a non-failed SELL execution for this symbol+account was created within the window.

    Rebalance-side double-submit guard: mirrors ``recent_open_buy_exists`` for sells.
    """
    init_execution_tables(db_path)
    cutoff = (datetime.now(UTC) - timedelta(seconds=within_seconds)).isoformat()
    account_clause = " AND account_id = ?" if account_id is not None else ""
    params: tuple[Any, ...] = (symbol.strip().upper(), cutoff)
    if account_id is not None:
        params = (*params, account_id)
    with _connect(db_path) as conn:
        row = conn.execute(
            f"""
            SELECT 1 FROM order_executions
            WHERE symbol = ? AND side = 'sell'
              AND status NOT IN ('rejected', 'canceled', 'cancelled', 'expired', 'failed')
              AND created_at >= ?{account_clause}
            LIMIT 1
            """,
            params,
        ).fetchone()
    return row is not None


def list_executions(
    *,
    status: str | None = None,
    account_id: str | None = None,
    limit: int = 100,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Newest-first execution records for the read-only orders blotter.

    Pure SELECT — no writes. Optionally filtered by ``status`` (e.g. "rejected")
    and/or ``account_id``.
    """
    init_execution_tables(db_path)
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if account_id:
        clauses.append("account_id = ?")
        params.append(account_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM order_executions
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_row_to_execution(row) for row in rows]


def list_recent_auto_entries(
    account_id: str | None,
    strategy_id: str,
    *,
    within_seconds: float,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """In-flight/recent auto-entry BUY execs for an account+strategy (for the concurrency cap).

    The per-symbol dedup (`recent_open_buy_exists`) stops same-symbol re-entry, but a strategy's
    `max_concurrent_positions` cap also needs to see in-flight auto orders on OTHER symbols — these
    live in order_executions, not in pending phone proposals. Matched by the synthetic
    `auto_entry|<id>|<ts>` proposal_id via an exact split (NOT a LIKE — strategy ids contain `_`,
    a LIKE wildcard). Terminal-failed statuses excluded; the caller dedupes already-held symbols.
    """
    init_execution_tables(db_path)
    cutoff = (datetime.now(UTC) - timedelta(seconds=within_seconds)).isoformat()
    account_clause = " AND account_id = ?" if account_id is not None else ""
    params: tuple[Any, ...] = (cutoff,)
    if account_id is not None:
        params = (*params, account_id)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT symbol, qty, proposal_id FROM order_executions
            WHERE side = 'buy'
              AND status NOT IN ('rejected', 'canceled', 'cancelled', 'expired', 'failed')
              AND created_at >= ?
              AND proposal_id IS NOT NULL{account_clause}
            """,
            params,
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        pid = str(row["proposal_id"])
        if not pid.startswith("auto_entry|"):
            continue
        parts = pid.split("|")
        if len(parts) >= 2 and parts[1] == strategy_id:
            out.append({"symbol": row["symbol"], "qty": row["qty"]})
    return out


def count_submitted_executions(
    broker: str,
    *,
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    init_execution_tables(db_path)
    account_clause = " AND account_id = ?" if account_id is not None else ""
    params: tuple[Any, ...] = (broker,)
    if account_id is not None:
        params = (*params, account_id)
    with _connect(db_path) as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS cnt FROM order_executions
            WHERE status IN ('submitted', 'partially_filled') AND broker = ?{account_clause}
            """,
            params,
        ).fetchone()
    return int(row["cnt"]) if row else 0


def count_pending_paper_executions(
    broker: str,
    *,
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    init_execution_tables(db_path)
    account_clause = " AND account_id = ?" if account_id is not None else ""
    params: tuple[Any, ...] = (broker,)
    if account_id is not None:
        params = (*params, account_id)
    with _connect(db_path) as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS cnt FROM order_executions
            WHERE status = 'pending'
              AND execution_mode = 'paper'
              AND broker = ?{account_clause}
            """,
            params,
        ).fetchone()
    return int(row["cnt"]) if row else 0


def list_stale_pending_paper_executions(
    broker: str,
    *,
    min_age_seconds: float = 30,
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Pending paper rows older than min_age_seconds (avoid racing in-flight submits)."""
    init_execution_tables(db_path)
    cutoff = datetime.now(UTC).timestamp() - min_age_seconds
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()
    account_clause = " AND account_id = ?" if account_id is not None else ""
    params: tuple[Any, ...] = (broker, cutoff_iso)
    if account_id is not None:
        params = (*params, account_id)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM order_executions
            WHERE status = 'pending'
              AND execution_mode = 'paper'
              AND broker = ?
              AND created_at < ?{account_clause}
            ORDER BY created_at ASC
            """,
            params,
        ).fetchall()
    return [_row_to_execution(row) for row in rows]
