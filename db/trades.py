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

from db.base import DEFAULT_DB_PATH, connect as _connect, now_iso as _now
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
            """
        )
        for ddl in (
            "ALTER TABLE trade_accounts ADD COLUMN last_synced_at TEXT",
            "ALTER TABLE trade_accounts ADD COLUMN broker_external_id TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass


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
    batch_id: str
    parsed: int
    inserted: int
    duplicates: int
    skipped: int
    warnings: list[str]


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

    with _connect(db_path) as conn:
        for event in events:
            event_id = f"evt_{uuid.uuid4().hex[:12]}"
            try:
                conn.execute(
                    """
                    INSERT INTO trade_events (
                        id, account_id, source, event_type, trade_date, symbol, side,
                        quantity, price, fees, amount, description, external_key,
                        raw_json, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        json.dumps(event.raw),
                        imported_at,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                duplicates += 1

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

    return ImportWriteResult(
        batch_id=batch_id,
        parsed=len(events),
        inserted=inserted,
        duplicates=duplicates,
        skipped=skipped,
        warnings=warnings or [],
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
    fees = sum(parse_dollar(e["fees"]) for e in events)
    net_amount = _sum_amounts(events)

    wins, losses, round_trips = _fifo_round_trip_stats(trade_events)

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


def _fifo_round_trip_stats(trade_events: list[dict[str, Any]]) -> tuple[int, int, int]:
    """FIFO round-trip win/loss counts per symbol (stock trades only in v1)."""
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in trade_events:
        symbol = event.get("symbol")
        if not symbol or " " in symbol:
            continue
        by_symbol[symbol].append(event)

    wins = 0
    losses = 0
    round_trips = 0

    for symbol, rows in by_symbol.items():
        sorted_rows = sorted(rows, key=lambda r: (r["trade_date"], r["id"]))
        lots: list[tuple[Decimal, Decimal, Decimal]] = []

        for row in sorted_rows:
            side = (row.get("side") or "").lower()
            qty = parse_dollar(row.get("quantity"))
            if qty <= 0:
                continue
            price = parse_dollar(row.get("price"))
            fees = parse_dollar(row.get("fees"))
            amount = parse_dollar(row.get("amount"))

            if side == "buy":
                cost_per = (-amount - fees) / qty if qty else Decimal("0")
                lots.append((qty, cost_per, fees))
                continue

            if side != "sell":
                continue

            remaining = qty
            proceeds = amount
            sell_fees = fees
            cost_basis = Decimal("0")
            matched_qty = Decimal("0")

            while remaining > 0 and lots:
                lot_qty, lot_cost, lot_fees = lots[0]
                take = min(remaining, lot_qty)
                cost_basis += take * lot_cost
                matched_qty += take
                remaining -= take
                if take == lot_qty:
                    lots.pop(0)
                else:
                    lots[0] = (lot_qty - take, lot_cost, lot_fees)

            if matched_qty > 0:
                pnl = proceeds - cost_basis - sell_fees
                round_trips += 1
                if pnl > 0:
                    wins += 1
                elif pnl < 0:
                    losses += 1

    return wins, losses, round_trips
