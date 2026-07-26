"""SQLite storage for polled broker snapshots."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from brokers.base import Account, Order, Position
from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.schema_helpers import _add_column_if_missing


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS poll_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                broker TEXT NOT NULL DEFAULT 'alpaca'
            );

            CREATE TABLE IF NOT EXISTS account_snapshots (
                poll_run_id INTEGER NOT NULL,
                account_id TEXT NOT NULL,
                status TEXT NOT NULL,
                cash REAL NOT NULL,
                buying_power REAL NOT NULL,
                portfolio_value REAL NOT NULL,
                FOREIGN KEY (poll_run_id) REFERENCES poll_runs(id)
            );

            CREATE TABLE IF NOT EXISTS position_snapshots (
                poll_run_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                qty REAL NOT NULL,
                side TEXT NOT NULL,
                market_value REAL NOT NULL,
                unrealized_pl REAL NOT NULL,
                avg_entry_price REAL NOT NULL,
                current_price REAL NOT NULL,
                FOREIGN KEY (poll_run_id) REFERENCES poll_runs(id)
            );

            CREATE TABLE IF NOT EXISTS order_snapshots (
                poll_run_id INTEGER NOT NULL,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                qty REAL NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                status TEXT NOT NULL,
                filled_qty REAL NOT NULL,
                limit_price REAL,
                FOREIGN KEY (poll_run_id) REFERENCES poll_runs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_poll_runs_captured
                ON poll_runs(captured_at DESC);

            CREATE TABLE IF NOT EXISTS snapshot_analytics (
                poll_run_id INTEGER PRIMARY KEY,
                analytics_json TEXT NOT NULL,
                FOREIGN KEY (poll_run_id) REFERENCES poll_runs(id)
            );
            """
        )
        try:
            conn.execute(
                "ALTER TABLE poll_runs ADD COLUMN broker TEXT NOT NULL DEFAULT 'alpaca'"
            )
        except sqlite3.OperationalError:
            pass
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_poll_runs_broker_captured
                ON poll_runs(broker, captured_at DESC)
            """
        )
        _add_column_if_missing(conn, "poll_runs", "account_id", "TEXT")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_poll_runs_account_captured
                ON poll_runs(account_id, captured_at)
            """
        )
        # Index the poll_run_id JOIN keys. Without these, history queries that join
        # across many poll_runs (e.g. list_equity_history's 7-day equity series) do a
        # full table scan of account_snapshots per matched run — an O(n*m) nested loop
        # that grew to ~45s as the tables passed 35k rows, saturating the poll thread.
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_account_snapshots_poll_run
                ON account_snapshots(poll_run_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_position_snapshots_poll_run
                ON position_snapshots(poll_run_id)
            """
        )
        # Same for order_snapshots: the nightly retention prune deletes by
        # poll_run_id in 5k-id batches — unindexed, each batch full-scans while
        # holding the write lock the 20s poll loop is waiting on.
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_order_snapshots_poll_run
                ON order_snapshots(poll_run_id)
            """
        )


def save_snapshot(
    account: Account,
    positions: list[Position],
    orders: list[Order],
    *,
    broker: str = "alpaca",
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> str:
    captured_at = datetime.now(UTC).isoformat()
    init_db(db_path)

    with _connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO poll_runs (captured_at, broker, account_id) VALUES (?, ?, ?)",
            (captured_at, broker, account_id),
        )
        run_id = cursor.lastrowid

        conn.execute(
            """
            INSERT INTO account_snapshots (
                poll_run_id, account_id, status, cash, buying_power, portfolio_value
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                account.id,
                account.status,
                account.cash,
                account.buying_power,
                account.portfolio_value,
            ),
        )

        for pos in positions:
            conn.execute(
                """
                INSERT INTO position_snapshots (
                    poll_run_id, symbol, qty, side, market_value, unrealized_pl,
                    avg_entry_price, current_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    pos.symbol,
                    pos.qty,
                    pos.side,
                    pos.market_value,
                    pos.unrealized_pl,
                    pos.avg_entry_price,
                    pos.current_price,
                ),
            )

        for order in orders:
            conn.execute(
                """
                INSERT INTO order_snapshots (
                    poll_run_id, order_id, symbol, qty, side, order_type,
                    status, filled_qty, limit_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    order.id,
                    order.symbol,
                    order.qty,
                    order.side,
                    order.order_type,
                    order.status,
                    order.filled_qty,
                    order.limit_price,
                ),
            )

        conn.commit()

    return captured_at


def _prior_run_id(conn: sqlite3.Connection, broker: str | None = None) -> int | None:
    if broker:
        row = conn.execute(
            """
            SELECT id FROM poll_runs
            WHERE broker = ?
            ORDER BY captured_at DESC LIMIT 1 OFFSET 1
            """,
            (broker,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT id FROM poll_runs
            ORDER BY captured_at DESC LIMIT 1 OFFSET 1
            """
        ).fetchone()
    return int(row["id"]) if row else None


