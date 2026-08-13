"""Conviction rank for the options-flow list: rarity × shape.

Surfacing only — not a signal, not a win%, not a direction call. Replaces the
widget's additive ``computeScore``. Does not use ``_unusual_score`` (that
heuristic bonuses 0DTE and known side — the opposite of this prior).

    score = 10 × rarity_w × dte_w × otm_w × type_w

Rarity is ``1 - rarity_p`` (Laplace rank from ``db/flow_rarity``). Unranked
names get a conservative 0.25 so they can still surface on shape without
claiming a percentile. Shape kernels peak ~55 DTE and ~12% OTM. Clusters
almost cannot win (``type_w = 0.08``). No known-side bonus, no DTE≤7 bonus.
"""

from __future__ import annotations

import math
from typing import Any

from db.flow_rarity import canon_right, signed_otm_pct

SCALE = 10.0
UNRANKED_RARITY = 0.25

DTE_PEAK = 55.0
DTE_SIGMA = 42.0
DTE_FLOOR = 0.22
DTE_SHORT_CUT = 7
DTE_SHORT_HAIRCUT = 0.45
DTE_MISSING = 0.4

OTM_PEAK = 12.0
OTM_SIGMA = 14.0
OTM_FLOOR = 0.20
OTM_MISSING = 0.5

TYPE_SWEEP = 1.0
TYPE_BLOCK = 0.85
TYPE_AGGREGATE = 0.08
_PARENT_SWEEP = frozenset({"sweep", "split"})


def _gauss(x: float, mu: float, sig: float) -> float:
    z = (x - mu) / sig
    return math.exp(-0.5 * z * z)


def _spot(event: dict) -> float | None:
    detail = event.get("detail")
    if not isinstance(detail, dict):
        return None
    raw = detail.get("underlying_price")
    if raw is None:
        return None
    try:
        spot = float(raw)
    except (TypeError, ValueError):
        return None
    return spot if spot > 0 else None


def dte_kernel(dte: Any) -> float:
    """Peak at ~55 DTE; November (~100) still counts; 0–7 is low, not zero."""
    if dte is None:
        return DTE_MISSING
    try:
        n = int(dte)
    except (TypeError, ValueError):
        return DTE_MISSING
    w = DTE_FLOOR + (1.0 - DTE_FLOOR) * _gauss(float(n), DTE_PEAK, DTE_SIGMA)
    if n <= DTE_SHORT_CUT:
        w *= DTE_SHORT_HAIRCUT
    return w


def otm_kernel(otm_pct: Any) -> float:
    """Peak at ~12% OTM. ITM is a hedge-ish floor. Missing → neutral 0.5."""
    if otm_pct is None:
        return OTM_MISSING
    try:
        x = float(otm_pct)
    except (TypeError, ValueError):
        return OTM_MISSING
    if x != x:
        return OTM_MISSING
    if x < 0:
        return 0.18 + 0.35 * math.exp(x / 18.0)
    return OTM_FLOOR + (1.0 - OTM_FLOOR) * _gauss(x, OTM_PEAK, OTM_SIGMA)


def type_gate(event_type: Any) -> float:
    t = str(event_type or "")
    if t in _PARENT_SWEEP:
        return TYPE_SWEEP
    if t == "block":
        return TYPE_BLOCK
    return TYPE_AGGREGATE


def rarity_weight(p: Any) -> tuple[float, bool]:
    """``(1 - p, ranked)``. Unranked → conservative 0.25, not fake-rare."""
    if p is None:
        return UNRANKED_RARITY, False
    try:
        v = float(p)
    except (TypeError, ValueError):
        return UNRANKED_RARITY, False
    if v != v:
        return UNRANKED_RARITY, False
    return max(0.0, min(1.0, 1.0 - v)), True


def resolved_otm(event: dict) -> float | None:
    """Percent OTM from the column, else strike/spot. None if uncomputable."""
    raw = event.get("otm_pct")
    if raw is not None:
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = None
        else:
            if v == v:
                return v
    return signed_otm_pct(
        event.get("strike"), _spot(event), canon_right(event.get("right"))
    )


def score_event(event: dict) -> tuple[float, dict[str, Any]]:
    """Return ``(score, parts)``. Pure; does not mutate ``event``."""
    rarity_w, ranked = rarity_weight(event.get("rarity_p"))
    dte_w = dte_kernel(event.get("dte"))
    otm_w = otm_kernel(resolved_otm(event))
    type_w = type_gate(event.get("event_type"))
    parts = {
        "rarity": round(rarity_w, 4),
        "dte": round(dte_w, 4),
        "otm": round(otm_w, 4),
        "type": round(type_w, 4),
        "ranked": ranked,
    }
    score = SCALE * rarity_w * dte_w * otm_w * type_w
    return round(score, 4), parts


def attach(event: dict) -> dict:
    """Mutate ``event`` with ``conviction`` + ``conviction_parts``.

    Fills ``otm_pct`` when it is missing and strike/spot can compute it.
    Cheap, no I/O — never opens the corpus.
    """
    if event.get("otm_pct") is None:
        filled = resolved_otm(event)
        if filled is not None:
            event["otm_pct"] = filled
    score, parts = score_event(event)
    event["conviction"] = score
    event["conviction_parts"] = parts
    return event
