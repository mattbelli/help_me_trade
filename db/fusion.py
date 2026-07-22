"""Persistent substrate for the observe-only Fusion Evidence Bus."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH, now_iso
from db.base import connect as _connect
from db.schema_helpers import _table_columns

STORES = ("options_flow", "equity_tape")

# Round-3 fusion_episodes shape (Fusion Horizon Redesign §2.4). Shared by the
# fresh-install CREATE and the one-time rebuild migration so the two never drift.
_FUSION_EPISODES_COLUMNS = """
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    market_day TEXT NOT NULL,            -- OPENING session only, no longer a key
    opened_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',  -- open | closed
    closed_at TEXT,
    close_reason TEXT,                   -- decay_to_noise|price_invalidation|hard_ttl|migrated
    last_evidence_at TEXT,               -- ts of most recent scored actor dot
    anchor_dot_id TEXT,
    anchor_price TEXT,
    anchor_atr14 TEXT,
    anchor_direction TEXT,
    registration_called INTEGER NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0,
    bull_mass REAL NOT NULL DEFAULT 0,       -- decayed thesis-class mass (cold-storage summary)
    bear_mass REAL NOT NULL DEFAULT 0,
    conviction REAL NOT NULL DEFAULT 0,
    key_levels_json TEXT NOT NULL DEFAULT '[]',
    seed_bull_mass REAL NOT NULL DEFAULT 0,  -- rehydration seed at open (§2.4); _rescore folds it
    seed_bear_mass REAL NOT NULL DEFAULT 0,  -- forward-decayed, so the seed survives rescores
    direction_state TEXT NOT NULL DEFAULT 'mixed',
    contested INTEGER NOT NULL DEFAULT 0,
    signature TEXT NOT NULL DEFAULT '',
    market_shock INTEGER NOT NULL DEFAULT 0
