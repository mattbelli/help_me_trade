"""Daily equity/P&L snapshots for the real Schwab accounts (read-only record).

Accurate account-value tracking straight from Schwab's OWN liquidation value +
its start-of-day-based day P&L — no FIFO matching, no cost-basis guessing. One
row per account per trading day (ET), upserted so repeated intraday captures
converge to the day's final (EOD) values. This is what makes the daily numbers
(e.g. a realized +$866 day) durable instead of vanishing at the next open.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

_ET = ZoneInfo("America/New_York")


def init_schwab_history_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schwab_account_daily (
                snapshot_date       TEXT NOT NULL,
                account_id          TEXT NOT NULL,
                last4               TEXT,
                label               TEXT,
                liquidation_value   TEXT,
                cash                TEXT,
                long_market_value   TEXT,
                day_pl              TEXT,
                captured_at         TEXT NOT NULL,
                PRIMARY KEY (snapshot_date, account_id)
            )
            """
        )
        try:
            conn.execute("ALTER TABLE schwab_account_daily ADD COLUMN futures_equity TEXT")
        except Exception:  # noqa: BLE001 — column already exists
            pass
        conn.commit()


def et_trading_date() -> str:
    return datetime.now(_ET).date().isoformat()


def record_schwab_daily(
    accounts: list[dict[str, Any]],
    *,
    snapshot_date: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Upsert one snapshot row per account for the (ET) trading day. Idempotent —
    the latest capture of the day overwrites earlier ones. Returns rows written."""
    init_schwab_history_tables(db_path)
    day = snapshot_date or et_trading_date()
    ts = _now()
    written = 0
    with _connect(db_path) as conn:
        for a in accounts:
            account_id = a.get("id")
            if not account_id:
                continue
            conn.execute(
                """
                INSERT INTO schwab_account_daily (
                    snapshot_date, account_id, last4, label, liquidation_value,
                    cash, long_market_value, day_pl, futures_equity, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_date, account_id) DO UPDATE SET
                    last4=excluded.last4,
                    label=excluded.label,
                    liquidation_value=excluded.liquidation_value,
                    cash=excluded.cash,
                    long_market_value=excluded.long_market_value,
                    day_pl=excluded.day_pl,
                    futures_equity=excluded.futures_equity,
                    captured_at=excluded.captured_at
                """,
                (
                    day,
                    str(account_id),
                    a.get("last4"),
                    a.get("label"),
                    str(a.get("liquidation_value") or 0),
                    str(a.get("cash") or 0),
                    str(a.get("long_market_value") or 0),
                    str(a.get("day_pl") or 0),
                    str(a.get("futures_equity") or 0),
                    ts,
                ),
            )
            written += 1
        conn.commit()
    return written


def prior_futures_equity(
    account_id: str,
    *,
    before_date: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> float | None:
    """The futures sub-account equity at the last capture of the most recent day
    before ``before_date`` (default today, ET) — the start-of-day baseline for
    the futures side, which Schwab's initialBalances does not include. None when
    no earlier row (or a row from before the column existed)."""
    init_schwab_history_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT futures_equity FROM schwab_account_daily"
            " WHERE account_id = ? AND snapshot_date < ?"
            " ORDER BY snapshot_date DESC LIMIT 1",
            (account_id, before_date or et_trading_date()),
        ).fetchone()
    try:
        return float(row["futures_equity"]) if row and row["futures_equity"] else None
    except (TypeError, ValueError):
        return None


def list_schwab_daily(
    account_id: str | None = None,
    *,
    limit: int = 400,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Daily snapshots, newest first (optionally for one account)."""
    init_schwab_history_tables(db_path)
    sql = "SELECT * FROM schwab_account_daily"
    params: list[Any] = []
    if account_id:
        sql += " WHERE account_id = ?"
        params.append(account_id)
    sql += " ORDER BY snapshot_date DESC, account_id LIMIT ?"
    params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]
