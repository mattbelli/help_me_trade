"""Per-turn context injected into chat (memories, decision-journal track record, etc.)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ai.memories.retrieval import retrieve_relevant_memories

logger = logging.getLogger(__name__)

# Bound on how many of the agent's own past decisions we inline per turn — mirrors the
# memory-injection cap (small; do not bloat the prompt).
_MAX_JOURNAL_DECISIONS = 3
# How many recent trade events feed the user's per-turn behavioral pattern line (cheap;
# the full net-cash truth lives in the journal widget / endpoint, not the chat prompt).
_PROFILE_EVENT_LIMIT = 500


def turn_memory_context(user_text: str, *, limit: int = 5) -> list[dict[str, Any]]:
    return retrieve_relevant_memories(user_text, limit=limit)


def format_memory_context_block(memories: list[dict[str, Any]]) -> str:
    if not memories:
        return ""
    compact = [
        {
            "type": item.get("type"),
            "content": item.get("content"),
            "score": item.get("score"),
        }
        for item in memories
    ]
    return "\n\nRelevant memories for this turn:\n" + json.dumps(compact, indent=2)


def _window_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wins = losses = r_n = 0
    r_total = 0.0
    for r in rows:
        outcome = r.get("outcome")
        if outcome == "win":
            wins += 1
        elif outcome == "loss":
            losses += 1
        rm = r.get("r_multiple")
        if rm is not None:
            r_total += float(rm)
            r_n += 1
    return {
        "n": len(rows),
        "wins": wins,
        "losses": losses,
        "avg_r": round(r_total / r_n, 2) if r_n else None,
    }


def _user_profile_line(behavior: dict[str, Any]) -> str | None:
    """One-line behavioral pattern (edges/leaks) — descriptive, never P&L."""
    count = behavior.get("trade_count") or 0
    if not count:
        return None
    parts = [f"{count} trades"]
    mix = behavior.get("asset_mix") or {}
    opt_pct = mix.get("option_pct")
    if opt_pct is not None:
        share = round(opt_pct * 100)
        parts.append(f"{share}% options" if share >= 50 else f"{100 - share}% equities")
    top = (behavior.get("symbol_concentration") or {}).get("by_trade_count") or []
    if top:
        parts.append("top " + "/".join(str(t.get("symbol")) for t in top[:2]))
    ratio = (behavior.get("buy_sell_ratio") or {}).get("overall")
    if ratio is not None:
        parts.append(f"buy/sell {ratio}")
    return ", ".join(parts)


def turn_journal_context(
    user_text: str, *, decision_limit: int = _MAX_JOURNAL_DECISIONS
) -> dict[str, Any]:
    """Compact, bounded per-turn track record: the agent's recent relevant decisions
    (scoped to a ticker named in the turn, else a short recap) + a one-line user
    behavioral profile. Fail-soft — must never raise into the chat path."""
    try:
        from db.behavioral import behavioral_facts
        from db.decision_journal import list_recent
        from db.trades import list_trade_events

        recent = list_recent(limit=50)
        text_up = (user_text or "").upper()
        scope: str | None = None
        for sym in {str(r.get("symbol")) for r in recent if r.get("symbol")}:
            if sym and re.search(rf"\b{re.escape(sym)}\b", text_up):
                scope = sym
                break
        window = [r for r in recent if r.get("symbol") == scope] if scope else recent
        # real_only: the "USER's pattern" line must never blend paper-sandbox fills.
        behavior = behavioral_facts(
            list_trade_events(real_only=True, limit=_PROFILE_EVENT_LIMIT)
        )
        return {
            "scope": scope,
            "decisions": window[:decision_limit],
            "ai_stats": _window_stats(window),
            "user_profile": _user_profile_line(behavior),
        }
    except Exception:
        logger.debug("turn_journal_context failed", exc_info=True)
        return {}


def format_journal_context_block(journal: dict[str, Any]) -> str:
    """Render the track-record block — clearly separating the AGENT's own calls from the
    USER's pattern. Bounded to a few lines; empty when there's nothing to show."""
    if not journal:
        return ""
    decisions = journal.get("decisions") or []
    stats = journal.get("ai_stats") or {}
    profile = journal.get("user_profile")
    if not decisions and not profile:
        return ""

    scope = journal.get("scope")
    lines: list[str] = []
    header = "The agent's own paper calls" + (f" for {scope}" if scope else " (recent)")
    if stats.get("n"):
        tail = f", avg {stats['avg_r']:+.2f}R" if stats.get("avg_r") is not None else ""
        lines.append(
            f"{header}: {stats['n']} decisions — "
            f"{stats.get('wins', 0)}W/{stats.get('losses', 0)}L{tail}"
        )
    else:
        lines.append(f"{header}: none yet.")

    for d in decisions[:_MAX_JOURNAL_DECISIONS]:
        score = d.get("score") if isinstance(d.get("score"), dict) else {}
        win = score.get("win_prob") if score else None
        win_s = f" · win% {round(win * 100)}" if isinstance(win, (int, float)) else ""
        outcome = d.get("outcome") or "pending"
        rm = d.get("r_multiple")
        rm_s = f" · {rm:+.2f}R" if isinstance(rm, (int, float)) else ""
        lines.append(
            f"- {d.get('symbol')} {d.get('decision') or '?'}/{outcome}{win_s}{rm_s}"
        )

    if profile:
        lines.append(f"The user's trading pattern (behavioral, not P&L): {profile}.")

    return "\n\nYour track record for this turn:\n" + "\n".join(lines)
