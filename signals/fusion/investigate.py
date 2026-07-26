"""Phase C1 — the SHADOW-MODE investigation gate (spec §2.6 LLM tier, §3 C).

The deterministic decision "this evidence stack would be investigated by the LLM
lane right now" — counted, never acted on. Zero LLM calls anywhere in this layer.
The cut-line is the z DUAL GATE (scoring-normalization spec, founder T pick
2026-07-22): thesis z ≥ FUSION_Z_GATE_T AND conviction ≥ CONVICTION_BAR, both on
the seedless ``score_decayed`` math the fusion_z_baseline T tables were computed
from. It then applies the daily budget (with the opening reserve so the bell
can't drain the day) and stamps one row per candidate episode into
``fusion_investigations``. (The pre-z trailing-percentile baseline machinery —
``fusion_investigation_baseline`` — was removed with the T pick; the table and
its historical rows remain for audit.)

``REASON_SHADOW_ONLY`` is the C1↔C3 seam: the gate asserts it and does nothing but
count. C3 flips the flag in one place (``config/idea_engine``) to arm real
investigation — until then this module has no side effects beyond its own ledger
and one ``lane_stats`` meter.

Called from ``signals/fusion/intake`` right after ``cards.update_stack_card``,
in the same single-writer, sync execution context — the few reads/writes here are
the only new work on that path.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from config.idea_engine import (
    REASON_DAILY_BUDGET,
    REASON_OPENING_RESERVE_FRAC,
    REASON_OPENING_WINDOW_MIN,
    REASON_SHADOW_ONLY,
)
from db.base import DEFAULT_DB_PATH, now_iso
from db.base import connect as _connect
from db.lane_stats import bump
from signals.fusion.scorer import CONVICTION_BAR, FUSION_Z_GATE_T
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
    candidate — wrong archetype, or below either leg of the day's dual gate. Those
    are non-decisions and never become rows; only the four budget/baseline
    outcomes are stamped.
    """
    # C1 is shadow-only: the flag is the single seam C3 flips to arm acting.
    assert REASON_SHADOW_ONLY, "C1 investigate gate is shadow-only (flip in C3)"

    group_scores = score_state.get("group_scores") or {}
    if not ARCHETYPE_GROUPS <= set(group_scores):
        return None  # not the Phase C archetype (options-actor + structure co-fire)
    # NOTE: score_state["contested"] — score_dots' binary contested-if-EVER bit —
    # is deliberately NOT gated on here. It fires when any actor group saw both a
    # bull and a bear dot anywhere in the session, undecayed and magnitude-blind;
    # the Horizon Redesign (2026-07-21 §1) named that the disease and shipped
    # score_decayed to replace it. The conviction leg of the dual gate below IS
    # the contested test now — contemporaneous, decayed, magnitude-aware — and it
    # strictly dominates the bit (§2.3's "never LLM-tier" rule, measured properly).
    #
    # Gating on both is not belt-and-braces, it is an empty set: clearing z ≥ T
    # needs accumulated mass, and accumulated mass on a liquid name guarantees at
    # least one contrary print, which trips the bit. Measured over 9,049 archetype
    # scorings on 07-22/23/24 — 691 cleared the dual gate, 99 cleared the bit,
    # ZERO cleared both, so this lane admitted nothing for three sessions.

    episode_id = str(episode["id"])
    market_day = str(episode["market_day"])
    symbol = str(episode.get("symbol") or "")
    when = (now or datetime.now(_ET)).astimezone(_ET)

    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT decision FROM fusion_investigations WHERE episode_id = ?",
            (episode_id,),
        ).fetchone()
    if existing is not None and existing["decision"] == "shadow_investigate":
        return "shadow_investigate"  # sticky/terminal — never re-decided or re-bumped

    # The z DUAL GATE (founder T pick 2026-07-22): significance AND effect
    # size, read from the seedless decayed thesis _rescore stashes in the
    # state — the same math the fusion_z_baseline T tables were computed
    # from, so T stays on one scale. A missing thesis (legacy caller) gates
    # closed, never open. Below either leg = a non-decision, no row. The
    # recorded score is the z (the value compared against the threshold).
    thesis = score_state.get("decayed_thesis") or {}
    z = float(thesis.get("z") or 0.0)
    conviction = float(thesis.get("conviction") or 0.0)
    if z < FUSION_Z_GATE_T or conviction < CONVICTION_BAR:
        return None
    threshold = FUSION_Z_GATE_T
    score = z

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
