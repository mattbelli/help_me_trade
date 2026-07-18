"""Phase A2 registration seam — fusion_mech register-everything (spec §2.4).

Called by the intake job exactly once per symbol-day episode, at the pinned
instant (first stampable directional actor dot). The mechanical plan is FROZEN
v1 (estimand discipline): entry = anchor dot price, stop = 1×ATR(14) from the
prior close, target = 2×ATR, journal-class lane. A changed formula is a NEW
lane name, never an edit here.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.idea_outcomes import register_idea
from db.lane_stats import bump

logger = logging.getLogger(__name__)

_DIRECTION = {"bull": "long", "bear": "short"}
_CENTS = Decimal("0.01")


def _dec(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def register_stack(
    episode: dict[str, Any],
    dots: list[dict[str, Any]],
    score_state: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Register one fusion_mech journal row for an anchored episode."""
    direction = _DIRECTION.get(str(episode.get("anchor_direction") or ""))
    price = _dec(episode.get("anchor_price"))
    atr = _dec(episode.get("anchor_atr14"))
    if direction is None or price is None or atr is None or price <= 0 or atr <= 0:
        bump("fusion_register", "unregistrable", db_path=db_path)
        logger.warning(
            "fusion registrar: unregistrable episode %s (dir=%r price=%r atr=%r)",
            episode.get("id"),
            episode.get("anchor_direction"),
            episode.get("anchor_price"),
            episode.get("anchor_atr14"),
        )
        return

    if direction == "long":
        stop = (price - atr).quantize(_CENTS)
        target = (price + 2 * atr).quantize(_CENTS)
    else:
        stop = (price + atr).quantize(_CENTS)
        target = (price - 2 * atr).quantize(_CENTS)
    if stop <= 0 or target <= 0:
        bump("fusion_register", "unregistrable", db_path=db_path)
        logger.warning(
            "fusion registrar: degenerate plan for %s (price=%s atr=%s)",
            episode.get("symbol"),
            price,
            atr,
        )
        return

    anchor_ts = next(
        (
            str(dot.get("ts"))
            for dot in dots
            if str(dot.get("id")) == str(episode.get("anchor_dot_id"))
        ),
        None,
    ) or str(episode.get("opened_at") or "")

    # Cohort stamp (spec §2.4b): the score as it exists at the pinned instant.
    # The day-final score is separately rankable pre-first_evaluable; this one
    # is the registration-instant truth and is never recomputed.
    source_ref = {
        "kind": "fusion",
        "id": str(episode.get("id")),
        "signature": str(episode.get("signature") or score_state.get("signature") or ""),
        "groups": score_state.get("group_scores") or {},
        "score": score_state.get("score"),
        "score_at_registration": score_state.get("score"),
        "contested": bool(score_state.get("contested")),
        "market_shock": bool(episode.get("market_shock")),
    }
    idea_id = register_idea(
        origin="fusion_mech",
        signal_family="fusion",
        symbol=str(episode.get("symbol") or ""),
        direction=direction,
        entry_zone={"low": str(price), "high": str(price)},
        stop=str(stop),
        targets=[str(target)],
        plan_horizon="days",
        grading_basis="plan",
        source_ref=source_ref,
        as_of=anchor_ts or None,
        user_action="skipped",
        db_path=db_path,
    )
    if idea_id is not None:
        bump("fusion_register", "registered", db_path=db_path)
    else:
        bump("fusion_register", "deduped_or_rejected", db_path=db_path)
