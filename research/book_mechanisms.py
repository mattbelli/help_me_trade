"""Closed mechanism vocabulary + n-gated scoreboard for the LLM paper books.

AQuA-shaped Keeper 2: the analyst proposes inside a constrained schema; the
idea tracker (sealed, unchanged) grades price; this module GROUPS those
outcomes by mechanism and renders a pack section. The nightly LLM never
writes a confidence number — n < MIN_SAMPLES is ``insufficient``, never a
0.7. Book-scoped only: nothing here writes fusion, perception, or chat memory.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from db.base import DEFAULT_DB_PATH, connect
from db.idea_outcomes import OPEN_STATUSES, RESOLVED_WIN_LOSS, init_idea_outcomes_tables
from research.ev import wilson_ci

logger = logging.getLogger(__name__)

# Honesty bar — same n as journal_review.MIN_SAMPLES and the 5b grading gate.
# Below this, the pack prints counts and ``insufficient``, never a rate.
MIN_SAMPLES = 10

# Closed vocabulary. Unknown strings are dropped (not stored, not scored).
# Aliases absorb hyphen/synonym drift so a near-miss still groups.
MECHANISMS: tuple[str, ...] = (
    "trend_pullback",  # dip in an intact trend
    "breakout_hold",  # range/level break that holds
    "mean_revert",  # stretch back to value/node
    "gap_fill",  # session/overnight gap expected to fill
    "earnings_drift",  # post-print drift
    "flow_follow",  # unusual options/tape flow is the reason
    "macro_hedge",  # rates/dollar/gold/vol as book hedge
    "catalyst_fade",  # news already in the price
    "relative_strength",  # name vs sector/index
    "failed_move",  # failed breakdown/breakout
)
MECHANISM_SET = frozenset(MECHANISMS)

_MECHANISM_ALIASES: dict[str, str] = {
    "gap-fill": "gap_fill",
    "gapfill": "gap_fill",
    "pullback": "trend_pullback",
    "dip_buy": "trend_pullback",
    "dipbuy": "trend_pullback",
    "pead": "earnings_drift",
    "post_earnings": "earnings_drift",
    "flow": "flow_follow",
    "fade": "catalyst_fade",
    "mean-revert": "mean_revert",
    "mean_reversion": "mean_revert",
    "breakout": "breakout_hold",
    "rs": "relative_strength",
    "hedge": "macro_hedge",
    "spring": "failed_move",
    "failed_breakdown": "failed_move",
    "failed_breakout": "failed_move",
}

# Stored for the pre-mortem / founder 5b grade. NOT scored by the tracker —
# the stop is the operational falsifier; these are labels, not predicates.
FAILURE_MODES: tuple[str, ...] = (
    "breaks_level",
    "thesis_date",
    "earnings_print",
    "regime_flip",
    "flow_reverses",
    "correlation_break",
    "time_stop",
)
FAILURE_MODE_SET = frozenset(FAILURE_MODES)
_FAILURE_CAP = 3

_STATUS_INSUFFICIENT = "insufficient"
_STATUS_WORKING = "working"
_STATUS_FAILING = "failing"
_STATUS_MIXED = "mixed"


def _norm_key(raw: Any) -> str:
    return str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_mechanism(raw: Any) -> str | None:
    """Map a model string onto the closed enum, or None if unknown."""
    key = _norm_key(raw)
    if not key:
        return None
    if key in MECHANISM_SET:
        return key
    return _MECHANISM_ALIASES.get(key)


def normalize_failure_modes(raw: Any) -> list[str]:
    """Keep at most ``_FAILURE_CAP`` known modes; drop the rest silently."""
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return []
    seen: list[str] = []
    for item in items:
        key = _norm_key(item)
        if key in FAILURE_MODE_SET and key not in seen:
            seen.append(key)
        if len(seen) >= _FAILURE_CAP:
            break
    return seen


def _verdict(won: int, lost: int) -> tuple[str, tuple[float, float] | None]:
    n = won + lost
    if n < MIN_SAMPLES:
        return _STATUS_INSUFFICIENT, None
    lo, hi = wilson_ci(won, n)
    if hi < 0.5:
        return _STATUS_FAILING, (lo, hi)
    if lo > 0.5:
        return _STATUS_WORKING, (lo, hi)
    return _STATUS_MIXED, (lo, hi)


def mechanism_scoreboard(
    origins: Sequence[str],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Per-mechanism counts for a book's lanes. Derived read — no writes.

    ``n`` is won+lost (expired/invalidated/data_gap do not enter the rate).
    ``status`` is insufficient until n ≥ MIN_SAMPLES; Wilson is omitted until then.
    """
    origin_list = [str(o) for o in origins if o]
    if not origin_list:
        return []

    init_idea_outcomes_tables(db_path)
    placeholders = ",".join("?" * len(origin_list))
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT mechanism, status, COUNT(*) AS n,
                   SUM(CASE WHEN alpha_pct IS NOT NULL THEN alpha_pct ELSE 0 END)
                       AS alpha_sum,
                   SUM(CASE WHEN alpha_pct IS NOT NULL THEN 1 ELSE 0 END) AS alpha_n
            FROM idea_outcomes
            WHERE origin IN ({placeholders})
              AND mechanism IS NOT NULL
              AND mechanism != ''
              AND synthetic = 0
            GROUP BY mechanism, status
            """,
            origin_list,
        ).fetchall()

    by_mech: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "open": 0,
            "won": 0,
            "lost": 0,
            "expired": 0,
            "alpha_sum": 0.0,
            "alpha_n": 0,
        }
    )
    for row in rows:
        mech = str(row["mechanism"] or "")
        if mech not in MECHANISM_SET:
            continue
        status = str(row["status"] or "")
        n = int(row["n"] or 0)
        cell = by_mech[mech]
        if status in OPEN_STATUSES:
            cell["open"] += n
        elif status == "won":
            cell["won"] += n
        elif status == "lost":
            cell["lost"] += n
        elif status == "expired":
            cell["expired"] += n
        if status in (RESOLVED_WIN_LOSS | {"expired"}):
            cell["alpha_sum"] += float(row["alpha_sum"] or 0)
            cell["alpha_n"] += int(row["alpha_n"] or 0)

    board: list[dict[str, Any]] = []
    for mech in MECHANISMS:
        cell = by_mech.get(mech)
        if cell is None:
            continue
        won, lost = int(cell["won"]), int(cell["lost"])
        n_wl = won + lost
        verdict, interval = _verdict(won, lost)
        mean_alpha = (
            cell["alpha_sum"] / cell["alpha_n"] if cell["alpha_n"] else None
        )
        board.append(
            {
                "mechanism": mech,
                "open": int(cell["open"]),
                "won": won,
                "lost": lost,
                "expired": int(cell["expired"]),
                "n": n_wl,
                "mean_alpha_pct": mean_alpha,
                "wilson": interval,
                "status": verdict,
            }
        )
    return board


def render_mechanism_ledger(
    origins: Sequence[str],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> str:
    """Markdown for the nightly pack. Vocabulary always listed; rates n-gated."""
    vocab = ", ".join(MECHANISMS)
    lines = [
        "Closed vocabulary — every candidate MUST pick one of: " + vocab + ".",
        "Unknown mechanism = the candidate is unfinished (dropped at parse).",
        "This table is scored by code after the idea tracker resolves a row. "
        "Never invent a numeric belief-confidence; n < "
        f"{MIN_SAMPLES} is insufficient, not a win%.",
        "",
    ]
    try:
        board = mechanism_scoreboard(origins, db_path=db_path)
    except Exception:
        logger.exception("mechanism scoreboard failed")
        lines.append("_unavailable_")
        return "\n".join(lines)
    if not board:
        lines.append("_no mechanism-tagged ideas yet_")
        return "\n".join(lines)

    lines.append("| mechanism | open | won/lost | n | α | Wilson | status |")
    lines.append("|---|---:|---:|---:|---:|---|---|")
    for row in board:
        if row["n"] == 0 and row["open"] == 0 and row["expired"] == 0:
            continue
        alpha = (
            f"{row['mean_alpha_pct']:+.1f}%"
            if row["mean_alpha_pct"] is not None
            else "—"
        )
        if row["wilson"] is None:
            wilson = "—"
        else:
            lo, hi = row["wilson"]
            wilson = f"[{lo:.2f}, {hi:.2f}]"
        lines.append(
            f"| {row['mechanism']} | {row['open']} | {row['won']}/{row['lost']} "
            f"| {row['n']} | {alpha} | {wilson} | {row['status']} |"
        )
    return "\n".join(lines)
