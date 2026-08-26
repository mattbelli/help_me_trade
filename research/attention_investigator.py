"""The bounded attention investigator (design §4.3 — the AI queue, no pollution).

One run consumes ONE queue item — a name-day that lit BOTH angles above the
display floor — reads only that name's own perception rows, makes one LLM call,
and posts a short inbox note labelled as perception. Nothing here is injected
into any other prompt; the note is honest by construction (the prompt forbids
win%/direction calls and the queue item carries the licensed facts verbatim).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Generous cap: a reasoning model's thinking tokens count against max_tokens
# (deepseek-llm-lessons — 400 starved the note to empty). The prompt, not the
# cap, keeps the note short.
MAX_NOTE_TOKENS = 2000

_SYSTEM = (
    "You are a senior market analyst writing a 3-5 sentence internal note about "
    "one stock that showed unusual activity today. You are given ONLY measured "
    "facts. HARD RULES: never state or imply a win probability, an edge, or a "
    "price direction; the range signal's direction is measured to be a coin "
    "flip. Say what lit, what the numbers mean in plain words, and what a human "
    "would want to check next (earnings? news? nothing obvious?). If the facts "
    "are thin, say so — a short honest note beats a padded one. Plain prose, "
    "no headers, no bullet points."
)


def _facts(item: dict[str, Any], db_path: Path | None) -> str:
    from db.attention import get_row
    from db.base import DEFAULT_DB_PATH
    from db.base import connect as _connect
    from db.news import recent_news

    sym, session = str(item["symbol"]), str(item["session"])
    lines = [f"Symbol: {sym} · session {session}"]
    row = get_row(sym, session, db_path=db_path)
    if row:
        lines.append(
            f"Range angle: {'lit' if row['abn_lit'] else 'not lit'}"
            + (
                f" — trailing-60-min range {row['abn_range_ratio']:.2f}x own time-of-day "
                f"norm (top {100 - row['abn_range_pctile']:.1f}% of its liquidity peers), "
                f"volume {row['abn_vol_ratio']:.1f}x, session RVOL "
                f"{row['abn_rvol_session'] or 0:.1f}x, {row['abn_repeats']} repeat fires, "
                f"base state {row['base_state']}"
                if row["abn_lit"]
                else ""
            )
        )
        if row["flow_lit"]:
            lines.append(
                f"Flow angle: {row['flow_events']} rare-sized option prints across "
                f"{row['flow_cell_days']} contract buckets — rarest at p={row['flow_min_p']:.3f} "
                f"vs this name's own history, largest single print "
                f"${(row['flow_max_print_usd'] or 0):,.0f}, calls ${(row['flow_call_prem'] or 0):,.0f} "
                f"vs puts ${(row['flow_put_prem'] or 0):,.0f}, "
                f"{(row['flow_short_dated_share'] or 0) * 100:.0f}% of prints ≤30 DTE"
            )
        lines.append(f"Liquidity quintile: Q{row['quintile']} (0-4). First lit {row['first_lit_ts']}")
    # Auxiliary color — fail-soft: the note stands on the ledger row alone.
    resolved = db_path if db_path is not None else DEFAULT_DB_PATH
    try:
        with _connect(resolved) as conn:
            evs = conn.execute(
                """
                SELECT ts, reason FROM options_flow_events
                WHERE underlying = ? AND rarity_p <= 0.05 AND ts >= ?
                ORDER BY rarity_p LIMIT 3
                """,
                (sym, session),
            ).fetchall()
        for ev in evs:
            lines.append(f"Print: {ev['reason']}")
    except Exception:
        logger.debug("investigator: flow print read failed for %s", sym, exc_info=True)
    try:
        news = recent_news(symbol=sym, limit=3, db_path=db_path)
        for n in news:
            lines.append(
                f"Headline ({str(n.get('published_at') or '')[:10]}): {n.get('headline')}"
            )
        if not news:
            lines.append("No stored news headlines for this name.")
    except Exception:
        logger.debug("investigator: news read failed for %s", sym, exc_info=True)
    return "\n".join(lines)


async def investigate_once(db_path: Path | None = None) -> dict[str, Any] | None:
    """Consume one pending item; returns the note row or None if queue empty."""
    from ai.llm.base import LLMMessage
    from ai.llm.factory import get_llm
    from config.research_engine import resolve_role_model
    from db.attention import mark_queue_item, next_pending
    from signals.notify import notify

    item = next_pending(db_path=db_path)
    if item is None:
        return None
    sym, session = str(item["symbol"]), str(item["session"])
    try:
        facts = _facts(item, db_path)
        llm = get_llm(model=resolve_role_model("reason"))
        note = (
            await llm.acomplete(
                [
                    LLMMessage(role="system", content=_SYSTEM),
                    LLMMessage(role="user", content=facts),
                ],
                max_tokens=MAX_NOTE_TOKENS,
            )
        ).strip()
        if not note:
            raise ValueError("empty investigator note")
        await notify(
            kind="attention",
            severity="info",
            title=f"Unusual today: {sym} — both angles lit",
            body=f"{note}\n\nPerception, not a forecast — no direction or win% is implied.",
            action_url=f"/watchlist/{sym}",
            group_key=f"attention_{sym}_{session}",
            data={"symbol": sym, "session": session},
            db_path=db_path if db_path is not None else _default_db(),
        )
        mark_queue_item(sym, session, status="done", note=note, db_path=db_path)
        return {"symbol": sym, "session": session, "note": note}
    except Exception:
        # Back to pending; the attempts<3 guard in next_pending retires it.
        logger.exception("attention investigator failed for %s %s", sym, session)
        mark_queue_item(sym, session, status="pending", db_path=db_path)
        return None


def _default_db() -> Path:
    from db.base import DEFAULT_DB_PATH

    return DEFAULT_DB_PATH
