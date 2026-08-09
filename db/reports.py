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

# Human label per kind, served on every report row as `kind_label`. It lives
# here — the module that defines what a report row IS — so both the renderer and
# the API read one map; signals/reports.py owned it before, which the clients
# could not import, leaving the phone's masthead to render the raw kind
# ("flow decomp"). The book entries mirror research/books.BOOKS names but are
# written out rather than imported: db/ importing research/ would invert the
# layering (research.books already reaches into db). A test pins them together.
REPORT_KIND_LABELS: dict[str, str] = {
    "morning_levels": "Morning Levels",
    "levels": "Levels",
    "morning_brief": "Morning Brief",
    "evening_brief": "Evening Brief",
    "on_request": "On-Request Report",
    "flow_decomp": "Flow Decomposition",
    "overnight_flow": "Overnight Flow",
    "book_deepseek": "Deep Seek Book",
    "book_fable": "Fable Book",
    "book_panel": "Claude Panel Book",
    "book_matt": "Matt + LLM Book",
}


def kind_label(kind: str) -> str:
    """Display name for a report kind — falls back to a readable form of the
    kind itself rather than a wrong-but-tidy default."""
    known = REPORT_KIND_LABELS.get(kind)
    return known if known else str(kind).replace("_", " ").strip().title()


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
        # Idempotent migration: `format` distinguishes plaintext bodies (the
        # deterministic levels report) from rendered HTML (LLM-authored briefs).
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(reports)")}
        if "format" not in cols:
            conn.execute(
                "ALTER TABLE reports ADD COLUMN format TEXT NOT NULL DEFAULT 'text'"
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
    keys = row.keys() if hasattr(row, "keys") else []
    fmt = row["format"] if "format" in keys else "text"
    return {
        "id": row["id"],
        "kind": row["kind"],
        "kind_label": kind_label(row["kind"]),
        "symbol": row["symbol"],
        "title": row["title"],
        "body": row["body"],
        "format": fmt or "text",
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
    format: str = "text",
    db_path: Path | None = None,
) -> dict[str, Any]:
    resolved = _db_path(db_path)
    init_reports_tables(resolved)
    report_id = uuid.uuid4().hex[:12]
    now = _now()
    sym = symbol.strip().upper()
    # markdown became first-class 07-23 (book analyst reports render like the
    # deep-research viewer); anything unknown still collapses to text.
    cleaned = str(format).strip().lower()
    fmt = cleaned if cleaned in ("html", "markdown") else "text"
    with _connect(resolved) as conn:
        conn.execute(
            """
            INSERT INTO reports
                (id, kind, symbol, title, body, payload_json, generated_at, format)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (report_id, kind, sym, title, body, json.dumps(payload), now, fmt),
        )
        conn.commit()
    return {
        "id": report_id,
        "kind": kind,
        "kind_label": kind_label(kind),
        "symbol": sym,
        "title": title,
        "body": body,
        "format": fmt,
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
            SELECT id, kind, symbol, title, body, format, payload_json, generated_at
            FROM reports
            ORDER BY generated_at DESC
            LIMIT ?
            """,
            (cap,),
        ).fetchall()
    return [_row_to_report(row) for row in rows]


def recent_reports_by_kind(
    kind: str, *, limit: int = 10, db_path: Path | None = None
) -> list[dict[str, Any]]:
    resolved = _db_path(db_path)
    init_reports_tables(resolved)
    cap = max(1, min(int(limit), 200))
    with _connect(resolved) as conn:
        rows = conn.execute(
            """
            SELECT id, kind, symbol, title, body, format, payload_json, generated_at
            FROM reports
            WHERE kind = ?
            ORDER BY generated_at DESC
            LIMIT ?
            """,
            (kind.strip(), cap),
        ).fetchall()
    return [_row_to_report(row) for row in rows]


def get_report(report_id: str, *, db_path: Path | None = None) -> dict[str, Any] | None:
    resolved = _db_path(db_path)
    init_reports_tables(resolved)
    with _connect(resolved) as conn:
        row = conn.execute(
            """
            SELECT id, kind, symbol, title, body, format, payload_json, generated_at
            FROM reports
            WHERE id = ?
            """,
            (report_id.strip(),),
        ).fetchone()
    return _row_to_report(row) if row else None


def report_exists_for_day(
    kind: str,
    et_date: date,
    *,
    dedup_key: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """True when a report of ``kind`` already exists for this ET date.

    Generic dedup for scheduled jobs: each job stamps ``et_date`` (and an
    optional ``dedup_key`` for params, e.g. the symbol set) into its payload so
    a job fires at most once per (kind, params, ET-day)."""
    resolved = _db_path(db_path)
    init_reports_tables(resolved)
    day = et_date.isoformat()
    with _connect(resolved) as conn:
        if dedup_key is None:
            row = conn.execute(
                """
                SELECT 1 FROM reports
                WHERE kind = ?
                  AND json_extract(payload_json, '$.et_date') = ?
                LIMIT 1
                """,
                (kind, day),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT 1 FROM reports
                WHERE kind = ?
                  AND json_extract(payload_json, '$.et_date') = ?
                  AND json_extract(payload_json, '$.dedup_key') = ?
                LIMIT 1
                """,
                (kind, day, dedup_key),
            ).fetchone()
    return row is not None


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
