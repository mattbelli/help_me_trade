"""Shadow outcomes for setup_scan ideas — mechanical price tracking, no trading."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ai.research.ideas import (
    _CONFIDENCE_VALUES,
    _has_fabricated_probability,
    _parse_price,
)
from config.idea_engine import IDEA_COOLDOWN_MIN
from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now
from db.schema_helpers import _add_column_if_missing

logger = logging.getLogger(__name__)

# Local ET zone — db/idea_outcomes must never import from trading/ (the
# dependency points ledger→nothing; trading→ledger, never the reverse).
_ET = ZoneInfo("America/New_York")

# Duration-style plan_horizon parsing for the intraday-horizon guard ([ADR-3]).
# Named horizons ('earnings_exit', 'exit_lead', ...) don't match → treated as
# multi-day and never rejected. One trading session ≈ 6.5 hours.
_TRADING_HOURS_PER_DAY = 6.5
_HORIZON_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([dhm])\s*$", re.IGNORECASE)

# Grading bases (spec [ADR-2]). 'plan' = zone/stop/targets Grading Law;
# 'horizon_return' = entry-ref → close at a fixed horizon (Path A proposals).
GRADING_BASES = frozenset({"plan", "horizon_return"})

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

        # Idea Engine Phase 1 (spec §1). Identity of the sample cell:
        _add_column_if_missing(
            conn, "idea_outcomes", "signal_family", "TEXT NOT NULL DEFAULT 'unknown'"
        )
        _add_column_if_missing(
            conn, "idea_outcomes", "direction", "TEXT NOT NULL DEFAULT 'long'"
        )
        _add_column_if_missing(conn, "idea_outcomes", "confidence_label", "TEXT")
        _add_column_if_missing(conn, "idea_outcomes", "regime_at_entry", "TEXT")
        _add_column_if_missing(conn, "idea_outcomes", "plan_horizon", "TEXT")
        # Provenance + audit:
        _add_column_if_missing(
            conn, "idea_outcomes", "user_action", "TEXT NOT NULL DEFAULT 'skipped'"
        )
        _add_column_if_missing(conn, "idea_outcomes", "source_ref", "TEXT")
        _add_column_if_missing(conn, "idea_outcomes", "dossier_ref", "TEXT")
        _add_column_if_missing(
            conn, "idea_outcomes", "bear_case", "TEXT NOT NULL DEFAULT ''"
        )
        _add_column_if_missing(conn, "idea_outcomes", "dedup_key", "TEXT")
        _add_column_if_missing(conn, "idea_outcomes", "episode_id", "TEXT")
        _add_column_if_missing(conn, "idea_outcomes", "cluster_key", "TEXT")
        _add_column_if_missing(
            conn, "idea_outcomes", "synthetic", "INTEGER NOT NULL DEFAULT 0"
        )
        _add_column_if_missing(conn, "idea_outcomes", "user_id", "TEXT")
        # Grading (NULL until ripe — NEVER zero-filled):
        _add_column_if_missing(
            conn, "idea_outcomes", "grading_basis", "TEXT NOT NULL DEFAULT 'plan'"
        )
        _add_column_if_missing(
            conn, "idea_outcomes", "grading_resolution", "TEXT NOT NULL DEFAULT 'daily'"
        )
        _add_column_if_missing(conn, "idea_outcomes", "fill_status", "TEXT")
        _add_column_if_missing(conn, "idea_outcomes", "ret_pct", "REAL")
        _add_column_if_missing(conn, "idea_outcomes", "spy_ret_pct", "REAL")
        _add_column_if_missing(conn, "idea_outcomes", "alpha_pct", "REAL")
        _add_column_if_missing(conn, "idea_outcomes", "exit_reason", "TEXT")

        # Calibration-cell index + the open-row dedup wall (partial unique over
        # the cooldown key). Created after the columns exist.
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_idea_cell
                ON idea_outcomes(origin, signal_family, status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_idea_dedup_open
                ON idea_outcomes(dedup_key)
                WHERE dedup_key IS NOT NULL AND status IN ('pending', 'triggered');
            """
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


def _insert_idea(
    conn: Any, values: dict[str, Any], *, raw_columns: dict[str, str] | None = None
) -> Any:
    """Shared INSERT OR IGNORE core for both register paths.

    `values` maps column → bound value; `raw_columns` maps column → a raw SQL
    expression spliced straight into VALUES (a self-allocating subquery, kept
    inside the INSERT so it stays race-safe under SQLite's single writer).
    Absent columns take their schema defaults. IGNORE fires on either unique
    index (legacy (artifact_id, idea_index) or the dedup_key open-row wall), so
    callers dedup by rowcount."""
    raw_columns = raw_columns or {}
    columns = list(values.keys()) + list(raw_columns.keys())
    slots = ["?"] * len(values) + list(raw_columns.values())
    return conn.execute(
        f"INSERT OR IGNORE INTO idea_outcomes ({', '.join(columns)}) "
        f"VALUES ({', '.join(slots)})",
        [values[c] for c in values],
    )


