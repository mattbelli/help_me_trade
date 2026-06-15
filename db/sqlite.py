"""SQLite storage for polled broker snapshots."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from brokers.base import Account, Order, Position

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(
    os.environ.get("TRADING_DB_PATH", str(ROOT / "data" / "trading.db"))
)


def _connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


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


def save_snapshot(
    account: Account,
    positions: list[Position],
    orders: list[Order],
    *,
    broker: str = "alpaca",
    db_path: Path = DEFAULT_DB_PATH,
) -> str:
    captured_at = datetime.now(UTC).isoformat()
    init_db(db_path)

    with _connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO poll_runs (captured_at, broker) VALUES (?, ?)",
            (captured_at, broker),
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


def _snapshot_from_run(conn: sqlite3.Connection, run_id: int, broker: str) -> dict:
    run = conn.execute(
        "SELECT captured_at FROM poll_runs WHERE id = ?", (run_id,)
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
        "account": account,
        "position_count": len(positions),
        "positions": positions,
        "open_order_count": len(orders),
        "open_orders": orders,
    }


def get_latest_snapshot(
    broker: str | None = None, db_path: Path = DEFAULT_DB_PATH
) -> dict | None:
    init_db(db_path)
    with _connect(db_path) as conn:
        run_id = _latest_run_id(conn, broker)
        if run_id is None:
            return None
        broker_name = broker or conn.execute(
            "SELECT broker FROM poll_runs WHERE id = ?", (run_id,)
        ).fetchone()["broker"]
        return _snapshot_from_run(conn, run_id, broker_name)


def get_all_latest_snapshots(db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT broker, MAX(captured_at) AS latest
            FROM poll_runs
            GROUP BY broker
            ORDER BY broker
            """
        ).fetchall()

        snapshots: list[dict] = []
        for row in rows:
            run = conn.execute(
                """
                SELECT id FROM poll_runs
                WHERE broker = ? AND captured_at = ?
                LIMIT 1
                """,
                (row["broker"], row["latest"]),
            ).fetchone()
            if run:
                snapshots.append(
                    _snapshot_from_run(conn, int(run["id"]), row["broker"])
                )
        return snapshots
