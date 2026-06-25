"""Stored reports — morning levels and future report kinds."""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now


def _db_path(db_path: Path | None) -> Path:
    return db_path if db_path is not None else DEFAULT_DB_PATH


def init_reports_tables(db_path: Path | None = None) -> None:
    resolved = _db_path(db_path)
    with _connect(resolved) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                symbol TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                generated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reports_kind_generated
                ON reports (kind, generated_at DESC);
            """
        )
        conn.commit()


def _row_to_report(row: Any) -> dict[str, Any]:
    payload_raw = row["payload_json"]
    try:
        payload = json.loads(payload_raw) if payload_raw else {}
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "id": row["id"],
        "kind": row["kind"],
        "symbol": row["symbol"],
        "title": row["title"],
        "body": row["body"],
        "payload": payload,
        "generated_at": row["generated_at"],
    }


def save_report(
    kind: str,
    symbol: str,
    title: str,
    body: str,
    payload: dict[str, Any],
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    resolved = _db_path(db_path)
    init_reports_tables(resolved)
    report_id = uuid.uuid4().hex[:12]
    now = _now()
    with _connect(resolved) as conn:
        conn.execute(
            """
            INSERT INTO reports (id, kind, symbol, title, body, payload_json, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                kind,
                symbol.strip().upper(),
                title,
                body,
                json.dumps(payload),
                now,
            ),
        )
        conn.commit()
    return {
        "id": report_id,
        "kind": kind,
        "symbol": symbol.strip().upper(),
        "title": title,
        "body": body,
        "payload": payload,
        "generated_at": now,
    }


def list_reports(*, limit: int = 30, db_path: Path | None = None) -> list[dict[str, Any]]:
    resolved = _db_path(db_path)
    init_reports_tables(resolved)
    cap = max(1, min(int(limit), 200))
    with _connect(resolved) as conn:
        rows = conn.execute(
            """
            SELECT id, kind, symbol, title, body, payload_json, generated_at
            FROM reports
            ORDER BY generated_at DESC
            LIMIT ?
            """,
            (cap,),
        ).fetchall()
    return [_row_to_report(row) for row in rows]


def get_report(report_id: str, *, db_path: Path | None = None) -> dict[str, Any] | None:
    resolved = _db_path(db_path)
    init_reports_tables(resolved)
    with _connect(resolved) as conn:
        row = conn.execute(
            """
            SELECT id, kind, symbol, title, body, payload_json, generated_at
            FROM reports
            WHERE id = ?
            """,
            (report_id.strip(),),
        ).fetchone()
    return _row_to_report(row) if row else None


def morning_levels_exists_for_day(
    symbol: str,
    et_date: date,
    *,
    db_path: Path | None = None,
) -> bool:
    """True when a morning_levels report already exists for symbol on this ET date."""
    resolved = _db_path(db_path)
    init_reports_tables(resolved)
    sym = symbol.strip().upper()
    day = et_date.isoformat()
    with _connect(resolved) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM reports
            WHERE kind = 'morning_levels'
              AND symbol = ?
              AND json_extract(payload_json, '$.et_date') = ?
            LIMIT 1
            """,
            (sym, day),
        ).fetchone()
    return row is not None
