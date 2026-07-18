"""Persistent substrate for the observe-only Fusion Evidence Bus."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH, now_iso
from db.base import connect as _connect

STORES = ("options_flow", "equity_tape")
DISPOSITIONS = frozenset({"scored", "collapsed", "shed", "unstampable", "ignored_by_rule"})


def init_fusion_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bus_cursors (
                store TEXT PRIMARY KEY,
                last_ingest_seq INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fusion_episodes (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                market_day TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                anchor_dot_id TEXT,
                anchor_price TEXT,
                anchor_atr14 TEXT,
                anchor_direction TEXT,
                registration_called INTEGER NOT NULL DEFAULT 0,
                score REAL NOT NULL DEFAULT 0,
                direction_state TEXT NOT NULL DEFAULT 'mixed',
                contested INTEGER NOT NULL DEFAULT 0,
                signature TEXT NOT NULL DEFAULT '',
                market_shock INTEGER NOT NULL DEFAULT 0,
                UNIQUE(symbol, market_day)
            );

            CREATE TABLE IF NOT EXISTS evidence_dots (
                id TEXT PRIMARY KEY,
                episode_id TEXT NOT NULL,
                source_store TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                source_ingest_seq INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                ts TEXT NOT NULL,
                component TEXT NOT NULL,
                actor_group TEXT NOT NULL,
                dot_kind TEXT NOT NULL,
                direction TEXT NOT NULL,
                raw_value TEXT,
                points REAL NOT NULL DEFAULT 0,
                detail_json TEXT NOT NULL DEFAULT '{}',
                trigger_price TEXT,
                prior_close_atr14 TEXT,
                stamped INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(source_store, source_event_id, component)
            );
            CREATE INDEX IF NOT EXISTS idx_evidence_episode
                ON evidence_dots(episode_id, ts);

            CREATE TABLE IF NOT EXISTS fusion_dispositions (
                source_store TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                source_ingest_seq INTEGER NOT NULL,
                episode_id TEXT,
                dot_id TEXT,
                disposition TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(source_store, source_event_id)
            );
            CREATE INDEX IF NOT EXISTS idx_fusion_disposition_day
                ON fusion_dispositions(created_at, disposition);

            CREATE TABLE IF NOT EXISTS fusion_atr_cache (
                symbol TEXT PRIMARY KEY,
                as_of_date TEXT NOT NULL,
                atr14 TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fusion_score_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id TEXT NOT NULL,
                score REAL NOT NULL,
                score_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fusion_component_groups (
                component TEXT PRIMARY KEY,
                actor_group TEXT NOT NULL,
                dot_kind TEXT NOT NULL,
                scoring_mode TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        group_rows = (
            ("option_sweep", "options_actor", "event", "directional_aggregate", 1),
            ("option_block", "options_actor", "event", "directional_aggregate", 1),
            (
                "unusual_option_volume",
                "options_actor",
                "event",
                "directional_aggregate",
                1,
            ),
            (
                "directional_pressure",
                "options_actor",
                "event",
                "directional_aggregate",
                1,
            ),
            ("equity_block", "equity_actor", "event", "directional_aggregate", 1),
            ("dark_pool", "equity_actor", "event", "directional_aggregate", 1),
            ("equity_sweep", "equity_actor", "event", "excluded", 0),
            ("rvol_ex_prints", "background_crowd", "state", "annotate_only", 1),
            ("session_level", "structure", "state", "latest_best", 1),
            ("price_alert", "structure", "state", "latest_best", 1),
            ("reddit_buzz", "attention", "state", "latest_best", 1),
            ("stocktwits_buzz", "attention", "state", "latest_best", 1),
            ("earnings_proximity", "catalyst", "state", "latest_best", 1),
            ("regime", "context", "state", "annotate_only", 1),
            ("index_pressure", "context", "state", "annotate_only", 1),
            ("market_shock", "context", "state", "annotate_only", 1),
        )
        conn.executemany(
            """
            INSERT INTO fusion_component_groups(
                component, actor_group, dot_kind, scoring_mode, enabled
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(component) DO UPDATE SET
                actor_group = excluded.actor_group,
                dot_kind = excluded.dot_kind,
                scoring_mode = excluded.scoring_mode,
                enabled = excluded.enabled
            """,
            group_rows,
        )
        now = now_iso()
        for store in STORES:
            conn.execute(
                """
                INSERT OR IGNORE INTO bus_cursors(store, last_ingest_seq, updated_at)
                VALUES (?, 0, ?)
                """,
                (store, now),
            )
        conn.commit()


def get_cursor(store: str, *, db_path: Path = DEFAULT_DB_PATH) -> int:
    init_fusion_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT last_ingest_seq FROM bus_cursors WHERE store = ?", (store,)
        ).fetchone()
    return int(row["last_ingest_seq"]) if row else 0


def set_cursor(store: str, value: int, *, db_path: Path = DEFAULT_DB_PATH) -> None:
    init_fusion_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE bus_cursors
            SET last_ingest_seq = MAX(last_ingest_seq, ?), updated_at = ?
            WHERE store = ?
            """,
            (int(value), now_iso(), store),
        )
        conn.commit()