"""


def _migrate_fusion_episodes(conn: Any) -> None:
    """One-time rebuild of the pre-Horizon symbol-day ``fusion_episodes`` table.

    SQLite cannot ``ALTER TABLE ... DROP`` a table-level UNIQUE, so when the
    legacy shape is detected — guarded by "does the ``state`` column exist?" —
    rebuild to the Round-3 shape. Legacy symbol-day rows become
    ``state='closed', close_reason='migrated'`` (they were day buckets that are,
    by definition, over); **ids are preserved** so every child row
    (``evidence_dots``, ``fusion_investigations``, ``reason_runs``,
    ``fusion_score_history``, ``idea_outcomes.episode_id``) stays linked. Fresh
    installs already have ``state`` (via the CREATE above) and skip this."""
    if "state" in _table_columns(conn, "fusion_episodes"):
        return
    # BEGIN IMMEDIATE: executescript autocommits each statement otherwise — a
    # crash between DROP and RENAME would let the next boot's CREATE IF NOT
    # EXISTS re-materialize an EMPTY live table with the data stranded in
    # fusion_episodes_new, silently detaching every child row.
    conn.executescript(
        f"""
        BEGIN IMMEDIATE;
        CREATE TABLE fusion_episodes_new ({_FUSION_EPISODES_COLUMNS});
        INSERT INTO fusion_episodes_new (
            id, symbol, market_day, opened_at, updated_at, state, closed_at,
            close_reason, last_evidence_at, anchor_dot_id, anchor_price,
            anchor_atr14, anchor_direction, registration_called, score,
            bull_mass, bear_mass, conviction, key_levels_json, direction_state,
            contested, signature, market_shock
        )
        SELECT
            id, symbol, market_day, opened_at, updated_at, 'closed', updated_at,
            'migrated', updated_at, anchor_dot_id, anchor_price, anchor_atr14,
            anchor_direction, registration_called, score, 0, 0, 0, '[]',
            direction_state, contested, signature, market_shock
        FROM fusion_episodes;
        DROP TABLE fusion_episodes;
        ALTER TABLE fusion_episodes_new RENAME TO fusion_episodes;
        COMMIT;
        """
    )
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
                state TEXT NOT NULL DEFAULT 'open',
                closed_at TEXT,
                close_reason TEXT,
                last_evidence_at TEXT,
                anchor_dot_id TEXT,
                anchor_price TEXT,
                anchor_atr14 TEXT,
                anchor_direction TEXT,
                registration_called INTEGER NOT NULL DEFAULT 0,
                score REAL NOT NULL DEFAULT 0,
                bull_mass REAL NOT NULL DEFAULT 0,
                bear_mass REAL NOT NULL DEFAULT 0,
                conviction REAL NOT NULL DEFAULT 0,
                key_levels_json TEXT NOT NULL DEFAULT '[]',
                seed_bull_mass REAL NOT NULL DEFAULT 0,
                seed_bear_mass REAL NOT NULL DEFAULT 0,
                direction_state TEXT NOT NULL DEFAULT 'mixed',
                contested INTEGER NOT NULL DEFAULT 0,
                signature TEXT NOT NULL DEFAULT '',
                market_shock INTEGER NOT NULL DEFAULT 0
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

            -- Storm-cheap session-levels read-cache, the structure sibling of
            -- fusion_atr_cache: a once-daily job stamps the latest completed
            -- session's nodes (PDH/PDL/VAL/VAH/POC) per active symbol here (the
            -- network fetch lives in that producer, exactly like ATR's), and the
            -- trigger intake only ever READS it on the hot path — never a live
            -- compute_session_levels call during a burst. levels_json is the JSON
            -- array the legacy nearest_level() consumes: [{"name","price",...}].
            CREATE TABLE IF NOT EXISTS fusion_session_levels (
                symbol TEXT PRIMARY KEY,
                as_of_date TEXT NOT NULL,
                levels_json TEXT NOT NULL,
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

            -- Phase C1 shadow-investigation gate (signals/fusion/investigate.py).
            -- A SEPARATE ledger from fusion_dispositions on purpose: dispositions
            -- carry the bus conservation law (one terminal row per consumed event,
            -- inputs == dispositions in conservation_counts), so an investigation
            -- decision must never add a row there.
            CREATE TABLE IF NOT EXISTS fusion_investigations (
                episode_id TEXT PRIMARY KEY,
                market_day TEXT NOT NULL,
                symbol TEXT NOT NULL,
                decided_at TEXT NOT NULL,
                decision TEXT NOT NULL CHECK (decision IN (
                    'shadow_investigate', 'dropped_budget',
                    'reserve_deferred', 'no_baseline'
                )),
                score REAL NOT NULL,
                threshold REAL
            );
            CREATE INDEX IF NOT EXISTS idx_fusion_investigation_day
                ON fusion_investigations(market_day, decision);

            -- Per-market-day threshold cache: the bar derives only from PAST
            -- sessions (< market_day), so it is stable within the day — computed
            -- once on first use and read thereafter, never per-dot.
            CREATE TABLE IF NOT EXISTS fusion_investigation_baseline (
                market_day TEXT PRIMARY KEY,
                decision TEXT NOT NULL,
                threshold REAL,
                pool_size INTEGER NOT NULL,
                sessions INTEGER NOT NULL,
                widened INTEGER NOT NULL DEFAULT 0,
                computed_at TEXT NOT NULL
            );

            -- Phase C3 investigation lane (research/reason.py). Sibling of
            -- fusion_investigations, same episode key: ONE row = this episode has
            -- been picked up by the reason lane (the processed marker — pending =
            -- shadow_investigate rows with no reason_runs row). Carries the T1
            -- triage verdict + null hypothesis and the v1 outcome; detail_json holds
            -- the full v1 output (never surfaced — dark until C5 arms).
            CREATE TABLE IF NOT EXISTS reason_runs (
                episode_id TEXT PRIMARY KEY,
                market_day TEXT NOT NULL,
                symbol TEXT NOT NULL,
                created_at TEXT NOT NULL,
                t1_status TEXT,
                t1_investigate INTEGER,
                null_hypothesis TEXT,
                angle_to_check TEXT,
                v1_status TEXT,
                idea_id INTEGER,
                no_idea INTEGER NOT NULL DEFAULT 0,
                detail_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_reason_runs_day
                ON reason_runs(market_day);
            """
        )
        # Rebuild a legacy symbol-day fusion_episodes table (drops the dead
        # UNIQUE(symbol, market_day)) BEFORE the partial index below — the index
        # references the `state` column the migration adds.
        _migrate_fusion_episodes(conn)
        conn.executescript(
            """
            -- One OPEN episode per symbol = the new dedup key (replaces the
            -- dropped UNIQUE(symbol, market_day)); closed rows are unconstrained.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fusion_episode_open
                ON fusion_episodes(symbol) WHERE state = 'open';
            CREATE INDEX IF NOT EXISTS idx_fusion_episode_state
                ON fusion_episodes(state, symbol);
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


def put_session_levels(
    symbol: str,
    as_of_date: str,
    levels: list[dict[str, Any]],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Stamp the latest completed session's nodes for *symbol* (producer side).

    ``levels`` is the JSON array the legacy ``nearest_level`` consumes — one dict
    per node with at least ``name`` and ``price`` (e.g. ``{"name":"PDH","price":
    320.5,"kind":"prior_high"}``). Last-write-wins per symbol, mirroring put_atr.
    """
    init_fusion_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO fusion_session_levels(symbol, as_of_date, levels_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                as_of_date = excluded.as_of_date,
                levels_json = excluded.levels_json,
                updated_at = excluded.updated_at
            """,
            (
                symbol.strip().upper(),
                as_of_date,
                json.dumps(list(levels), default=str),
                now_iso(),
            ),
        )
        conn.commit()


def get_session_levels(symbol: str, *, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """The cached session nodes for *symbol*, or ``[]`` — storm-cheap intake read."""
    init_fusion_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT levels_json FROM fusion_session_levels WHERE symbol = ?",
            (symbol.strip().upper(),),
        ).fetchone()
    if row is None:
        return []
    try:
        parsed = json.loads(row["levels_json"])
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


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


# --------------------------------------------------------------- reason_runs (C3)


def list_pending_reason_episodes(
    market_day: str, *, db_path: Path = DEFAULT_DB_PATH
) -> list[dict]:
    """Episodes admitted by C1 that the reason lane has not yet processed.

    Pending = ``fusion_investigations`` rows with decision ``shadow_investigate`` for
    ``market_day`` that have no ``reason_runs`` row. Returns the full episode rows,
    highest score first (budget spends on the best stacks). One row per episode.
    """
    init_fusion_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT e.* FROM fusion_episodes e
            JOIN fusion_investigations fi ON fi.episode_id = e.id
            WHERE fi.market_day = ?
              AND fi.decision = 'shadow_investigate'
              AND e.id NOT IN (SELECT episode_id FROM reason_runs)
            ORDER BY e.score DESC, e.updated_at DESC, e.id ASC
            """,
            (str(market_day),),
        ).fetchall()
    return [dict(row) for row in rows]


def put_reason_run(run: dict[str, Any], *, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Upsert one reason-lane processed marker (keyed on episode_id)."""
    init_fusion_tables(db_path)
    detail = run.get("detail_json")
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, default=str)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO reason_runs (
                episode_id, market_day, symbol, created_at, t1_status,
                t1_investigate, null_hypothesis, angle_to_check, v1_status,
                idea_id, no_idea, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(episode_id) DO UPDATE SET
                market_day = excluded.market_day,
                symbol = excluded.symbol,
                created_at = excluded.created_at,
                t1_status = excluded.t1_status,
                t1_investigate = excluded.t1_investigate,
                null_hypothesis = excluded.null_hypothesis,
                angle_to_check = excluded.angle_to_check,
                v1_status = excluded.v1_status,
                idea_id = excluded.idea_id,
                no_idea = excluded.no_idea,
                detail_json = excluded.detail_json
            """,
            (
                str(run["episode_id"]),
                str(run.get("market_day") or ""),
                str(run.get("symbol") or ""),
                str(run.get("created_at") or now_iso()),
                run.get("t1_status"),
                None if run.get("t1_investigate") is None else int(run["t1_investigate"]),
                run.get("null_hypothesis"),
                run.get("angle_to_check"),
                run.get("v1_status"),
                None if run.get("idea_id") is None else int(run["idea_id"]),
                int(run.get("no_idea") or 0),
                str(detail or "{}"),
            ),
        )
        conn.commit()


def get_reason_run(
    episode_id: str, *, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    """The reason-lane row for ``episode_id`` (detail parsed back), or None."""
    init_fusion_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM reason_runs WHERE episode_id = ?", (str(episode_id),)
        ).fetchone()
    if row is None:
        return None
    out = dict(row)
    try:
        out["detail"] = json.loads(out.get("detail_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        out["detail"] = {}
    return out
