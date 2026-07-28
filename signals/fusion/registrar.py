"""Phase A2 registration seam — fusion_mech register-everything (spec §2.4).

Called by the intake job exactly once per SYMBOL-DAY, at the pinned instant
(the day's first stampable directional actor dot). The mechanical plan is
FROZEN v1 (estimand discipline): entry = anchor dot price, stop = 1×ATR(14)
from the prior close, target = 2×ATR, journal-class lane. A changed formula is
a NEW lane name, never an edit here.

COHORT NOTES (splits any pre/post analysis):
- 2026-07-22..07-28 the cadence had silently drifted to once per EPISODE
  LIFETIME (Round-3 long-lived episodes kept the boolean latch) — restored to
  the pinned symbol-day cadence 2026-07-28 via the per-day latch in intake.
- 2026-07-28 the FUSION_MIN_ATR_PCT floor started refusing noise-width plans.

⚠️ The MONOTONIC registered_market_day latch is the ONLY wall for an
already-RESOLVED symbol-day (the ledger's dedup index covers open rows only) —
any future backfill/replay tool that rewinds a bus cursor or clears the latch
will mint duplicate rows at fresh prices. Don't build one without a new wall.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from config.idea_engine import FUSION_MIN_ATR_PCT
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

    # ATR floor (2026-07-28, founder-blessed): a 1×ATR stop under
    # FUSION_MIN_ATR_PCT of price is quote noise, not a plan — measured: every
    # sub-1% row is a bond/cash ETF and the sub-2% cohort won 3.2%. Refused
    # BEFORE geometry; atr_pct is stamped on every row that does register, so
    # the floor's effect stays measurable. Cohort break: 2026-07-28.
    atr_pct = float(atr / price * 100)
    if atr_pct < FUSION_MIN_ATR_PCT:
        bump("fusion_register", "atr_floor_refused", db_path=db_path)
        logger.info(
            "fusion registrar: ATR floor refused %s (atr_pct=%.4f < %s)",
            episode.get("symbol"),
            atr_pct,
            FUSION_MIN_ATR_PCT,
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
    # 2026-07-28: also stamp the RANKING statistic (the seedless decayed thesis
    # the z dual gate runs on) — before this, the ledger carried only the legacy
    # undecayed score, so "does z rank outcomes?" was unanswerable from
    # source_ref. Registration-instant truth, never recomputed; additive keys
    # only (the legacy keys keep their exact meaning).
    thesis = score_state.get("decayed_thesis") or {}
    source_ref = {
        "kind": "fusion",
        "id": str(episode.get("id")),
        "signature": str(episode.get("signature") or score_state.get("signature") or ""),
        "groups": score_state.get("group_scores") or {},
        "score": score_state.get("score"),
        "score_at_registration": score_state.get("score"),
        "contested": bool(score_state.get("contested")),
        "market_shock": bool(episode.get("market_shock")),
        "atr_pct": round(atr_pct, 4),
        # Where the entry price came from (review S3): 'cache' = live bars
        # cache, 'print' = the tape event's own execution price, 'tape_spot' =
        # the whole-market spot cache (can be minutes old inside its fence) —
        # so "do spot-priced entries grade differently?" stays answerable.
        "price_source": str(episode.get("anchor_price_source") or "cache"),
        "decayed": {
            "z": thesis.get("z"),
            "conviction": thesis.get("conviction"),
            "bull_mass": thesis.get("bull_mass"),
            "bear_mass": thesis.get("bear_mass"),
            "gross": thesis.get("gross"),
            "contested": thesis.get("contested"),
        },
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