def _latest_run_id(conn: sqlite3.Connection, broker: str | None = None) -> int | None:
    if broker:
        row = conn.execute(
            """
            SELECT id FROM poll_runs
            WHERE broker = ?
            ORDER BY captured_at DESC LIMIT 1
            """,
            (broker,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM poll_runs ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()
    return int(row["id"]) if row else None


def _latest_run_id_for_account(
    conn: sqlite3.Connection, account_id: str
) -> int | None:
    row = conn.execute(
        """
        SELECT id FROM poll_runs
        WHERE account_id = ?
        ORDER BY captured_at DESC LIMIT 1
        """,
        (account_id,),
    ).fetchone()
    return int(row["id"]) if row else None


def _snapshot_from_run(conn: sqlite3.Connection, run_id: int, broker: str) -> dict:
    run = conn.execute(
        "SELECT captured_at, account_id FROM poll_runs WHERE id = ?", (run_id,)
    ).fetchone()
    account_row = conn.execute(
        "SELECT * FROM account_snapshots WHERE poll_run_id = ?", (run_id,)
    ).fetchone()
    position_rows = conn.execute(
        "SELECT * FROM position_snapshots WHERE poll_run_id = ? ORDER BY symbol",
        (run_id,),
    ).fetchall()
    order_rows = conn.execute(
        "SELECT * FROM order_snapshots WHERE poll_run_id = ? ORDER BY symbol",
        (run_id,),
    ).fetchall()

    if not run or not account_row:
        raise ValueError(f"No snapshot data for run {run_id}")

    account = {
        "id": account_row["account_id"],
        "status": account_row["status"],
        "cash": account_row["cash"],
        "buying_power": account_row["buying_power"],
        "portfolio_value": account_row["portfolio_value"],
    }

    positions = [
        {
            "symbol": row["symbol"],
            "qty": row["qty"],
            "side": row["side"],
            "market_value": row["market_value"],
            "unrealized_pl": row["unrealized_pl"],
            "avg_entry_price": row["avg_entry_price"],
            "current_price": row["current_price"],
        }
        for row in position_rows
    ]

    orders = [
        {
            "id": row["order_id"],
            "symbol": row["symbol"],
            "qty": row["qty"],
            "side": row["side"],
            "order_type": row["order_type"],
            "status": row["status"],
            "filled_qty": row["filled_qty"],
            "limit_price": row["limit_price"],
        }
        for row in order_rows
    ]

    return {
        "broker": broker,
        "captured_at": run["captured_at"],
        # Internal ledger account id (poll_runs.account_id) — NOT account["id"],
        # which is the broker-side id (an Alpaca UUID). Callers that must verify
        # which account a snapshot belongs to compare against this field.
        "account_id": run["account_id"],
        "account": account,
        "position_count": len(positions),
        "positions": positions,
        "open_order_count": len(orders),
        "open_orders": orders,
    }


def _load_analytics(conn: sqlite3.Connection, run_id: int) -> dict | None:
    row = conn.execute(
        "SELECT analytics_json FROM snapshot_analytics WHERE poll_run_id = ?",
        (run_id,),
    ).fetchone()
    if not row:
        return None
    return json.loads(row["analytics_json"])


def _snapshot_with_analytics(
    conn: sqlite3.Connection, run_id: int, broker: str
) -> dict:
    snapshot = _snapshot_from_run(conn, run_id, broker)
    analytics = _load_analytics(conn, run_id)
    if analytics is not None:
        snapshot["analytics"] = analytics
    return snapshot


def save_snapshot_analytics(
    broker: str,
    analytics: dict,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        run_id = _latest_run_id(conn, broker)
        if run_id is None:
            return
        conn.execute(
            """
            INSERT INTO snapshot_analytics (poll_run_id, analytics_json)
            VALUES (?, ?)
            ON CONFLICT(poll_run_id) DO UPDATE SET analytics_json = excluded.analytics_json
            """,
            (run_id, json.dumps(analytics)),
        )
        conn.commit()


def _run_id_at_or_before(
    conn: sqlite3.Connection, broker: str, captured_before: str
) -> int | None:
    row = conn.execute(
        """
        SELECT id FROM poll_runs
        WHERE broker = ? AND captured_at <= ?
        ORDER BY captured_at DESC
        LIMIT 1
        """,
        (broker, captured_before),
    ).fetchone()
    return int(row["id"]) if row else None


def get_snapshot_days_ago(
    broker: str,
    days: float,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict | None:
    """Nearest snapshot at or before now - days (for drift vs 1d / 7d ago)."""
    init_db(db_path)
    target = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with _connect(db_path) as conn:
        run_id = _run_id_at_or_before(conn, broker, target)
        if run_id is None:
            return None
        return _snapshot_from_run(conn, run_id, broker)


def list_equity_history(
    broker: str,
    *,
    days: int = 7,
    max_points: int = 48,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, float | str]]:
    """Downsampled equity series from poll history for charts / LLM context."""
    init_db(db_path)
    since = (datetime.now(UTC) - timedelta(days=max(1, days))).isoformat()
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT pr.id, pr.captured_at, ac.portfolio_value
            FROM poll_runs pr
            JOIN account_snapshots ac ON ac.poll_run_id = pr.id
            WHERE pr.broker = ? AND pr.captured_at >= ?
            ORDER BY pr.captured_at ASC
            """,
            (broker, since),
        ).fetchall()
    if not rows:
        return []
    if len(rows) <= max_points:
        return [
            {
                "captured_at": str(r["captured_at"]),
                "equity": float(r["portfolio_value"]),
            }
            for r in rows
        ]
    step = max(1, len(rows) // max_points)
    return [
        {
            "captured_at": str(rows[i]["captured_at"]),
            "equity": float(rows[i]["portfolio_value"]),
        }
        for i in range(0, len(rows), step)
    ]


def compute_and_save_snapshot_analytics(
    broker: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict | None:
    """Compute analytics for the latest snapshot and persist alongside the poll run."""
    from portfolio.analytics import compute_portfolio_analytics

    init_db(db_path)
    with _connect(db_path) as conn:
        run_id = _latest_run_id(conn, broker)
        if run_id is None:
            return None
        snapshot = _snapshot_from_run(conn, run_id, broker)
        prior_run_id = _prior_run_id(conn, broker)
        prior_snapshot = (
            _snapshot_from_run(
                conn,
                prior_run_id,
                broker,
            )
            if prior_run_id is not None
            else None
        )
    day_ago_snapshot = get_snapshot_days_ago(broker, 1.0, db_path=db_path)
    week_ago_snapshot = get_snapshot_days_ago(broker, 7.0, db_path=db_path)
    equity_series_7d = list_equity_history(broker, days=7, db_path=db_path)
    analytics = compute_portfolio_analytics(
        snapshot,
        prior_snapshot,
        day_ago_snapshot=day_ago_snapshot,
        week_ago_snapshot=week_ago_snapshot,
    )
    if equity_series_7d:
        analytics["equity_series_7d"] = equity_series_7d
    save_snapshot_analytics(broker, analytics, db_path=db_path)
    return analytics


def get_prior_snapshot(
    broker: str | None = None, db_path: Path = DEFAULT_DB_PATH
) -> dict | None:
    init_db(db_path)
    with _connect(db_path) as conn:
        run_id = _prior_run_id(conn, broker)
        if run_id is None:
            return None
        broker_name = broker or conn.execute(
            "SELECT broker FROM poll_runs WHERE id = ?", (run_id,)
        ).fetchone()["broker"]
        return _snapshot_from_run(conn, run_id, broker_name)


def get_latest_snapshot(
    broker: str | None = None,
    *,
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict | None:
    init_db(db_path)
    with _connect(db_path) as conn:
        run_id: int | None = None
        if account_id is not None:
            # An account-scoped ask returns THAT account's run or nothing — it
            # never falls back. Six accounts share the alpaca broker, so the
            # broker-wide latest run is usually somebody else's, and callers size
            # and gate real orders on what comes back (trading/risk_engine.py,
            # trading/proposal_intake.py). Falling back to an unkeyed
            # (account_id IS NULL) run is no safer: api/main.py:_poll_broker
            # writes NULL whenever a broker has no default trade account, so an
            # unkeyed run is a real account's portfolio with its label missing —
            # and with broker=None it can cross the paper/real firewall.
            run_id = _latest_run_id_for_account(conn, account_id)
        else:
            run_id = _latest_run_id(conn, broker)
        if run_id is None:
            return None
        broker_name = broker or conn.execute(
            "SELECT broker FROM poll_runs WHERE id = ?", (run_id,)
        ).fetchone()["broker"]
        return _snapshot_with_analytics(conn, run_id, broker_name)


def get_all_latest_snapshots(db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """Latest snapshot per ACCOUNT (not per broker).

    Multiple accounts can share a broker (e.g. two Alpaca paper accounts), so
    grouping by broker alone returns whichever account polled last — making the
    dashboard summary flicker between accounts. Grouping by (broker, account_id)
    returns one stable snapshot per account, each carrying its `account_id`.
    """
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT broker, account_id, MAX(captured_at) AS latest
            FROM poll_runs
            GROUP BY broker, account_id
            ORDER BY broker, account_id
            """
        ).fetchall()

        snapshots: list[dict] = []
        for row in rows:
            run = conn.execute(
                """
                SELECT id FROM poll_runs
                WHERE broker = ? AND captured_at = ?
                  AND COALESCE(account_id, '') = COALESCE(?, '')
                ORDER BY id DESC
                LIMIT 1
                """,
                (row["broker"], row["latest"], row["account_id"]),
            ).fetchone()
            if run:
                snap = _snapshot_with_analytics(conn, int(run["id"]), row["broker"])
                snap["account_id"] = row["account_id"]
                snapshots.append(snap)
        return snapshots
