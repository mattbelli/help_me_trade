"""Chart layouts, drawings, and price alerts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect


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
                repeat INTEGER NOT NULL DEFAULT 0,
                last_triggered_at TEXT,
                last_fire_price REAL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_chart_layouts_symbol
                ON chart_layouts(symbol, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_price_alerts_symbol
                ON price_alerts(symbol, triggered);

            CREATE TABLE IF NOT EXISTS chart_drawings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                kind TEXT NOT NULL,
                data TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_chart_drawings_symbol
                ON chart_drawings(symbol, created_at);
            """
        )
        # Idempotent migration for DBs created before repeat/last_triggered_at.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(price_alerts)")}
        if "repeat" not in cols:
            conn.execute(
                "ALTER TABLE price_alerts ADD COLUMN repeat INTEGER NOT NULL DEFAULT 0"
            )
        if "last_triggered_at" not in cols:
            conn.execute(
                "ALTER TABLE price_alerts ADD COLUMN last_triggered_at TEXT"
            )
        if "last_fire_price" not in cols:
            conn.execute(
                "ALTER TABLE price_alerts ADD COLUMN last_fire_price REAL"
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
        # Repeat (persistent) alerts stay active forever — they re-arm after each
        # crossing and only leave the list when deleted.
        query += " AND (triggered = 0 OR repeat = 1)"
    query += " ORDER BY created_at DESC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def create_price_alert(
    *,
    symbol: str,
    price: float,
    direction: str = "cross",
    note: str = "",
    repeat: bool = False,
) -> dict:
    init_chart_features()
    now = datetime.now(UTC).isoformat()
    sym = symbol.strip().upper()
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO price_alerts
                (symbol, price, direction, note, triggered, repeat, created_at)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (sym, price, direction, note, 1 if repeat else 0, now),
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


def update_price_alert(
    alert_id: int,
    *,
    price: float | None = None,
    direction: str | None = None,
    note: str | None = None,
    repeat: bool | None = None,
) -> dict | None:
    """Edit an alert in place (only the fields provided). Editing RE-ARMS it
    (triggered=0) so a one-shot that already fired becomes active again at its new
    level. Returns the updated row, or None if no alert with that id exists."""
    init_chart_features()
    sets: list[str] = []
    params: list = []
    if price is not None:
        sets.append("price = ?")
        params.append(float(price))
    if direction is not None:
        sets.append("direction = ?")
        params.append(direction)
    if note is not None:
        sets.append("note = ?")
        params.append(note)
    if repeat is not None:
        sets.append("repeat = ?")
        params.append(1 if repeat else 0)
    sets.append("triggered = 0")  # editing re-arms
    with _connect() as conn:
        cursor = conn.execute(
            f"UPDATE price_alerts SET {', '.join(sets)} WHERE id = ?",
            (*params, alert_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM price_alerts WHERE id = ?", (alert_id,)
        ).fetchone()
    return dict(row) if row else None


def mark_alert_triggered(
    alert_id: int, *, repeat: bool = False, fire_price: float | None = None
) -> bool:
    """Record that an alert fired; return True if this call claimed the fire.

    One-shot (repeat=False): set triggered=1 so it leaves the active list. The
    UPDATE is conditional on ``triggered = 0`` so two concurrent evaluations
    (e.g. overlapping ext-price polls) can't both fire — only the writer that
    flips the row (rowcount == 1) returns True; the loser returns False and must
    not emit a notification.
    Persistent (repeat=True): stamp last_triggered_at and record last_fire_price
    (the re-arm anchor) but keep it active so it re-arms for the next crossing.
    Always returns True for repeat alerts (they are debounced by the cooldown +
    the price-band hysteresis instead of the triggered flag).
    """
    init_chart_features()
    now = datetime.now(UTC).isoformat()
    with _connect() as conn:
        if repeat:
            conn.execute(
                "UPDATE price_alerts SET last_triggered_at = ?, last_fire_price = ? "
                "WHERE id = ?",
                (now, fire_price, alert_id),
            )
            conn.commit()
            return True
        cursor = conn.execute(
            "UPDATE price_alerts SET triggered = 1, last_triggered_at = ? "
            "WHERE id = ? AND triggered = 0",
            (now, alert_id),
        )
        conn.commit()
        return cursor.rowcount == 1


def rearm_repeat_alert(alert_id: int) -> None:
    """Clear a repeat alert's last_fire_price so it can fire on the next crossing.

    Called once price has retreated at least the hysteresis band away from where
    the alert last fired — the "move away, then cross back" gate that stops a
    price dithering at the level from re-firing on the 60s cooldown alone.
    """
    init_chart_features()
    with _connect() as conn:
        conn.execute(
            "UPDATE price_alerts SET last_fire_price = NULL WHERE id = ?",
            (alert_id,),
        )
        conn.commit()


def _parse_drawing_row(row) -> dict:
    d = dict(row)
    d["data"] = json.loads(d["data"])
    return d


def list_chart_drawings(*, symbol: str | None = None) -> list[dict]:
    """All drawings, optionally filtered by symbol; newest last. `data` parsed to a dict."""
    init_chart_features()
    query = "SELECT * FROM chart_drawings WHERE 1=1"
    params: list = []
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol.strip().upper())
    query += " ORDER BY created_at ASC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_parse_drawing_row(row) for row in rows]


def create_chart_drawing(*, symbol: str, kind: str, data: dict) -> dict:
    """Insert a drawing (created_at = UTC ISO now). Returns the inserted row (data parsed)."""
    init_chart_features()
    now = datetime.now(UTC).isoformat()
    sym = symbol.strip().upper()
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO chart_drawings (symbol, kind, data, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (sym, kind, json.dumps(data), now),
        )
        conn.commit()
        row_id = int(cursor.lastrowid)
        row = conn.execute(
            "SELECT * FROM chart_drawings WHERE id = ?", (row_id,)
        ).fetchone()
    return _parse_drawing_row(row) if row else {}


def update_chart_drawing(
    drawing_id: int, *, data: dict | None = None, kind: str | None = None
) -> dict | None:
    """Patch data and/or kind. Returns updated row (data parsed) or None if not found."""
    init_chart_features()
    sets: list[str] = []
    params: list = []
    if data is not None:
        sets.append("data = ?")
        params.append(json.dumps(data))
    if kind is not None:
        sets.append("kind = ?")
        params.append(kind)
    if not sets:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM chart_drawings WHERE id = ?", (drawing_id,)
            ).fetchone()
        return _parse_drawing_row(row) if row else None
    with _connect() as conn:
        cursor = conn.execute(
            f"UPDATE chart_drawings SET {', '.join(sets)} WHERE id = ?",
            (*params, drawing_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM chart_drawings WHERE id = ?", (drawing_id,)
        ).fetchone()
    return _parse_drawing_row(row) if row else None


def delete_chart_drawing(drawing_id: int) -> bool:
    """Delete by id. True if a row was removed, else False."""
    init_chart_features()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM chart_drawings WHERE id = ?", (drawing_id,)
        )
        conn.commit()
    return cursor.rowcount > 0