def put_atr(
    symbol: str,
    as_of_date: str,
    atr14: Decimal,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    init_fusion_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO fusion_atr_cache(symbol, as_of_date, atr14, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                as_of_date = excluded.as_of_date,
                atr14 = excluded.atr14,
                updated_at = excluded.updated_at
            """,
            (symbol.strip().upper(), as_of_date, str(atr14), now_iso()),
        )
        conn.commit()


def get_atr(symbol: str, *, db_path: Path = DEFAULT_DB_PATH) -> Decimal | None:
    init_fusion_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT atr14 FROM fusion_atr_cache WHERE symbol = ?",
            (symbol.strip().upper(),),
        ).fetchone()
    return Decimal(str(row["atr14"])) if row else None


def list_recent_episodes(
    *,
    market_day: str | None = None,
    limit: int = 50,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict]:
    init_fusion_tables(db_path)
    lim = max(1, min(int(limit), 500))
    with _connect(db_path) as conn:
        resolved_day = market_day
        if resolved_day is None:
            row = conn.execute("SELECT MAX(market_day) AS market_day FROM fusion_episodes").fetchone()
            resolved_day = str(row["market_day"]) if row and row["market_day"] else None
        if resolved_day is None:
            return []
        rows = conn.execute(
            """
            SELECT e.id, e.symbol, e.market_day, e.opened_at, e.updated_at,
                   e.anchor_dot_id, e.anchor_price, e.anchor_atr14,
                   e.anchor_direction, e.registration_called, e.score,
                   e.direction_state, e.contested, e.signature, e.market_shock,
                   COALESCE(d.dot_count, 0) AS dot_count
            FROM fusion_episodes AS e
            LEFT JOIN (
                SELECT episode_id, COUNT(*) AS dot_count
                FROM evidence_dots
                GROUP BY episode_id
            ) AS d ON d.episode_id = e.id
            WHERE e.market_day = ?
            ORDER BY e.score DESC, e.updated_at DESC
            LIMIT ?
            """,
            (resolved_day, lim),
        ).fetchall()
    return [dict(row) for row in rows]


def episode_dots(episode_id: str, *, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM evidence_dots WHERE episode_id = ? ORDER BY ts, id",
            (episode_id,),
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["detail"] = json.loads(item.pop("detail_json"))
        except (TypeError, json.JSONDecodeError):
            item["detail"] = {}
        out.append(item)
    return out


def conservation_counts(*, db_path: Path = DEFAULT_DB_PATH) -> dict[str, int]:
    """Return ledger-derived counts; equality is the bus conservation law."""
    init_fusion_tables(db_path)
    with _connect(db_path) as conn:
        inputs = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM options_flow_events
               WHERE ingest_seq <= (
                 SELECT last_ingest_seq FROM bus_cursors WHERE store = 'options_flow'
               ))
              +
              (SELECT COUNT(*) FROM equity_tape_events
               WHERE ingest_seq <= (
                 SELECT last_ingest_seq FROM bus_cursors WHERE store = 'equity_tape'
               ))
            """
        ).fetchone()[0]
        outputs = conn.execute(
            """
            SELECT COUNT(*) FROM fusion_dispositions
            WHERE disposition IN ('scored','collapsed','shed','unstampable','ignored_by_rule')
            """
        ).fetchone()[0]
    return {"inputs": int(inputs), "dispositions": int(outputs)}


def serialize_detail(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
