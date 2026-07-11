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

# The founder grades JUDGMENT (5b): only fully resolved ideas can be graded —
# never pending/triggered (still open), never invalidated/data_gap (no clean
# outcome to judge), never the vetoed journal-only lane.
GRADEABLE_STATUSES = frozenset({"won", "lost", "expired"})

# founder_verdict JSON shape — the 5b rubric (spec appendix): four 1–5 scores.
#   take_it          (a) would you have taken it?
#   entry_honest     (b) did price actually offer the entry zone?
#   stop_survivable  (c) did the stop survive (not wicked out before the move)?
#   thesis_quality   (d) did the stated reason play out, even if it lost?
VERDICT_SCORE_KEYS = ("take_it", "entry_honest", "stop_survivable", "thesis_quality")

# 5b gate (spec appendix): ≥10 graded, mean take-it ≥3, zero numbers-wrong.
# A "numbers were wrong" fail is a bottom score (==1) on a b/c-type dimension —
# the entry zone never existed, or the stop was un-survivable.
GATE_MIN_GRADED = 10
GATE_MIN_MEAN_TAKE_IT = 3.0
_NUMBERS_WRONG_KEYS = ("entry_honest", "stop_survivable")


def normalize_verdict(payload: Any) -> dict[str, Any]:
    """Validate + normalize a founder grade into the stored JSON shape.

    Raises ValueError on any malformed field (endpoint maps this to 400)."""
    if not isinstance(payload, dict):
        raise ValueError("verdict must be an object")
    verdict: dict[str, Any] = {}
    for key in VERDICT_SCORE_KEYS:
        raw = payload.get(key)
        if isinstance(raw, bool):  # bool is an int subclass — reject explicitly
            raise ValueError(f"{key} must be an integer 1-5")
        try:
            score = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be an integer 1-5")
        if not 1 <= score <= 5:
            raise ValueError(f"{key} must be between 1 and 5")
        verdict[key] = score
    note = payload.get("note")
    if note is not None:
        note_str = str(note).strip()
        if note_str:
            verdict["note"] = note_str[:500]
    verdict["graded_at"] = _now()
    return verdict


def grade_outcome(
    outcome_id: int,
    verdict: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> str:
    """Persist a founder verdict onto a resolved idea. Idempotent overwrite
    (re-grade allowed). Returns 'graded' | 'not_found' | 'not_gradeable'."""
    init_idea_outcomes_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, origin FROM idea_outcomes WHERE id = ?",
            (int(outcome_id),),
        ).fetchone()
        if row is None:
            return "not_found"
        status = str(row["status"])
        origin = str(row["origin"] or "structure")
        if origin == VETOED_ORIGIN or status not in GRADEABLE_STATUSES:
            return "not_gradeable"
        conn.execute(
            "UPDATE idea_outcomes SET founder_verdict = ?, updated_at = ? WHERE id = ?",
            (json.dumps(verdict), _now(), int(outcome_id)),
        )
        conn.commit()
    return "graded"


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
        # Latest daily close seen by the sweep — lets the widget explain WHY a
        # row is still pending (distance from the entry zone) without a live
        # quote fetch at read time.
        _add_column_if_missing(conn, "idea_outcomes", "last_price", "TEXT")
        _add_column_if_missing(conn, "idea_outcomes", "last_price_at", "TEXT")
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
    raw_verdict = out.get("founder_verdict")
    if raw_verdict:
        try:
            parsed = json.loads(raw_verdict)
            out["verdict"] = parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            out["verdict"] = None
    else:
        out["verdict"] = None
    return out


def register_ideas(
    artifact_id: int,
    ideas: list[dict[str, Any]],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Insert snapshot rows for validated ideas. Idempotent on (artifact_id, idea_index).

    Also dedupes across artifacts: an idea whose (symbol, entry zone, stop,
    origin) exactly matches a still-OPEN row is skipped — a scheduled daily
    scan re-suggesting yesterday's untouched zone must not multi-count it in
    the calibration ledger."""
    init_idea_outcomes_tables(db_path)
    if not ideas:
        return 0

    inserted = 0
    now = _now()
    open_placeholders = ",".join("?" * len(OPEN_STATUSES))
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

            dup = conn.execute(
                f"""
                SELECT 1 FROM idea_outcomes
                WHERE status IN ({open_placeholders})
                  AND symbol = ? AND entry_low = ? AND entry_high = ?
                  AND stop = ? AND origin = ?
                LIMIT 1
                """,
                (*OPEN_STATUSES, symbol, entry_low, entry_high, stop, origin),
            ).fetchone()
            if dup is not None:
                continue

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
    last_price: str | None = None,
    last_price_at: str | None = None,
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
    if last_price is not None:
        fields.append("last_price = ?")
        values.append(last_price)
    if last_price_at is not None:
        fields.append("last_price_at = ?")
        values.append(last_price_at)

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
        # Founder grades — vetoed journal-only lane is never founder-facing, so
        # it never counts toward the 5b gate.
        verdict_rows = conn.execute(
            "SELECT founder_verdict FROM idea_outcomes "
            "WHERE origin != ? AND founder_verdict IS NOT NULL "
            "AND founder_verdict != ''",
            (VETOED_ORIGIN,),
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

    grading = _grading_summary(verdict_rows)

    return {
        "counts": counts,
        "resolved": resolved,
        "win_rate": win_rate,
        "by_origin": by_origin,
        "grading": grading,
    }


def _grading_summary(verdict_rows: list[Any]) -> dict[str, Any]:
    """Aggregate founder grades into the 5b gate view."""
    graded = 0
    sums = dict.fromkeys(VERDICT_SCORE_KEYS, 0.0)
    numbers_wrong = 0
    for row in verdict_rows:
        try:
            verdict = json.loads(row["founder_verdict"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(verdict, dict):
            continue
        graded += 1
        for key in VERDICT_SCORE_KEYS:
            val = verdict.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                sums[key] += float(val)
        if any(verdict.get(k) == 1 for k in _NUMBERS_WRONG_KEYS):
            numbers_wrong += 1

    means = {
        key: (sums[key] / graded if graded else None) for key in VERDICT_SCORE_KEYS
    }
    mean_take_it = means["take_it"]
    gate_pass = (
        graded >= GATE_MIN_GRADED
        and mean_take_it is not None
        and mean_take_it >= GATE_MIN_MEAN_TAKE_IT
        and numbers_wrong == 0
    )
    return {
        "graded": graded,
        "means": means,
        "mean_take_it": mean_take_it,
        "numbers_wrong": numbers_wrong,
        "gate": {
            "min_graded": GATE_MIN_GRADED,
            "min_mean_take_it": GATE_MIN_MEAN_TAKE_IT,
            "max_numbers_wrong": 0,
        },
        "gate_pass": gate_pass,
    }
