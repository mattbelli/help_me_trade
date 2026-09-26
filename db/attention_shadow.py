"""Jev shadow for the attention investigator — a gradable prediction, delivered nowhere.

**This is an experiment, not a lane.** Every name-day the investigator writes a prose
note about ALSO gets one typed Jev judgment on the *same* facts, persisted here and
shown to nobody. The point is a number that can be graded: ``(symbol, session)``
joins ``attention_outcomes``, so Jev's ``materiality`` / ``activity_unusual`` can be
scored against what the name actually did. That join is the only path by which a Jev
answer may ever become a displayable number — house law is that a displayed win% is
calibrated on our own ledger, never fabricated and never taken from a model's
self-reported ``confidence`` (which measures the spread of its own distribution, not
its chance of being right).

Why the shadow also runs when the prose note came back EMPTY: that 22% is exactly the
case a text model cannot answer. A typed answer cannot be blank, so recording those
rows is the cleanest evidence the shape difference is real.

Why its own table rather than ``llm_spend``: that ledger is character-counted budget
accounting ("the table IS the counter") with parse-outcome statuses that do not apply
here. Real token counts live HERE. If Jev graduates to a real lane, that is the moment
to decide whether ``llm_spend`` gains token columns or this table gets promoted.

Delete this module wholesale when the experiment concludes.
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

LANE = "attention_jev_shadow"

_INIT_DONE: set[str] = set()

STATUSES = frozenset({"ok", "unavailable", "error"})

_DDL = """
CREATE TABLE IF NOT EXISTS attention_jev_shadow (
    symbol TEXT NOT NULL,
    session TEXT NOT NULL,
    ts TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ok', 'unavailable', 'error')),
    model TEXT,
    latency_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    prompt_chars INTEGER,
    answers_json TEXT,
    text_model_ok INTEGER,
    source TEXT NOT NULL DEFAULT 'live',
    activity_unusual REAL,
    explained_by_supplied_fact REAL,
    materiality REAL,
    materiality_norm REAL,
    driver TEXT,
    driver_confidence REAL,
    evidence_thin REAL,
    next_check TEXT,
    detail TEXT,
    PRIMARY KEY (symbol, session)
);
CREATE INDEX IF NOT EXISTS idx_attention_jev_shadow_session
    ON attention_jev_shadow(session);
"""

_COLS = (
    "symbol",
    "session",
    "ts",
    "status",
    "model",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "prompt_chars",
    "answers_json",
    "text_model_ok",
    "source",
    "activity_unusual",
    "explained_by_supplied_fact",
    "materiality",
    "materiality_norm",
    "driver",
    "driver_confidence",
    "evidence_thin",
    "next_check",
    "detail",
)


def _db_path(db_path: Path | None) -> Path:
    return db_path if db_path is not None else DEFAULT_DB_PATH


def init_db(db_path: Path | None = None) -> None:
    resolved = _db_path(db_path)
    key = str(resolved)
    if key in _INIT_DONE:
        return
    # `graded_pairs` and `summary` JOIN attention_outcomes, so this module depends on
    # the attention ledger's schema: create it here rather than failing at read time.
    from db.attention import init_db as _init_attention_ledger

    _init_attention_ledger(resolved)
    with _connect(resolved) as conn:
        conn.executescript(_DDL)
        _migrate_pre_launch(conn)
        conn.commit()
    _INIT_DONE.add(key)


def _migrate_pre_launch(conn: Any) -> None:
    """Add `source` / relax `text_model_ok` on a table created by an earlier build.

    A drop is only taken while the table is EMPTY (it is, before the lane first runs:
    the shape was still moving). Once rows exist we add the column and leave the old
    NOT NULL standing — losing measurements to a schema tidy-up would be worse than
    an inconsistent default, and it is logged rather than done quietly.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(attention_jev_shadow)")}
    if not cols:
        return
    if "source" in cols and "explained_by_supplied_fact" in cols:
        return
    rows = conn.execute("SELECT COUNT(*) AS n FROM attention_jev_shadow").fetchone()["n"]
    if rows == 0:
        conn.execute("DROP TABLE attention_jev_shadow")
        conn.executescript(_DDL)
        return
    if "source" not in cols:
        logger.warning(
            "attention_jev_shadow: adding 'source' to a table holding %d rows; "
            "text_model_ok stays NOT NULL (NULL means 'not measured', which needs "
            "the new shape)",
            rows,
        )
        conn.execute(
            "ALTER TABLE attention_jev_shadow ADD COLUMN source TEXT NOT NULL DEFAULT 'live'"
        )
    if "explained_by_supplied_fact" not in cols:
        # Free to add: the answer was always recorded in answers_json, it just had no
        # denormalized column. populate_from_answers_json() fills it without a call.
        conn.execute(
            "ALTER TABLE attention_jev_shadow ADD COLUMN explained_by_supplied_fact REAL"
        )


