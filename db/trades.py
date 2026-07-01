"""Trade ledger persistence and analytics."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now
from db.fifo_round_trips import fifo_round_trip_stats
from db.schema_helpers import _add_column_if_missing
from imports.base import ParsedTradeEvent
from imports.money import decimal_to_str, parse_dollar


def init_trade_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trade_accounts (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL UNIQUE,
                broker TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trade_events (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                source TEXT NOT NULL,
                event_type TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                symbol TEXT,
                side TEXT,
                quantity TEXT,
                price TEXT,
                fees TEXT NOT NULL DEFAULT '0',
                amount TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                external_key TEXT NOT NULL,
                raw_json TEXT,
                imported_at TEXT NOT NULL,
                FOREIGN KEY (account_id) REFERENCES trade_accounts(id),
                UNIQUE (account_id, external_key)
            );

            CREATE TABLE IF NOT EXISTS trade_import_batches (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                source TEXT NOT NULL,
                filename TEXT,
                from_date TEXT,
                to_date TEXT,
                rows_parsed INTEGER NOT NULL,
                rows_inserted INTEGER NOT NULL,
                rows_skipped INTEGER NOT NULL,
                rows_duplicate INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (account_id) REFERENCES trade_accounts(id)
            );

            CREATE INDEX IF NOT EXISTS idx_trade_events_date
                ON trade_events(trade_date DESC);
            CREATE INDEX IF NOT EXISTS idx_trade_events_symbol
                ON trade_events(symbol, trade_date DESC);
            CREATE INDEX IF NOT EXISTS idx_trade_events_account_date
                ON trade_events(account_id, trade_date DESC);

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
        for ddl in (
            "ALTER TABLE trade_accounts ADD COLUMN last_synced_at TEXT",
            "ALTER TABLE trade_accounts ADD COLUMN broker_external_id TEXT",
            "ALTER TABLE trade_events ADD COLUMN broker_txn_id TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        for column, decl in (
            ("connection_id", "TEXT"),
            ("enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("execution_capable", "INTEGER NOT NULL DEFAULT 0"),
            ("is_default", "INTEGER NOT NULL DEFAULT 0"),
        ):
            _add_column_if_missing(conn, "trade_accounts", column, decl)
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_events_broker_txn
                ON trade_events(account_id, broker_txn_id)
                WHERE broker_txn_id IS NOT NULL
            """
        )


def get_or_create_broker_account(
    broker: str,
    *,
    label: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_trade_tables(db_path)
    broker = broker.strip().lower()
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, label, broker, created_at, last_synced_at, broker_external_id
            FROM trade_accounts WHERE broker = ? ORDER BY created_at LIMIT 1
            """,
            (broker,),
        ).fetchone()
    if row:
        return dict(row)
    return create_trade_account(label or broker.title(), broker, db_path=db_path)


def set_account_last_synced(
    account_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    init_trade_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE trade_accounts SET last_synced_at = ? WHERE id = ?",
            (_now(), account_id),
        )


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "source": row["source"],
        "event_type": row["event_type"],
        "trade_date": row["trade_date"],
        "symbol": row["symbol"],
        "side": row["side"],
        "quantity": row["quantity"],
        "price": row["price"],
        "fees": row["fees"],
        "amount": row["amount"],
        "description": row["description"],
        "external_key": row["external_key"],
        "imported_at": row["imported_at"],
    }


def list_trade_accounts(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    init_trade_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, label, broker, created_at, last_synced_at
            FROM trade_accounts ORDER BY label
            """
        ).fetchall()
    return [dict(row) for row in rows]


