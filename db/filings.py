"""Filings feed events — insider Form 4 buys, 13D stakes, congressional trades.

A FEED, not a signal: rows are facts the founder eyeballs to decide if a name
deserves a look. No scoring, no outcome tracking (the idea/event ledger spec
covers how that would bolt on later if the feed earns it).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

logger = logging.getLogger(__name__)

KINDS = frozenset({"insider_buy", "stake_13d", "congress_trade"})
_RECENT_CAP = 300


def init_filings_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS filings_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                symbol TEXT NOT NULL DEFAULT '',
                person TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT '',
                side TEXT NOT NULL DEFAULT '',
                value_usd REAL,
                value_label TEXT NOT NULL DEFAULT '',
                filed_at TEXT NOT NULL,
                traded_at TEXT,
                url TEXT NOT NULL DEFAULT '',
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_filings_events_filed
                ON filings_events(filed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_filings_events_kind
                ON filings_events(kind);

            -- Accessions we already fetched and judged not feed-worthy
            -- (sells/grants/tiny buys). Survives a restart so the next boot
            -- does not re-burst 40 XML fetches and trip EDGAR's 10 req/s cap.
            CREATE TABLE IF NOT EXISTS filings_seen (
                uid TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()


def insert_events(
    events: list[dict[str, Any]], *, db_path: Path = DEFAULT_DB_PATH
) -> int:
    """Insert feed rows; duplicates (same uid) are ignored. Returns inserted count."""
    init_filings_tables(db_path)
    if not events:
        return 0
    inserted = 0
    now = _now()
    with _connect(db_path) as conn:
        for ev in events:
            uid = str(ev.get("uid", "")).strip()
            kind = str(ev.get("kind", "")).strip()
            filed_at = str(ev.get("filed_at", "")).strip()
            if not uid or kind not in KINDS or not filed_at:
                continue
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO filings_events (
                    uid, kind, symbol, person, role, side,
                    value_usd, value_label, filed_at, traded_at,
                    url, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    kind,
                    str(ev.get("symbol", "")).strip().upper(),
                    str(ev.get("person", "")).strip(),
                    str(ev.get("role", "")).strip(),
                    str(ev.get("side", "")).strip(),
                    ev.get("value_usd"),
                    str(ev.get("value_label", "")).strip(),
                    filed_at,
                    ev.get("traded_at"),
                    str(ev.get("url", "")).strip(),
                    json.dumps(ev.get("detail") or {}),
                    now,
                ),
            )
            inserted += int(cur.rowcount or 0)
        conn.commit()
    return inserted


def has_uid(uid: str, *, db_path: Path = DEFAULT_DB_PATH) -> bool:
    init_filings_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM filings_events WHERE uid = ?
            UNION ALL
            SELECT 1 FROM filings_seen WHERE uid = ?
            LIMIT 1
            """,
            (uid, uid),
        ).fetchone()
    return row is not None


def remember_uid(uid: str, *, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Persist a skip so a restart does not re-fetch the same accession."""
    uid = str(uid).strip()
    if not uid:
        return
    init_filings_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO filings_seen (uid, created_at) VALUES (?, ?)",
            (uid, _now()),
        )
        conn.commit()


def list_recent(
    *,
    kinds: list[str] | None = None,
    limit: int = 100,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_filings_tables(db_path)
    lim = max(1, min(limit, _RECENT_CAP))
    wanted = [k for k in (kinds or []) if k in KINDS]
    where = ""
    params: list[Any] = []
    if wanted:
        where = f"WHERE kind IN ({','.join('?' * len(wanted))})"
        params.extend(wanted)
    params.append(lim)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM filings_events
            {where}
            ORDER BY filed_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["detail"] = json.loads(d.pop("detail_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["detail"] = {}
        out.append(d)
    return out
