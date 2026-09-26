"""Jev (TypeSafe System One) typed reads on the brief board.

Two jobs, both perception for the specialists and the judge:
- every candidate card gets a typed tag: catalyst, materiality, whether a fact on the
  card explains the move, whether the evidence is thin, and which way the evidence leans;
- the weekly board's weekend headlines are ordered by Jev's materiality instead of the
  market-wide-first rule alone.

Jev's probabilities are model claims, not calibration: the board shows labels only,
never a number that reads as a chance (house rule; ai/llm/system_one.py).
Fail-soft everywhere: no key or a failed call leaves the board as it was.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

CONCURRENCY = 8
HEADLINE_POOL = 60  # headlines scored per weekly board (after filler/dedupe)

# Every board name is unusual by construction, so the rubric separates WHY: a 3-level
# "routine / notable / worth attention" put all 26 names of 09-25 at "worth attention"
# (1.71-1.92); these five spread the same board 0.81-3.20 (deal break top, held-only low).
MATERIALITY = (
    "Routine for this name: nothing new, or a move the facts show is already fully priced",
    "Unusual, but explained by one known event the market has already reacted to",
    "A fresh catalyst whose effect is plausibly still being priced",
    "A fresh story that several independent angles (flow, news, tape) agree on",
    "A major story for this name, likely to drive the next several sessions",
)
_MATERIALITY_LABEL = ("routine", "already priced", "fresh catalyst", "fresh + confirmed", "major story")

CATALYSTS = {
    "earnings": "An earnings report or pre-announcement",
    "guidance": "Guidance, an outlook change or an investor day",
    "m_and_a": "A merger, acquisition, divestiture or deal break",
    "analyst": "An analyst rating or price-target change",
    "product_or_contract": "A product, customer contract or partnership",
    "legal_or_regulatory": "A legal, regulatory or government action",
    "macro_or_sector": "A macro or sector move carrying the name",
    "flow_or_tape_only": "No news: options flow or the tape alone",
    "none": "Nothing in the facts explains it",
}

LEANS = {
    "bullish": "The evidence on this card mostly points up",
    "bearish": "The evidence on this card mostly points down",
    "two_sided": "The evidence is mixed or points both ways",
}

HEADLINE_MATERIALITY = (
    "Noise — no market effect",
    "Could move one stock",
    "Could move a sector or several stocks",
    "Could move the whole US market at the next session",
)


def _card_questions() -> dict[str, Any]:
    from ai.llm.system_one import choice, noul, score

    return {
        "catalyst": choice("What is the main catalyst behind this name lighting up?",
                           criteria=CATALYSTS),
        "materiality": score("How material is this name for a swing trader choosing what to "
                             "research before the next session?", criteria=list(MATERIALITY)),
        "explained": noul("Is the activity explained by a specific fact on this card (a "
                          "headline, an earnings release, a filing)?"),
        "evidence_thin": noul("Are the facts on this card too thin to support any judgment?"),
        "lean": choice("Taking all the evidence on the card together, which way does it lean?",
                       criteria=LEANS),
    }


def _headline_questions() -> dict[str, Any]:
    from ai.llm.system_one import score

    return {"materiality": score("How much could this headline move US stocks at the next "
                                 "session?", criteria=list(HEADLINE_MATERIALITY))}


def _level(value: float, labels: tuple[str, ...]) -> str:
    return labels[max(0, min(len(labels) - 1, round(value)))]


async def tag_cards(cards: list[dict[str, Any]], render: Any) -> dict[str, Any]:
    """Add card["jev"] to every card in place; returns a summary for the board."""
    from ai.llm import system_one as so

    if not so.is_configured():
        return {"error": "no typesafe_api_key configured"}
    questions = _card_questions()
    sem = asyncio.Semaphore(CONCURRENCY)
    models: set[str] = set()
    raw: dict[str, float] = {}

    async def one(card: dict[str, Any]) -> None:
        async with sem:
            try:
                resp = await so.asystem_one("\n".join(render(card)), questions)
            except Exception as exc:  # noqa: BLE001 — one card's failure never costs the board
                logger.warning("jev: card %s failed: %s", card.get("symbol"), exc)
                card["jev"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
                return
        models.add(resp.model)
        raw[card["symbol"]] = resp.score("materiality")
        card["jev"] = {
            "catalyst": resp.choice("catalyst"),
            "materiality": _level(resp.score("materiality"), _MATERIALITY_LABEL),
            "explained": resp.noul("explained") >= 0.5,
            "evidence_thin": resp.noul("evidence_thin") >= 0.5,
            "lean": resp.choice("lean"),
        }

    await asyncio.gather(*(one(c) for c in cards))
    errors = sum(1 for c in cards if "error" in (c.get("jev") or {}))
    # The order is shown, the raw scores are not (a rubric position, but it reads as a number).
    return {"tagged": len(cards) - errors, "errors": errors, "model": sorted(models),
            "order": sorted(raw, key=lambda sym: -raw[sym])}


async def order_headlines(headlines: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Most material first (ties keep the incoming order); unchanged on any failure."""
    from ai.llm import system_one as so

    if not headlines or not so.is_configured():
        return headlines[:limit]
    questions = _headline_questions()
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(h: dict[str, Any]) -> float | None:
        async with sem:
            try:
                state = f"{h.get('headline')}\nTickers: {', '.join(h.get('symbols') or []) or 'none'}"
                return (await so.asystem_one(state, questions)).score("materiality")
            except Exception as exc:  # noqa: BLE001
                logger.warning("jev: headline failed: %s", exc)
                return None

    scores = await asyncio.gather(*(one(h) for h in headlines))
    if all(s is None for s in scores):
        return headlines[:limit]
    ranked = sorted(zip(scores, range(len(headlines)), headlines, strict=True),
                    key=lambda t: (-(t[0] if t[0] is not None else -1), t[1]))
    return [{**h, "jev_materiality": _level(s, ("noise", "one stock", "a sector", "the market"))
             if s is not None else None} for s, _, h in ranked[:limit]]
