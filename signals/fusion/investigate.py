"""Phase C1 — the SHADOW-MODE investigation gate (spec §2.6 LLM tier, §3 C).

The deterministic decision "this evidence stack would be investigated by the LLM
lane right now" — counted, never acted on. Zero LLM calls anywhere in this layer.
It reads the trailing-session score distribution to set today's investigation bar,
applies the daily budget (with the opening reserve so the bell can't drain the
day), and stamps one row per candidate episode into ``fusion_investigations``.

``REASON_SHADOW_ONLY`` is the C1↔C3 seam: the gate asserts it and does nothing but
count. C3 flips the flag in one place (``config/idea_engine``) to arm real
investigation — until then this module has no side effects beyond its own ledger
and one ``lane_stats`` meter.

Called from ``signals/fusion/intake`` right after ``cards.update_stack_card``,
in the same single-writer, sync execution context — the few reads/writes here are
the only new work on that path.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from config.idea_engine import (
    INVESTIGATE_MIN_POOL,
    INVESTIGATE_TRAILING_SESSIONS,
    REASON_DAILY_BUDGET,
    REASON_OPENING_RESERVE_FRAC,
    REASON_OPENING_WINDOW_MIN,
    REASON_SHADOW_ONLY,
)
from db.base import DEFAULT_DB_PATH, now_iso
from db.base import connect as _connect
from db.lane_stats import bump
from trading.market_calendar import is_trading_day
from trading.market_hours import _SESSION_OPEN

# The Phase C archetype is ONE co-fire: the scorer's options-actor group AND the
# structure group both scored (group names read from signals/fusion/scorer.py
# GROUP_COMPONENTS — options_actor + structure). "Scored" = a positive-score key
# in the episode's score_state group_scores, the exact set the scorer emits.
ARCHETYPE_GROUPS = frozenset({"options_actor", "structure"})

# ceil(0.25 × 5) = 2 investigations may be spent inside the opening window; the
# rest of the daily budget stays reserved until the window closes.
_OPENING_RESERVE = math.ceil(REASON_OPENING_RESERVE_FRAC * REASON_DAILY_BUDGET)

_ET = ZoneInfo("America/New_York")

_NO_BASELINE: dict[str, Any] = {
    "has_baseline": False,
    "threshold": None,
    "pool_size": 0,
    "sessions": 0,
    "widened": False,
}


def _scored_groups(score_json: Any) -> set[str]:
    """The set of groups the scorer actually scored, from a persisted state row."""
    if not score_json:
        return set()
    try:
        payload = json.loads(score_json)
    except (TypeError, ValueError):
        return set()
    groups = payload.get("group_scores")
    return set(groups) if isinstance(groups, dict) else set()


def _compute_baseline(market_day: str, db_path: Path) -> dict[str, Any]:
    """Today's investigation bar from the trailing sessions (all strictly < today).

    Pool = day-final scores of archetype-eligible non-contested episodes over the
    trailing ``INVESTIGATE_TRAILING_SESSIONS`` distinct market days. Threshold =
    the (B×S)-th highest score (B = daily budget, S = distinct days in the pool),
    so ~B stacks clear an average day; a pool smaller than B×S falls back to its
    minimum. Cold start: an archetype pool under ``INVESTIGATE_MIN_POOL`` widens
    to ALL non-contested episodes over the same window; still under the floor →
    no baseline exists today (self-heals as sessions accrue).
    """
    with _connect(db_path) as conn:
        days = [
            str(row[0])
            for row in conn.execute(
                """
                SELECT DISTINCT market_day FROM fusion_episodes
                WHERE market_day < ?
                ORDER BY market_day DESC
                LIMIT ?
                """,
                (market_day, INVESTIGATE_TRAILING_SESSIONS),
            ).fetchall()
        ]
        if not days:
            return dict(_NO_BASELINE)
        placeholders = ",".join("?" for _ in days)
        rows = conn.execute(
            f"""
            SELECT e.market_day AS market_day, e.score AS score,
                   (SELECT h.score_json FROM fusion_score_history h
                    WHERE h.episode_id = e.id
                    ORDER BY h.id DESC LIMIT 1) AS score_json
            FROM fusion_episodes e
            WHERE e.contested = 0 AND e.market_day IN ({placeholders})
            """,
            days,
        ).fetchall()

    all_pool: list[tuple[str, float]] = []
    archetype_pool: list[tuple[str, float]] = []
    for row in rows:
        entry = (str(row["market_day"]), float(row["score"]))
        all_pool.append(entry)
        if ARCHETYPE_GROUPS <= _scored_groups(row["score_json"]):
            archetype_pool.append(entry)

    if len(archetype_pool) >= INVESTIGATE_MIN_POOL:
        pool, widened = archetype_pool, False
    elif len(all_pool) >= INVESTIGATE_MIN_POOL:
        pool, widened = all_pool, True
    else:
        return {
            **_NO_BASELINE,
            "pool_size": len(all_pool),
            "sessions": len({day for day, _ in all_pool}),
        }

    scores = sorted((score for _, score in pool), reverse=True)
    sessions = len({day for day, _ in pool})
    rank = REASON_DAILY_BUDGET * sessions
    threshold = scores[-1] if len(scores) < rank else scores[rank - 1]
    return {
        "has_baseline": True,
        "threshold": float(threshold),
        "pool_size": len(scores),
        "sessions": sessions,
        "widened": widened,
    }


def _baseline(market_day: str, *, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    """Cached per-day baseline — compute once on first use, read the row after."""
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT decision, threshold, pool_size, sessions, widened
            FROM fusion_investigation_baseline WHERE market_day = ?
            """,
            (market_day,),
        ).fetchone()
    if row is not None:
        return {
            "has_baseline": row["decision"] == "ok",
            "threshold": None if row["threshold"] is None else float(row["threshold"]),
            "pool_size": int(row["pool_size"]),
            "sessions": int(row["sessions"]),
            "widened": bool(row["widened"]),
        }
    result = _compute_baseline(market_day, db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO fusion_investigation_baseline(
                market_day, decision, threshold, pool_size, sessions, widened, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market_day,
                "ok" if result["has_baseline"] else "no_baseline",
                result["threshold"],
                int(result["pool_size"]),
                int(result["sessions"]),
                int(result["widened"]),
                now_iso(),
            ),
        )
        conn.commit()
    return result


