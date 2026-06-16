"""Chart layouts, drawings, and price alerts."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from db.base import DEFAULT_DB_PATH, connect as _connect


def init_chart_features(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chart_layouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                resolution TEXT NOT NULL DEFAULT '1D',
                content TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS price_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                direction TEXT NOT NULL DEFAULT 'cross',
                note TEXT NOT NULL DEFAULT '',
                triggered INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_chart_layouts_symbol
                ON chart_layouts(symbol, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_price_alerts_symbol
                ON price_alerts(symbol, triggered);
            """
        )


def list_chart_layouts(symbol: str | None = None) -> list[dict]:
    init_chart_features()
    with _connect() as conn:
        if symbol:
            rows = conn.execute(
                """
                SELECT id, symbol, name, resolution, updated_at
                FROM chart_layouts
                WHERE symbol = ?
                ORDER BY updated_at DESC
                """,
                (symbol.strip().upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, symbol, name, resolution, updated_at
                FROM chart_layouts
                ORDER BY updated_at DESC
                """
            ).fetchall()
    return [dict(row) for row in rows]


def get_chart_layout(chart_id: int) -> dict | None:
    init_chart_features()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM chart_layouts WHERE id = ?", (chart_id,)
        ).fetchone()
    return dict(row) if row else None


def save_chart_layout(
    *,
    symbol: str,
    name: str,
    content: str,
    resolution: str = "1D",
    chart_id: int | None = None,
) -> dict:
    init_chart_features()
    now = datetime.now(UTC).isoformat()
    sym = symbol.strip().upper()
    with _connect() as conn:
        if chart_id:
            conn.execute(
                """
                UPDATE chart_layouts
                SET symbol = ?, name = ?, resolution = ?, content = ?, updated_at = ?
                WHERE id = ?
                """,
                (sym, name, resolution, content, now, chart_id),
            )
            row_id = chart_id
        else:
            cursor = conn.execute(
                """
                INSERT INTO chart_layouts (symbol, name, resolution, content, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sym, name, resolution, content, now),
            )
            row_id = int(cursor.lastrowid)
        conn.commit()
    saved = get_chart_layout(row_id)
    return saved or {}


def delete_chart_layout(chart_id: int) -> bool:
    init_chart_features()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM chart_layouts WHERE id = ?", (chart_id,))
        conn.commit()
    return cursor.rowcount > 0


def list_price_alerts(*, symbol: str | None = None, active_only: bool = True) -> list[dict]:
    init_chart_features()
    query = "SELECT * FROM price_alerts WHERE 1=1"
    params: list = []
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol.strip().upper())
    if active_only:
        query += " AND triggered = 0"
    query += " ORDER BY created_at DESC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def list_price_alerts_for_symbol(symbol: str) -> list[dict]:
    return list_price_alerts(symbol=symbol, active_only=True)


def create_price_alert(
    *,
    symbol: str,
    price: float,
    direction: str = "cross",
    note: str = "",
) -> dict:
    init_chart_features()
    now = datetime.now(UTC).isoformat()
    sym = symbol.strip().upper()
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO price_alerts (symbol, price, direction, note, triggered, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (sym, price, direction, note, now),
        )
        conn.commit()
        row_id = int(cursor.lastrowid)
        row = conn.execute(
            "SELECT * FROM price_alerts WHERE id = ?", (row_id,)
        ).fetchone()
    return dict(row) if row else {}


def delete_price_alert(alert_id: int) -> bool:
    init_chart_features()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM price_alerts WHERE id = ?", (alert_id,))
        conn.commit()
    return cursor.rowcount > 0


def mark_alert_triggered(alert_id: int) -> None:
    init_chart_features()
    with _connect() as conn:
        conn.execute(
            "UPDATE price_alerts SET triggered = 1 WHERE id = ?", (alert_id,)
        )
        conn.commit()