def _to_et(as_of_iso: str) -> datetime:
    """Parse an ISO timestamp to ET. Naive inputs are assumed already-ET-agnostic
    UTC (now_iso emits UTC); unparseable input falls back to now()."""
    try:
        dt = datetime.fromisoformat(as_of_iso)
    except (TypeError, ValueError):
        dt = datetime.now(_ET)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_ET)
    return dt.astimezone(_ET)


def _dedup_bucket(as_of_iso: str, cooldown_min: int) -> str:
    """ET-date + intraday cooldown window index (spec §1)."""
    dt = _to_et(as_of_iso)
    window = max(1, int(cooldown_min))
    minute_of_day = dt.hour * 60 + dt.minute
    return f"{dt.date().isoformat()}:{minute_of_day // window}"


def _cluster_key(as_of_iso: str, direction: str) -> str:
    """(direction, ISO-week bucket, day) — stamped now so Phase 4 correlation
    math has history (spec §1). The macro-day tag is the ET date in Phase 1."""
    dt = _to_et(as_of_iso)
    iso = dt.isocalendar()
    return f"{direction}:{iso.year}W{iso.week:02d}:{dt.date().isoformat()}"


def _horizon_trading_days(plan_horizon: str | None) -> float | None:
    """Parse a duration-style horizon into trading days, or None for named
    templates. Used only by the intraday-horizon guard ([ADR-3])."""
    if not plan_horizon:
        return None
    match = _HORIZON_DURATION_RE.match(str(plan_horizon))
    if match is None:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "d":
        return value
    if unit == "h":
        return value / _TRADING_HOURS_PER_DAY
    return value / (_TRADING_HOURS_PER_DAY * 60)  # 'm'


def _validate_long_plan(
    entry_zone: Any, stop: Any, targets: Any
) -> tuple[str, str, str, str] | None:
    """Side-correctness checks for a long plan (reuse of the validate_ideas
    rules, minus the last-price plausibility leg register_idea has no input for).
    Returns (entry_low, entry_high, stop, targets_json) raw strings, or None."""
    if not isinstance(entry_zone, dict):
        return None
    low = _parse_price(entry_zone.get("low"))
    high = _parse_price(entry_zone.get("high"))
    if low is None or high is None or low > high:
        return None
    stop_d = _parse_price(stop)
    if stop_d is None or stop_d >= low:  # long: stop must sit below the zone
        return None
    if not isinstance(targets, list) or not (1 <= len(targets) <= 3):
        return None
    parsed: list[Any] = []
    for raw in targets:
        t = _parse_price(raw)
        if t is None or t <= high:  # long: targets above the zone
            return None
        if parsed and t <= parsed[-1]:  # strictly ascending
            return None
        parsed.append(t)
    entry_low = str(entry_zone.get("low", "")).strip()
    entry_high = str(entry_zone.get("high", "")).strip()
    return (
        entry_low,
        entry_high,
        str(stop).strip(),
        json.dumps([str(t) for t in targets]),
    )


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

            cur = _insert_idea(
                conn,
                {
                    "artifact_id": int(artifact_id),
                    "idea_index": int(idx),
                    "symbol": symbol,
                    "as_of": as_of,
                    "entry_low": entry_low,
                    "entry_high": entry_high,
                    "stop": stop,
                    "targets_json": targets_json,
                    "status": "pending",
                    "origin": origin,
                    "veto_reason": veto_reason,
                    "updated_at": now,
                },
            )
            inserted += int(cur.rowcount or 0)
        conn.commit()
    return inserted