def record(
    symbol: str,
    session: str,
    *,
    status: str,
    model: str | None = None,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    prompt_chars: int | None = None,
    answers: dict[str, Any] | None = None,
    text_model_ok: bool | None = False,
    source: str = "live",
    detail: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Idempotent per (symbol, session): a retried name-day overwrites its own row.

    ``text_model_ok`` is tri-state on purpose: True/False when the prose lane ran,
    and **None when it did not** (a replay of a session the investigator never
    processed). UNMEASURED is not FAILED — collapsing None into False would invent
    "Jev answered where prose failed" rows out of sessions where prose never ran.

    Fail-soft by construction — the caller is a best-effort shadow hanging off a
    delivery lane, so a write failure must never propagate.
    """
    try:
        init_db(db_path)
        scores = answers or {}
        row = {
            "symbol": str(symbol).strip().upper(),
            "session": str(session),
            "ts": _now(),
            "status": status,
            "model": model,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "prompt_chars": prompt_chars,
            "answers_json": json.dumps(scores, default=str) if scores else None,
            "text_model_ok": None if text_model_ok is None else (1 if text_model_ok else 0),
            "source": source,
            "activity_unusual": _num(scores.get("activity_unusual")),
            "explained_by_supplied_fact": _num(scores.get("explained_by_supplied_fact")),
            "materiality": _num(scores.get("materiality")),
            "materiality_norm": _num(scores.get("materiality_norm")),
            "driver": _text(scores.get("driver")),
            "driver_confidence": _num(scores.get("driver_confidence")),
            "evidence_thin": _num(scores.get("evidence_thin")),
            "next_check": _text(scores.get("next_check")),
            "detail": detail,
        }
        placeholders = ", ".join("?" for _ in _COLS)
        with _connect(_db_path(db_path)) as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO attention_jev_shadow ({', '.join(_COLS)}) "
                f"VALUES ({placeholders})",
                tuple(row[col] for col in _COLS),
            )
            conn.commit()
    except Exception:
        logger.exception("attention_jev_shadow: record failed for %s %s", symbol, session)


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def populate_from_answers_json(*, db_path: Path | None = None) -> int:
    """Backfill denormalized columns from `answers_json` — no API calls, already paid for.

    Used when a question was recorded in the JSON blob before it had its own column.
    Returns the number of rows touched.
    """
    init_db(db_path)
    resolved = _db_path(db_path)
    touched = 0
    with _connect(resolved) as conn:
        rows = conn.execute(
            "SELECT symbol, session, answers_json FROM attention_jev_shadow "
            "WHERE answers_json IS NOT NULL AND explained_by_supplied_fact IS NULL"
        ).fetchall()
        for row in rows:
            try:
                answers = json.loads(row["answers_json"])
            except (TypeError, ValueError):
                continue
            value = _num(answers.get("explained_by_supplied_fact"))
            if value is None:
                continue
            conn.execute(
                "UPDATE attention_jev_shadow SET explained_by_supplied_fact = ? "
                "WHERE symbol = ? AND session = ?",
                (value, row["symbol"], row["session"]),
            )
            touched += 1
        conn.commit()
    return touched


def get(symbol: str, session: str, *, db_path: Path | None = None) -> dict[str, Any] | None:
    init_db(db_path)
    with _connect(_db_path(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM attention_jev_shadow WHERE symbol = ? AND session = ?",
            (str(symbol).strip().upper(), str(session)),
        ).fetchone()
    return dict(row) if row else None


def graded_pairs(*, db_path: Path | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """Shadow predictions joined to realized attention outcomes, newest first.

    Raw material for whoever grades it. No metric is computed here — the caller
    decides when the sample is large enough to mean anything.
    """
    init_db(db_path)
    with _connect(_db_path(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT s.symbol, s.session, s.ts, s.model, s.materiality_norm,
                   s.activity_unusual, s.driver, s.evidence_thin, s.text_model_ok,
                   o.angles, o.quintile, o.entry, o.move_2h, o.move_close,
                   o.excess_2h, o.direction, o.graded_at
            FROM attention_jev_shadow s
            JOIN attention_outcomes o
              ON o.symbol = s.symbol AND o.session = s.session
            WHERE s.status = 'ok'
            ORDER BY s.session DESC, s.symbol
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def summary(*, db_path: Path | None = None, sessions: int = 10) -> dict[str, Any]:
    """Coverage and outcome counts for the experiment. Descriptive only.

    Deliberately computes NO hit rate: the floor, the honesty label, and the sample
    are the caller's business (see the module docstring). Read the ``note`` field.
    """
    init_db(db_path)
    resolved = _db_path(db_path)
    with _connect(resolved) as conn:
        by_status = {
            row["status"]: row["n"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM attention_jev_shadow GROUP BY status"
            ).fetchall()
        }
        recent = [
            row["session"]
            for row in conn.execute(
                "SELECT DISTINCT session FROM attention_jev_shadow "
                "ORDER BY session DESC LIMIT ?",
                (int(sessions),),
            ).fetchall()
        ]
        by_source = {
            row["source"]: row["n"]
            for row in conn.execute(
                "SELECT source, COUNT(*) AS n FROM attention_jev_shadow GROUP BY source"
            ).fetchall()
        }
        coverage: list[dict[str, Any]] = []
        for session in recent:
            row = conn.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM attention_jev_shadow WHERE session = ?) AS shadow,
                  (SELECT COUNT(*) FROM attention_jev_shadow
                     WHERE session = ? AND status = 'ok') AS answered,
                  (SELECT COUNT(*) FROM attention_jev_shadow
                     WHERE session = ? AND status = 'ok' AND text_model_ok = 0)
                     AS recovered_from_text_failure,
                  (SELECT COUNT(*) FROM attention_jev_shadow
                     WHERE session = ? AND status = 'ok' AND text_model_ok IS NULL)
                     AS text_model_not_run
                """,
                (session, session, session, session),
            ).fetchone()
            coverage.append({"session": session, **dict(row)})
        outcomes = conn.execute(
            "SELECT COUNT(*) AS n FROM attention_outcomes WHERE horizon_complete = 1"
        ).fetchone()["n"]
        graded = conn.execute(
            """
            SELECT COUNT(*) AS n FROM attention_jev_shadow s
            JOIN attention_outcomes o
              ON o.symbol = s.symbol AND o.session = s.session
            WHERE s.status = 'ok' AND o.horizon_complete = 1
            """
        ).fetchone()["n"]

    return {
        "by_status": by_status,
        "by_source": by_source,
        "total": sum(by_status.values()),
        "coverage": coverage,
        "graded": graded,
        "graded_outcomes_available": outcomes,
        "note": (
            "Descriptive only — no hit rate is computed here. Shadow answers are "
            "model claims, not calibration; grade them against the ledger before "
            "showing anyone a number."
        ),
    }