def create_trade_account(
    label: str,
    broker: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_trade_tables(db_path)
    account_id = f"acct_{uuid.uuid4().hex[:12]}"
    created_at = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO trade_accounts (id, label, broker, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (account_id, label.strip(), broker.strip().lower(), created_at),
        )
    return {
        "id": account_id,
        "label": label.strip(),
        "broker": broker.strip().lower(),
        "created_at": created_at,
    }


def latest_trade_date(
    account_id: str,
    *,
    source: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> date | None:
    """Most recent trade_date on record for an account, or None.

    Used to sync brokers incrementally. Pass ``source`` to scope to one import
    path (e.g. ``schwab_json_import`` vs ``schwab_api``) — the API sync uses the
    JSON boundary to know where a file export ends (never re-pull those, since
    the dedup key differs and would double-count) and its own ``schwab_api``
    cursor to re-fetch a rolling recent window without dropping same-day fills.
    """
    init_trade_tables(db_path)
    sql = "SELECT MAX(trade_date) FROM trade_events WHERE account_id = ?"
    params: list[Any] = [account_id]
    if source is not None:
        sql += " AND source = ?"
        params.append(source)
    with _connect(db_path) as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    raw = row[0] if row else None
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def get_trade_account(
    account_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    init_trade_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, label, broker, created_at FROM trade_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
    return dict(row) if row else None


def list_trade_events(
    *,
    account_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    symbol: str | None = None,
    event_type: str | None = None,
    limit: int = 500,
    offset: int = 0,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_trade_tables(db_path)
    clauses: list[str] = []
    params: list[Any] = []

    if account_id:
        clauses.append("account_id = ?")
        params.append(account_id)
    if from_date:
        clauses.append("trade_date >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("trade_date <= ?")
        params.append(to_date)
    if symbol:
        clauses.append("UPPER(symbol) = ?")
        params.append(symbol.upper())
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])

    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, account_id, source, event_type, trade_date, symbol, side,
                   quantity, price, fees, amount, description, external_key, imported_at
            FROM trade_events
            {where}
            ORDER BY trade_date DESC, imported_at DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
    return [_row_to_event(row) for row in rows]


@dataclass
class ImportWriteResult:
    batch_id: str | None
    parsed: int
    inserted: int
    duplicates: int
    skipped: int
    warnings: list[str]
    failed: list[dict[str, Any]]


class _ImportBatchFailed(Exception):
    pass


def _validate_trade_event(event: ParsedTradeEvent) -> None:
    if not event.external_key:
        raise ValueError("missing external_key")
    if event.trade_date is None:
        raise ValueError("missing trade_date")


def get_import_dedup_sets(
    account_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[set[str], set[str]]:
    """Return existing external_key and broker_txn_id sets for import preview."""
    init_trade_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT external_key, broker_txn_id
            FROM trade_events
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchall()
    external_keys = {row["external_key"] for row in rows}
    broker_txn_ids = {
        row["broker_txn_id"] for row in rows if row["broker_txn_id"]
    }
    return external_keys, broker_txn_ids


def is_duplicate_event(
    event: ParsedTradeEvent,
    *,
    external_keys: set[str],
    broker_txn_ids: set[str],
) -> bool:
    if event.broker_txn_id and event.broker_txn_id in broker_txn_ids:
        return True
    return event.external_key in external_keys


def import_trade_events(
    account_id: str,
    events: list[ParsedTradeEvent],
    *,
    source: str,
    filename: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    skipped: int = 0,
    warnings: list[str] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> ImportWriteResult:
    init_trade_tables(db_path)
    if get_trade_account(account_id, db_path=db_path) is None:
        raise ValueError(f"Unknown account_id: {account_id}")

    batch_id = f"batch_{uuid.uuid4().hex[:12]}"
    imported_at = _now()
    inserted = 0
    duplicates = 0
    failed: list[dict[str, Any]] = []

    with _connect(db_path) as conn:
        try:
            with conn:
                for row_index, event in enumerate(events):
                    try:
                        _validate_trade_event(event)
                        event_id = f"evt_{uuid.uuid4().hex[:12]}"
                        conn.execute(
                            """
                            INSERT INTO trade_events (
                                id, account_id, source, event_type, trade_date, symbol, side,
                                quantity, price, fees, amount, description, external_key,
                                broker_txn_id, raw_json, imported_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                event_id,
                                account_id,
                                source,
                                event.event_type,
                                event.trade_date.isoformat(),
                                event.symbol,
                                event.side,
                                decimal_to_str(event.quantity),
                                decimal_to_str(event.price),
                                decimal_to_str(event.fees) or "0",
                                decimal_to_str(event.amount) or "0",
                                event.description,
                                event.external_key,
                                event.broker_txn_id,
                                json.dumps(event.raw),
                                imported_at,
                            ),
                        )
                        inserted += 1
                    except sqlite3.IntegrityError:
                        duplicates += 1
                    except Exception as exc:
                        failed.append({"row_index": row_index, "reason": str(exc)})

                if failed:
                    raise _ImportBatchFailed()

                conn.execute(
                    """
                    INSERT INTO trade_import_batches (
                        id, account_id, source, filename, from_date, to_date,
                        rows_parsed, rows_inserted, rows_skipped, rows_duplicate, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        account_id,
                        source,
                        filename,
                        from_date,
                        to_date,
                        len(events),
                        inserted,
                        skipped,
                        duplicates,
                        imported_at,
                    ),
                )
        except _ImportBatchFailed:
            inserted = 0
            batch_id = None

    return ImportWriteResult(
        batch_id=batch_id,
        parsed=len(events),
        inserted=inserted,
        duplicates=duplicates if not failed else 0,
        skipped=skipped,
        warnings=warnings or [],
        failed=failed,
    )


def _sum_amounts(events: list[dict[str, Any]]) -> Decimal:
    total = Decimal("0")
    for event in events:
        total += parse_dollar(event.get("amount"))
    return total


def trade_summary_stats(
    *,
    account_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    events = list_trade_events(
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
        limit=100_000,
        offset=0,
        db_path=db_path,
    )
    trade_events = [e for e in events if e["event_type"] == "trade"]
    fifo_events = [e for e in events if e["event_type"] in ("trade", "option_expired")]
    fees = sum(parse_dollar(e["fees"]) for e in events)
    net_amount = _sum_amounts(events)

    wins, losses, round_trips = fifo_round_trip_stats(fifo_events)

    return {
        "event_count": len(events),
        "trade_count": len(trade_events),
        "net_amount": decimal_to_str(net_amount),
        "total_fees": decimal_to_str(fees),
        "round_trips": round_trips,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / round_trips, 4) if round_trips else None,
        "from_date": from_date,
        "to_date": to_date,
    }


def daily_trade_stats(
    *,
    account_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    events = list_trade_events(
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
        limit=100_000,
        offset=0,
        db_path=db_path,
    )
    by_day: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    counts: dict[str, int] = defaultdict(int)
    for event in events:
        day = event["trade_date"]
        by_day[day] += parse_dollar(event["amount"])
        counts[day] += 1

    return [
        {
            "date": day,
            "net_amount": decimal_to_str(amount),
            "event_count": counts[day],
        }
        for day, amount in sorted(by_day.items())
    ]


def symbol_trade_stats(
    *,
    account_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    events = list_trade_events(
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
        limit=100_000,
        offset=0,
        db_path=db_path,
    )
    by_symbol: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"trade_count": 0, "net_amount": Decimal("0")}
    )
    for event in events:
        symbol = event.get("symbol") or "(cash)"
        by_symbol[symbol]["trade_count"] += 1
        by_symbol[symbol]["net_amount"] += parse_dollar(event["amount"])

    rows = [
        {
            "symbol": symbol,
            "trade_count": data["trade_count"],
            "net_amount": decimal_to_str(data["net_amount"]),
        }
        for symbol, data in by_symbol.items()
    ]
    rows.sort(key=lambda row: abs(parse_dollar(row["net_amount"])), reverse=True)
    return rows

