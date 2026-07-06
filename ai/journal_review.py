"""Journal review — realized calibration of the agent's own paper calls (Decision Journal J3).

SURFACE-ONLY. Reads the immutable ``decision_journal`` and compares the PREDICTED win%
(``score_json.win_prob`` recorded at propose time) against the REALIZED outcome, grouped by
strategy and by predicted-win% bucket, with sample counts + Wilson confidence intervals. It
NEVER mutates a strategy or the scorer — the learning layer observes and proposes; a human
arms any change (the standing "strategies are frozen; learning never mutates them" rule).

Honest by construction: a group under ``MIN_SAMPLES`` is ``verdict='insufficient_data'`` —
never a fabricated single win-rate. The verdict is grounded in the Wilson CI: predicted inside
the interval → well_calibrated; above it → overconfident; below → underconfident.

``compute_calibration`` is pure/deterministic given the journal rows (unit-testable);
``run_journal_review`` is the 24h-reflection hook that seeds lesson-memories on notable drift.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from ai.memories import get_memory_service
from db.base import DEFAULT_DB_PATH
from research.ev import wilson_ci

logger = logging.getLogger(__name__)

# Only trades that actually resolved are graded — open/never_filled/closed_incomplete have
# no realized win/loss to compare against a prediction.
_RESOLVED_OUTCOMES = frozenset({"win", "loss", "scratch"})

# Honesty bar: below this per-group sample size we say "insufficient_data", never a win rate.
MIN_SAMPLES = 10
# Notable calibration miss (in win-probability points) worth seeding a lesson for.
DRIFT_THRESHOLD = 0.10
# Cap lessons per review run (mirrors the retrospective's MAX_LESSONS).
MAX_LESSONS = 3
# How many recent journal rows to scan (matches the perception summary window).
_REVIEW_WINDOW = 500

# Predicted-win% buckets (win_prob is a 0–1 fraction). Last bucket is inclusive of 1.0.
_BUCKETS: tuple[tuple[str, str, float, float], ...] = (
    ("lt40", "<40%", 0.0, 0.4),
    ("40-60", "40–60%", 0.4, 0.6),
    ("60-80", "60–80%", 0.6, 0.8),
    ("gt80", ">80%", 0.8, 1.01),
)


def _win_prob(row: dict[str, Any]) -> float | None:
    """The predicted win probability recorded at propose time (0–1), or None."""
    score = row.get("score")
    if not isinstance(score, dict):
        return None
    raw = score.get("win_prob")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _bucket_for(win_prob: float) -> str:
    for bucket_id, _label, lo, hi in _BUCKETS:
        if lo <= win_prob < hi:
            return bucket_id
    return _BUCKETS[-1][0]


def _verdict(
    n: int, predicted: float, ci_low: float, ci_high: float, min_samples: int
) -> str:
    if n < min_samples:
        return "insufficient_data"
    if ci_low <= predicted <= ci_high:
        return "well_calibrated"
    if predicted > ci_high:
        return "overconfident"
    return "underconfident"


def _group_stats(
    key: str,
    label: str,
    items: list[tuple[dict[str, Any], str, float]],
    min_samples: int,
) -> dict[str, Any]:
    """Calibration for one group of resolved (row, outcome, predicted_win) tuples."""
    n = len(items)
    wins = sum(1 for _row, outcome, _wp in items if outcome == "win")
    losses = sum(1 for _row, outcome, _wp in items if outcome == "loss")
    scratches = sum(1 for _row, outcome, _wp in items if outcome == "scratch")
    predicted = sum(wp for _row, _o, wp in items) / n
    realized = wins / n
    ci_low, ci_high = wilson_ci(wins, n)
    r_vals = [
        float(row["r_multiple"])
        for row, _o, _wp in items
        if row.get("r_multiple") is not None
    ]
    avg_r = (sum(r_vals) / len(r_vals)) if r_vals else None
    return {
        "key": key,
        "label": label,
        "n": n,
        "wins": wins,
        "losses": losses,
        "scratches": scratches,
        "predicted_win": round(predicted, 4),
        "realized_win": round(realized, 4),
        "ci_low": round(ci_low, 4),
        "ci_high": round(ci_high, 4),
        "drift": round(realized - predicted, 4),
        "avg_r_multiple": round(avg_r, 3) if avg_r is not None else None,
        "verdict": _verdict(n, predicted, ci_low, ci_high, min_samples),
    }


def compute_calibration(
    rows: list[dict[str, Any]] | None = None,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    min_samples: int = MIN_SAMPLES,
) -> dict[str, Any]:
    """Compare PREDICTED vs REALIZED win% over resolved journal rows.

    Pure/deterministic given ``rows`` (pass them for tests). When ``rows`` is None, reads the
    most recent journal rows via ``db.decision_journal.list_recent`` (read-only). Returns
    per-strategy and per-bucket calibration groups; empty groups are omitted, and on an empty
    journal everything is empty (``n_resolved=0``) — the honest no-op, never a fabricated rate.
    """
    if rows is None:
        from db.decision_journal import list_recent

        rows = list_recent(limit=_REVIEW_WINDOW, db_path=db_path)

    resolved: list[tuple[dict[str, Any], str, float]] = []
    for row in rows:
        outcome = str(row.get("outcome") or "").strip().lower()
        if outcome not in _RESOLVED_OUTCOMES:
            continue
        win_prob = _win_prob(row)
        if win_prob is None:
            continue
        resolved.append((row, outcome, win_prob))

    by_strat: dict[str, list[tuple[dict[str, Any], str, float]]] = defaultdict(list)
    for row, outcome, win_prob in resolved:
        strategy_id = str(row.get("strategy_id") or "unattributed")
        by_strat[strategy_id].append((row, outcome, win_prob))
    by_strategy = [
        _group_stats(sid, sid, items, min_samples)
        for sid, items in sorted(by_strat.items())
    ]

    by_bucket_items: dict[str, list[tuple[dict[str, Any], str, float]]] = {
        bucket_id: [] for bucket_id, *_ in _BUCKETS
    }
    for row, outcome, win_prob in resolved:
        by_bucket_items[_bucket_for(win_prob)].append((row, outcome, win_prob))
    by_bucket = [
        _group_stats(bucket_id, label, by_bucket_items[bucket_id], min_samples)
        for bucket_id, label, _lo, _hi in _BUCKETS
        if by_bucket_items[bucket_id]
    ]

    return {
        "n_resolved": len(resolved),
        "min_samples": min_samples,
        "by_strategy": by_strategy,
        "by_bucket": by_bucket,
    }


def _lesson_text(group: dict[str, Any]) -> str:
    predicted = round(group["predicted_win"] * 100)
    realized = round(group["realized_win"] * 100)
    verdict = str(group["verdict"]).replace("_", " ")
    tail = (
        "consider recalibration"
        if group["verdict"] == "overconfident"
        else "may be leaving edge on the table"
    )
    return (
        f"{group['key']}: predicted ~{predicted}% win vs realized {realized}% over "
        f"{group['n']} resolved trades — {verdict}; {tail} "
        "(surface-only, not auto-applied)."
    )


def build_calibration_lessons(
    calibration: dict[str, Any],
    *,
    max_lessons: int = MAX_LESSONS,
    drift_threshold: float = DRIFT_THRESHOLD,
) -> list[str]:
    """Turn notable per-strategy calibration drift into lesson strings (largest drift first).

    Emits nothing for insufficient/well-calibrated groups or sub-threshold drift — the honest
    silence when there's nothing to learn yet."""
    scored: list[tuple[float, str]] = []
    for group in calibration.get("by_strategy", []):
        if group["verdict"] in ("insufficient_data", "well_calibrated"):
            continue
        if abs(group["drift"]) < drift_threshold:
            continue
        scored.append((abs(group["drift"]), _lesson_text(group)))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [text for _drift, text in scored[:max_lessons]]


def run_journal_review(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    memory: Any | None = None,
    max_lessons: int = MAX_LESSONS,
) -> dict[str, Any]:
    """Reflection hook: compute calibration + seed ≤``max_lessons`` lesson memories on drift.

    SURFACE-ONLY (writes only ``lesson`` memories; touches no strategy/scorer/config) and
    FAIL-SOFT — a review error returns an error-tagged dict and seeds nothing, so it can never
    break the reflection loop it hangs off."""
    try:
        calibration = compute_calibration(db_path=db_path)
        lessons = build_calibration_lessons(calibration, max_lessons=max_lessons)
        if lessons:
            mem = memory or get_memory_service()
            for text in lessons:
                mem.add(
                    text,
                    memory_type="lesson",
                    metadata={"source": "journal_calibration"},
                    source="reflection",
                )
        return {
            "n_resolved": calibration["n_resolved"],
            "lessons_seeded": len(lessons),
            "calibration": calibration,
        }
    except Exception:
        logger.exception("Journal calibration review failed (surface-only, non-fatal)")
        return {"n_resolved": 0, "lessons_seeded": 0, "calibration": None, "error": True}