def register_idea(
    *,
    origin: str,
    signal_family: str,
    symbol: str,
    direction: str = "long",
    entry_zone: dict[str, Any] | None,
    stop: str | None,
    targets: list[str] | None,
    thesis: str = "",
    bear_case: str = "",
    confidence_label: str | None = None,
    as_of: str | None = None,
    plan_horizon: str | None = None,
    grading_basis: str = "plan",
    source_ref: dict[str, Any] | None = None,
    user_action: str = "skipped",
    regime_at_entry: str | None = None,
    entry_ref_price: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int | None:
    """The narrow write path for non-artifact idea sources (spec §2).

    Returns the new idea id, or None when the registration is deduped (an open
    row already holds this cooldown bucket) or rejected as invalid. NO conviction
    filter — low-confidence ideas are logged; that is how the calibration bins
    earn their shape. Long-only in Phase 1.
    """
    init_idea_outcomes_tables(db_path)

    symbol = str(symbol or "").strip().upper()
    origin = str(origin or "").strip()
    direction = str(direction or "long").strip().lower()
    if not symbol or not origin:
        return None
    if direction != "long":
        logger.warning("register_idea: only long setups supported (got %r)", direction)
        return None
    if grading_basis not in GRADING_BASES:
        logger.warning("register_idea: unknown grading_basis %r", grading_basis)
        return None

    label = str(confidence_label).strip().lower() if confidence_label else None
    if label is not None and label not in _CONFIDENCE_VALUES:
        logger.warning("register_idea: invalid confidence_label %r", confidence_label)
        return None

    # Honesty wall — never persist an LLM-authored numeric probability (the
    # 07-08 leak shape); mirrors validate_ideas on the free-text fields.
    if _has_fabricated_probability(thesis, bear_case):
        logger.warning("register_idea: rejected fabricated probability for %s", symbol)
        return None

    # Intraday-horizon guard ([ADR-3]): Phase 1 grades daily bars only.
    horizon_td = _horizon_trading_days(plan_horizon)
    if horizon_td is not None and horizon_td < 1.0:
        logger.warning(
            "register_idea: rejected intraday plan_horizon %r for %s (%.3f trading days)",
            plan_horizon,
            symbol,
            horizon_td,
        )
        return None

    if grading_basis == "horizon_return":
        if not entry_ref_price or not plan_horizon:
            logger.warning(
                "register_idea: horizon_return requires entry_ref_price + plan_horizon (%s)",
                symbol,
            )
            return None
        if stop is not None or targets:
            logger.warning(
                "register_idea: horizon_return forbids stop/targets (%s)", symbol
            )
            return None
        entry_ref = str(entry_ref_price).strip()
        if not entry_ref:
            return None
        entry_low = entry_high = entry_ref
        stop_val = ""
        targets_json = "[]"
    else:  # plan
        plan = _validate_long_plan(entry_zone, stop, targets)
        if plan is None:
            logger.warning("register_idea: invalid plan geometry for %s", symbol)
            return None
        entry_low, entry_high, stop_val, targets_json = plan

    now = _now()
    as_of = str(as_of or now).strip() or now
    cooldown = int(IDEA_COOLDOWN_MIN[origin])
    dedup_key = f"{origin}:{symbol}:{direction}:{_dedup_bucket(as_of, cooldown)}"

    values: dict[str, Any] = {
        "artifact_id": 0,
        "symbol": symbol,
        "as_of": as_of,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop": stop_val,
        "targets_json": targets_json,
        "status": "pending",
        "origin": origin,
        "signal_family": str(signal_family or "unknown").strip() or "unknown",
        "direction": direction,
        "confidence_label": label,
        "regime_at_entry": (str(regime_at_entry).strip() or None) if regime_at_entry else None,
        "plan_horizon": str(plan_horizon).strip() if plan_horizon else None,
        "user_action": str(user_action or "skipped").strip() or "skipped",
        "source_ref": json.dumps(source_ref) if source_ref is not None else None,
        "bear_case": str(bear_case or ""),
        "dedup_key": dedup_key,
        "cluster_key": _cluster_key(as_of, direction),
        "grading_basis": grading_basis,
        "updated_at": now,
    }

    with _connect(db_path) as conn:
        # The legacy UNIQUE(artifact_id, idea_index) predates this path and would
        # otherwise shadow the dedup_key guard for a fixed idea_index=0, so
        # self-allocate a non-colliding index (starts at 0 for the first row).
        # The dedup_key partial-unique index remains the real open-row wall —
        # INSERT OR IGNORE fires on it for a true duplicate. The MAX+1 subquery
        # runs inside the INSERT under the write lock, so it stays race-safe.
        cur = _insert_idea(
            conn,
            values,
            raw_columns={
                "idea_index": (
                    "(SELECT COALESCE(MAX(idea_index) + 1, 0) "
                    "FROM idea_outcomes WHERE artifact_id = 0)"
                )
            },
        )
        conn.commit()
        if int(cur.rowcount or 0) == 0:
            return None  # deduped by the open-row wall
        return int(cur.lastrowid)


def list_open(*, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    init_idea_outcomes_tables(db_path)
    placeholders = ",".join("?" * len(OPEN_STATUSES))
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM idea_outcomes
            WHERE status IN ({placeholders}) AND synthetic = 0
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
            WHERE status IN ({placeholders}) AND origin != ? AND synthetic = 0
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
            WHERE status IN ({placeholders}) AND origin != ? AND synthetic = 0
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
            "WHERE origin != ? AND synthetic = 0 GROUP BY status",
            (VETOED_ORIGIN,),
        ).fetchall()
        origin_rows = conn.execute(
            """
            SELECT origin, status, COUNT(*) AS n
            FROM idea_outcomes
            WHERE synthetic = 0
            GROUP BY origin, status
            """
        ).fetchall()
        # Founder grades — vetoed journal-only lane is never founder-facing, so
        # it never counts toward the 5b gate.
        verdict_rows = conn.execute(
            "SELECT founder_verdict FROM idea_outcomes "
            "WHERE origin != ? AND synthetic = 0 AND founder_verdict IS NOT NULL "
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
