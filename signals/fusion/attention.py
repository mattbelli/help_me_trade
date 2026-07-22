"""Attention tiers + the flow hot lane (Round 4 — FUSION_ATTENTION_TIERS_ROUND4_SPEC).

Principle: attention follows the trade lifecycle. Perception resources rank

    open positions / live ideas  >  triggered ideas  >  armed ideas

with the founder watchlist OUTSIDE the ladder (explicit human ask — always
served, never evicted by engine promotion; ``merge_hot_lane`` appends hot-lane
names AFTER the watchlist so cap pressure clips promotions first).

Tiers are a DERIVED VIEW over state that already exists (idea lifecycle,
managed positions, episode masses) — no new state machine. The one persisted
piece is ``flow_hot_lane``, because hysteresis needs memory: promote
instantly, demote only after FUSION_HOT_LANE_DEMOTE_SESSIONS quiet trading
days, never flap. ``promoted_at`` is never rewritten on requalify — it is the
symbol's per-symbol flow-baseline stationarity floor (its options-flow history
starts there; Stage-2 must never back-compare across it).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.idea_engine import FUSION_HOT_LANE_DEMOTE_SESSIONS
from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from signals.fusion.lifecycle import _parse_dt, _trading_days_between
from trading.market_hours import _ET

logger = logging.getLogger("hmt.fusion.attention")

# Strongest reason wins on upsert; readers order the lane by this rank.
_REASON_RANK = {"position": 0, "idea_triggered": 1, "idea_armed": 2}
# Ledger lifecycle_state → hot-lane reason. 'live' = entered — position-grade
# attention even when the fill isn't in managed_positions (manual takes).
_STATE_REASON = {
    "live": "position",
    "trigger_fired": "idea_triggered",
    "armed": "idea_armed",
}


def attention_tier(*, hot_reason: str | None, thesis_gross: float, noise_floor: float) -> str:
    """T1 'live' (hot lane) / T2 'poll' (episode above noise) / T3 'scan'."""
    if hot_reason is not None:
        return "live"
    return "poll" if thesis_gross >= noise_floor else "scan"


def hot_lane_rows(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Current lane, strongest reason first, then earliest promotion."""
    with _connect(db_path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT symbol, reason, promoted_at, last_qualified_at FROM flow_hot_lane"
            ).fetchall()
        ]
    rows.sort(key=lambda r: (_REASON_RANK.get(r["reason"], 99), str(r["promoted_at"])))
    return rows


def merge_hot_lane(base: list[str], db_path: Path = DEFAULT_DB_PATH) -> list[str]:
    """Watchlist ∪ hot lane, watchlist first (never evicted — the downstream
    OPTIONS_FLOW_MAX_UNDERLYINGS clip eats the hot-lane tail, not the founder's
    list). Fail-soft: a broken lane read must never blind the flow collector
    to the watchlist it already had."""
    try:
        seen = {s.strip().upper() for s in base}
        extra = [
            row["symbol"]
            for row in hot_lane_rows(db_path)
            if row["symbol"] not in seen
        ]
        return list(base) + extra
    except Exception:
        logger.exception("hot-lane merge failed — serving watchlist only")
        return list(base)


def sweep_hot_lane(
    now: datetime,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, int]:
    """Reconcile the lane against current state (called from the 60s lifecycle
    sweep — no loop of its own). Qualifying = armed/trigger_fired/live ideas +
    open managed positions. Requalify stamps ``last_qualified_at`` and sets
    ``reason`` to the CURRENT strongest truth — it weakens when the stronger
    state ends (position closed, armed idea remains), because reason rank is
    the cap-eviction order and a stale strong rank would misorder eviction
    forever. Membership hysteresis lives entirely in the demotion clock:
    rows quiet for > FUSION_HOT_LANE_DEMOTE_SESSIONS trading days delete."""
    from db.managed_positions import list_open_positions

    now_utc = now.astimezone(timezone.utc)
    now_iso = now_utc.isoformat()
    reasons: dict[str, str] = {}

    with _connect(db_path) as conn:
        for row in conn.execute(
            "SELECT DISTINCT symbol, lifecycle_state FROM idea_outcomes "
            "WHERE lifecycle_state IN ('armed', 'trigger_fired', 'live')"
        ).fetchall():
            symbol = str(row["symbol"]).strip().upper()
            reason = _STATE_REASON[str(row["lifecycle_state"])]
            if not symbol:
                continue
            best = reasons.get(symbol)
            if best is None or _REASON_RANK[reason] < _REASON_RANK[best]:
                reasons[symbol] = reason

    try:
        for pos in list_open_positions(db_path=db_path):
            symbol = str(pos.get("symbol") or "").strip().upper()
            if symbol:
                reasons[symbol] = "position"
    except Exception:
        # Positions promotion is additive — a broken read must not stop
        # idea-driven promotion or demotion housekeeping.
        logger.exception("hot-lane sweep: open-positions read failed")

    counters = {"hot_promoted": 0, "hot_requalified": 0, "hot_demoted": 0}
    with _connect(db_path) as conn:
        for symbol, reason in reasons.items():
            cur = conn.execute(
                "UPDATE flow_hot_lane SET reason = ?, last_qualified_at = ?, "
                "updated_at = ? WHERE symbol = ?",
                (reason, now_iso, now_iso, symbol),
            )
            if cur.rowcount:
                counters["hot_requalified"] += 1
                continue
            conn.execute(
                "INSERT INTO flow_hot_lane("
                "    symbol, reason, promoted_at, last_qualified_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?)",
                (symbol, reason, now_iso, now_iso, now_iso),
            )
            counters["hot_promoted"] += 1

        for row in conn.execute(
            "SELECT symbol, last_qualified_at FROM flow_hot_lane"
        ).fetchall():
            symbol = str(row["symbol"])
            if symbol in reasons:
                continue
            last = _parse_dt(row["last_qualified_at"])
            if last is None:
                continue  # unparseable stamp: keep the row, never silent-drop
            quiet_days = _trading_days_between(
                last.astimezone(_ET).date(), now.astimezone(_ET).date()
            )
            if quiet_days > FUSION_HOT_LANE_DEMOTE_SESSIONS:
                conn.execute("DELETE FROM flow_hot_lane WHERE symbol = ?", (symbol,))
                counters["hot_demoted"] += 1
        conn.commit()
    return counters
