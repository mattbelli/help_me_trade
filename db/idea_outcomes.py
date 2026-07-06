"""Shadow outcomes for setup_scan ideas — mechanical price tracking, no trading."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now
from db.schema_helpers import _add_column_if_missing

logger = logging.getLogger(__name__)

OPEN_STATUSES = frozenset({"pending", "triggered"})
# Rows the mechanical sweep re-examines each pass. data_gap is included so a
# later successful fetch can RESTORE it to an open status (H2 recovery) — an
# outage must never permanently tombstone an idea.
SWEEPABLE_STATUSES = frozenset({"pending", "triggered", "data_gap"})
TERMINAL_STATUSES = frozenset(
    {"won", "lost", "expired", "invalidated", "data_gap"}
)
RESOLVED_WIN_LOSS = frozenset({"won", "lost"})
# Journal-only lane: vetoed structures are outcome-tracked for calibration but
# are NOT recommended — excluded from the headline paper win-rate (see
# get_summary), still visible per-origin.
VETOED_ORIGIN = "structure_vetoed"


def init_idea_outcomes_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS idea_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_id INTEGER NOT NULL,
                idea_index INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                as_of TEXT NOT NULL,
                entry_low TEXT NOT NULL,
                entry_high TEXT NOT NULL,
                stop TEXT NOT NULL,
                targets_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                triggered_at TEXT,
                resolved_at TEXT,
                resolution_price TEXT,
                first_target_hit INTEGER,
                mfe_pct REAL,
                mae_pct REAL,
                founder_verdict TEXT,
                fetch_fail_streak INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                UNIQUE(artifact_id, idea_index)
            );

            CREATE INDEX IF NOT EXISTS idx_idea_outcomes_status
                ON idea_outcomes(status);
            CREATE INDEX IF NOT EXISTS idx_idea_outcomes_symbol
                ON idea_outcomes(symbol);
            CREATE INDEX IF NOT EXISTS idx_idea_outcomes_updated
                ON idea_outcomes(updated_at DESC);
            """
        )
        _add_column_if_missing(
            conn, "idea_outcomes", "origin", "TEXT NOT NULL DEFAULT 'structure'"
        )
        _add_column_if_missing(
            conn, "idea_outcomes", "veto_reason", "TEXT NOT NULL DEFAULT ''"
        )
        conn.commit()


def _row_to_outcome(row: Any) -> dict[str, Any]:
    out = dict(row)
    raw_targets = out.get("targets_json")
    if raw_targets:
        try:
            out["targets"] = json.loads(raw_targets)
        except (json.JSONDecodeError, TypeError):
            out["targets"] = []
    else:
        out["targets"] = []
    return out


def register_ideas(
    artifact_id: int,
    ideas: list[dict[str, Any]],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Insert snapshot rows for validated ideas. Idempotent on (artifact_id, idea_index)."""
    init_idea_outcomes_tables(db_path)
    if not ideas:
        return 0

    inserted = 0
    now = _now()
    with _connect(db_path) as conn:
        for idx, idea in enumerate(ideas):
            if not isinstance(idea, dict):
                continue
            symbol = str(idea.get("symbol", "")).strip().upper()
            entry_zone = idea.get("entry_zone")
            if not symbol or not isinstance(entry_zone, dict):
                continue
            entry_low = str(entry_zone.get("low", "")).strip()
            entry_high = str(entry_zone.get("high", "")).strip()
            stop = str(idea.get("stop", "")).strip()
            targets = idea.get("targets")
            if not entry_low or not entry_high or not stop or not isinstance(targets, list):
                continue

            as_of = str(idea.get("as_of") or now).strip() or now
            targets_json = json.dumps([str(t) for t in targets])
            origin = str(idea.get("origin", "structure")).strip() or "structure"
            veto_reason = str(idea.get("veto_reason", "")).strip()

            cur = conn.execute(
                """
                INSERT OR IGNORE INTO idea_outcomes (
                    artifact_id, idea_index, symbol, as_of,
                    entry_low, entry_high, stop, targets_json,
                    status, origin, veto_reason, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    int(artifact_id),
                    int(idx),
                    symbol,
                    as_of,
                    entry_low,
                    entry_high,
                    stop,
                    targets_json,
                    origin,
                    veto_reason,
                    now,
                ),
            )
            inserted += int(cur.rowcount or 0)
        conn.commit()
    return inserted