def _shadow_spent(market_day: str, db_path: Path) -> int:
    """Budget spent today = COUNT of shadow_investigate rows (the table is the
    counter — never a parallel tally)."""
    with _connect(db_path) as conn:
        return int(
            conn.execute(
                """
                SELECT COUNT(*) FROM fusion_investigations
                WHERE market_day = ? AND decision = 'shadow_investigate'
                """,
                (market_day,),
            ).fetchone()[0]
        )


def _in_opening_window(when: datetime) -> bool:
    """True during the first REASON_OPENING_WINDOW_MIN minutes of a trading
    session (09:30 ET open + window), in America/New_York wall-clock time."""
    et = when.astimezone(_ET)
    if not is_trading_day(et.date()):
        return False
    open_minute = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
    minute_of_day = et.hour * 60 + et.minute
    return open_minute <= minute_of_day < open_minute + REASON_OPENING_WINDOW_MIN


def _record(
    episode_id: str,
    market_day: str,
    symbol: str,
    decision: str,
    score: float,
    threshold: float | None,
    db_path: Path,
) -> None:
    """Upsert one decision row on the episode PK — a re-fire may UPGRADE a
    deferred/dropped decision, never mint a second row."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO fusion_investigations(
                episode_id, market_day, symbol, decided_at, decision, score, threshold
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(episode_id) DO UPDATE SET
                market_day = excluded.market_day,
                symbol = excluded.symbol,
                decided_at = excluded.decided_at,
                decision = excluded.decision,
                score = excluded.score,
                threshold = excluded.threshold
            """,
            (episode_id, market_day, symbol, now_iso(), decision, score, threshold),
        )
        conn.commit()


def evaluate_investigation(
    episode: dict[str, Any],
    score_state: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
    now: datetime | None = None,
) -> str | None:
    """Would the LLM lane investigate this stack right now? (shadow only).

    Returns the recorded decision, or None when the stack is not an investigation
    candidate — wrong archetype, contested, or below the day's threshold. Those
    are non-decisions and never become rows; only the four budget/baseline
    outcomes are stamped.
    """
    # C1 is shadow-only: the flag is the single seam C3 flips to arm acting.
    assert REASON_SHADOW_ONLY, "C1 investigate gate is shadow-only (flip in C3)"

    group_scores = score_state.get("group_scores") or {}
    if not ARCHETYPE_GROUPS <= set(group_scores):
        return None  # not the Phase C archetype (options-actor + structure co-fire)
    if bool(score_state.get("contested")):
        return None  # contested stacks are never LLM-tier (§2.3)

    episode_id = str(episode["id"])
    market_day = str(episode["market_day"])
    symbol = str(episode.get("symbol") or "")
    score = float(score_state.get("score") or 0.0)
    when = (now or datetime.now(_ET)).astimezone(_ET)

    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT decision FROM fusion_investigations WHERE episode_id = ?",
            (episode_id,),
        ).fetchone()
    if existing is not None and existing["decision"] == "shadow_investigate":
        return "shadow_investigate"  # sticky/terminal — never re-decided or re-bumped

    baseline = _baseline(market_day, db_path=db_path)
    if not baseline["has_baseline"]:
        _record(episode_id, market_day, symbol, "no_baseline", score, None, db_path)
        return "no_baseline"

    threshold = float(baseline["threshold"])
    if score < threshold:
        return None  # below the day's investigation bar — a non-decision, no row

    spent = _shadow_spent(market_day, db_path)
    if spent >= REASON_DAILY_BUDGET:
        decision = "dropped_budget"
    elif _in_opening_window(when) and spent >= _OPENING_RESERVE:
        decision = "reserve_deferred"
    else:
        decision = "shadow_investigate"

    _record(episode_id, market_day, symbol, decision, score, threshold, db_path)
    if decision == "shadow_investigate":
        # Meter keyed on the decision's market day (not the wall-clock ET date) so
        # a late decision counts on the session it belongs to. Bumped exactly once
        # per episode: the sticky early-return above guarantees no re-bump.
        bump(
            "fusion_investigate",
            "fusion_would_investigate",
            date=market_day,
            db_path=db_path,
        )
    return decision
