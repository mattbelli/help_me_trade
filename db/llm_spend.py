"""LLM spend ledger — one row per investigation-lane completion (Unified §4).

Every investigator call (T1 triage, REASON v1) resolves to exactly one terminal
status persisted BEFORE the response is used, alongside an ``llm_spend`` row here.
**The table IS the counter** — there is no parallel tally: budgets are enforced by
counting rows (``lane_spent``), so accounting can never drift from what was actually
spent. Attempted calls consume budget (an ``ok``/``parse_fail``/``refusal``/
``timeout`` row each counts); a ``budget_denied`` row records a refusal-to-call and
must NOT count against the budget, or a single denial would self-perpetuate.

Char counts, never token counts: the client returns no usage on ``complete``, and
this house never fabricates token numbers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH, now_iso
from db.base import connect as _connect

# The terminal statuses a call resolves to (Unified component 4). Mirrored in a
# SQLite CHECK so a bad status can never land silently.
SPEND_STATUSES = frozenset(
    {"ok", "parse_fail", "refusal", "timeout", "budget_denied"}
)


def init_llm_spend(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS llm_spend (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                market_day TEXT NOT NULL,
                lane TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK(status IN (
                    'ok', 'parse_fail', 'refusal', 'timeout', 'budget_denied'
                )),
                latency_ms INTEGER,
                prompt_chars INTEGER,
                completion_chars INTEGER,
                episode_id TEXT,
                detail TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_llm_spend_day_lane
                ON llm_spend(market_day, lane);
            """
        )
        conn.commit()


def record_spend(
    market_day: str,
    lane: str,
    status: str,
    *,
    model: str = "",
    latency_ms: int | None = None,
    prompt_chars: int | None = None,
    completion_chars: int | None = None,
    episode_id: str | None = None,
    detail: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Append one spend row (the persist-before-use record). Returns its id."""
    if status not in SPEND_STATUSES:
        raise ValueError(f"llm_spend: unknown status {status!r}")
    init_llm_spend(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO llm_spend (
                ts, market_day, lane, model, status, latency_ms,
                prompt_chars, completion_chars, episode_id, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso(),
                str(market_day),
                str(lane),
                str(model or ""),
                str(status),
                None if latency_ms is None else int(latency_ms),
                None if prompt_chars is None else int(prompt_chars),
                None if completion_chars is None else int(completion_chars),
                str(episode_id) if episode_id else None,
                detail,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def lane_spent(market_day: str, lane: str, *, db_path: Path = DEFAULT_DB_PATH) -> int:
    """Budget consumed on (market_day, lane) = COUNT of ATTEMPTED calls.

    ``budget_denied`` rows are excluded: a denial is a refusal-to-call, so counting
    it would let one denial keep the lane denied forever.
    """
    init_llm_spend(db_path)
    with _connect(db_path) as conn:
        return int(
            conn.execute(
                """
                SELECT COUNT(*) FROM llm_spend
                WHERE market_day = ? AND lane = ? AND status != 'budget_denied'
                """,
                (str(market_day), str(lane)),
            ).fetchone()[0]
        )


def episode_attempts(
    market_day: str, lane: str, episode_id: str, *, db_path: Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    """ATTEMPTED spend rows for one episode (budget_denied excluded), oldest first.

    The crash-recovery anchor (Fusion C5): the spend row is the persist-before-use
    record — it commits BEFORE the idea/shadow/marker, so a crash in the
    spend→register window is detectable from it ALONE, never from idea_outcomes."""
    init_llm_spend(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM llm_spend
            WHERE market_day = ? AND lane = ? AND episode_id = ?
              AND status != 'budget_denied'
            ORDER BY id ASC
            """,
            (str(market_day), str(lane), str(episode_id)),
        ).fetchall()
    return [dict(row) for row in rows]


def day_spend(market_day: str, *, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """All spend rows for a market day (dev/debug read; oldest first)."""
    init_llm_spend(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM llm_spend WHERE market_day = ? ORDER BY id ASC",
            (str(market_day),),
        ).fetchall()
    return [dict(row) for row in rows]