def list_open(*, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    init_idea_outcomes_tables(db_path)
    placeholders = ",".join("?" * len(OPEN_STATUSES))
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM idea_outcomes
            WHERE status IN ({placeholders})
            ORDER BY updated_at ASC, id ASC
            """,
            tuple(OPEN_STATUSES),
        ).fetchall()
    return [_row_to_outcome(r) for r in rows]


def list_sweepable(*, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Rows the mechanical sweep advances: open rows PLUS data_gap rows (so an
    outage can be recovered when bars reappear). data_gap stays out of
    list_open, so the widget/win-rate still treat it as closed."""
    init_idea_outcomes_tables(db_path)
    placeholders = ",".join("?" * len(SWEEPABLE_STATUSES))
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM idea_outcomes
            WHERE status IN ({placeholders})
            ORDER BY updated_at ASC, id ASC
            """,
            tuple(SWEEPABLE_STATUSES),
        ).fetchall()
    return [_row_to_outcome(r) for r in rows]


def list_recent_open(
    *, limit: int = 50, db_path: Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    init_idea_outcomes_tables(db_path)
    lim = max(1, min(limit, 500))
    placeholders = ",".join("?" * len(OPEN_STATUSES))
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM idea_outcomes
            WHERE status IN ({placeholders}) AND origin != ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (*OPEN_STATUSES, VETOED_ORIGIN, lim),
        ).fetchall()
    return [_row_to_outcome(r) for r in rows]


def list_recent_resolved(
    *, limit: int = 50, db_path: Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    init_idea_outcomes_tables(db_path)
    lim = max(1, min(limit, 500))
    placeholders = ",".join("?" * len(TERMINAL_STATUSES))
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM idea_outcomes
            WHERE status IN ({placeholders}) AND origin != ?
            ORDER BY resolved_at DESC, updated_at DESC, id DESC
            LIMIT ?
            """,
            (*TERMINAL_STATUSES, VETOED_ORIGIN, lim),
        ).fetchall()
    return [_row_to_outcome(r) for r in rows]


def update_outcome(
    outcome_id: int,
    *,
    status: str | None = None,
    triggered_at: str | None = None,
    resolved_at: str | None = None,
    resolution_price: str | None = None,
    first_target_hit: int | None = None,
    mfe_pct: float | None = None,
    mae_pct: float | None = None,
    founder_verdict: str | None = None,
    fetch_fail_streak: int | None = None,
    notes: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """Apply a forward-only update. Returns True if a row was modified."""
    init_idea_outcomes_tables(db_path)
    fields: list[str] = ["updated_at = ?"]
    values: list[Any] = [_now()]

    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if triggered_at is not None:
        fields.append("triggered_at = ?")
        values.append(triggered_at)
    if resolved_at is not None:
        fields.append("resolved_at = ?")
        values.append(resolved_at)
    if resolution_price is not None:
        fields.append("resolution_price = ?")
        values.append(resolution_price)
    if first_target_hit is not None:
        fields.append("first_target_hit = ?")
        values.append(first_target_hit)
    if mfe_pct is not None:
        fields.append("mfe_pct = ?")
        values.append(mfe_pct)
    if mae_pct is not None:
        fields.append("mae_pct = ?")
        values.append(mae_pct)
    if founder_verdict is not None:
        fields.append("founder_verdict = ?")
        values.append(founder_verdict)
    if fetch_fail_streak is not None:
        fields.append("fetch_fail_streak = ?")
        values.append(int(fetch_fail_streak))
    if notes is not None:
        fields.append("notes = ?")
        values.append(notes)

    values.append(int(outcome_id))
    with _connect(db_path) as conn:
        cur = conn.execute(
            f"UPDATE idea_outcomes SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        conn.commit()
        return int(cur.rowcount or 0) > 0


def get_summary(*, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    init_idea_outcomes_tables(db_path)
    with _connect(db_path) as conn:
        # Headline counts/win-rate cover the RECOMMENDED lanes only. Vetoed
        # structures are journal-only (calibration) and must never flatter or
        # pollute the paper's win% — they surface under by_origin instead.
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM idea_outcomes "
            "WHERE origin != ? GROUP BY status",
            (VETOED_ORIGIN,),
        ).fetchall()
        origin_rows = conn.execute(
            """
            SELECT origin, status, COUNT(*) AS n
            FROM idea_outcomes
            GROUP BY origin, status
            """
        ).fetchall()

    counts: dict[str, int] = {}
    for row in rows:
        counts[str(row["status"])] = int(row["n"])

    won = counts.get("won", 0)
    lost = counts.get("lost", 0)
    resolved = sum(counts.get(s, 0) for s in TERMINAL_STATUSES)
    win_loss = won + lost
    win_rate = (won / win_loss) if win_loss else None

    origin_counts: dict[str, dict[str, int]] = {}
    for row in origin_rows:
        origin = str(row["origin"] or "structure")
        status = str(row["status"])
        origin_counts.setdefault(origin, {})[status] = int(row["n"])

    by_origin: dict[str, Any] = {}
    for origin, origin_status_counts in origin_counts.items():
        origin_won = origin_status_counts.get("won", 0)
        origin_lost = origin_status_counts.get("lost", 0)
        origin_resolved = sum(
            origin_status_counts.get(s, 0) for s in TERMINAL_STATUSES
        )
        origin_win_loss = origin_won + origin_lost
        by_origin[origin] = {
            "counts": origin_status_counts,
            "resolved": origin_resolved,
            "win_rate": (origin_won / origin_win_loss) if origin_win_loss else None,
        }

    return {
        "counts": counts,
        "resolved": resolved,
        "win_rate": win_rate,
        "by_origin": by_origin,
    }
