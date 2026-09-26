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

# The Jev shadow's materiality rubric. Shared with normalize_score so the recorded
# 0..1 position can never drift from the levels the model was actually asked about.
_JEV_MATERIALITY_LEVELS: tuple[str, ...] = (
    "Routine noise — nothing a human would act on",
    "Notable but explainable from the supplied facts",
    "Worth a human look today",
)


def _jev_questions() -> dict[str, Any]:
    """The shadow's typed question set, built lazily.

    Lazy so importing this module never pulls in the vendor SDK (the file already
    defers every other import to call time). Same state as the prose note, split
    into atomic judgments. Nothing here asks for a direction or a win% — the shadow
    measures MATERIALITY and EXPLICABILITY, which is what the note claims to report,
    so the two stay comparable (see db/attention_shadow.py).
    """
    from ai.llm.system_one import choice, noul, score

    return {
        "activity_unusual": noul(
            "Does the supplied evidence describe activity that is genuinely unusual "
            "for this name, relative to the norms stated for it?"
        ),
        "explained_by_supplied_fact": noul(
            "Is the activity already explained by one of the supplied facts — a "
            "supplied headline, a stated repeat-fire pattern, or the stated "
            "liquidity quintile?"
        ),
        "materiality": score(
            "How material is this name-day to a human swing trader deciding whether "
            "to spend attention on it?",
            criteria=list(_JEV_MATERIALITY_LEVELS),
        ),
        "evidence_thin": noul(
            "Are the supplied facts too thin to support any judgment about this "
            "name-day?"
        ),
        "driver": choice(
            "Which supplied fact is doing most of the work in this name-day lighting up?",
            criteria={
                "options_flow": "Rare-sized or sweep option prints are the notable feature",
                "range_and_volume": "The trailing-60-min range/volume expansion is the notable feature",
                "news_catalyst": "A supplied headline plausibly explains the activity",
                "liquidity_context": "The liquidity quintile or peer comparison is the notable feature",
                "no_supplied_explanation_fits": "None of the supplied facts explain it",
            },
        ),
        "next_check": choice(
            "What would a human most usefully check next?",
            criteria={
                "earnings": "The earnings calendar or a near-term earnings date",
                "news": "News beyond the supplied headlines",
                "peer_group": "Sector or peer names for a corroborating move",
                "nothing_obvious": "Nothing obvious — treat it as unexplained",
            },
        ),
    }


async def _jev_shadow(
    sym: str,
    session: str,
    facts: str,
    *,
    text_model_ok: bool,
    db_path: Path | None,
) -> None:
    """Best-effort typed second opinion on the SAME facts. Never raises, never delivered.

    Runs on BOTH outcomes, including the empty-note branch: that failure is exactly
    what a text model cannot answer and a typed judgment can, so those rows are the
    cleanest evidence the shape difference is real.
    """
    from db.attention_shadow import LANE, record
    from db.lane_stats import bump

    try:
        from ai.llm import system_one as so

        if not so.is_configured():
            record(
                sym,
                session,
                status="unavailable",
                detail="no typesafe_api_key configured",
                text_model_ok=text_model_ok,
                db_path=db_path,
            )
            bump(LANE, "unavailable", db_path=db_path or _default_db())
            return

        resp = await so.asystem_one(facts, _jev_questions())
        materiality = resp.answers["materiality"]
        record(
            sym,
            session,
            status="ok",
            model=resp.model,
            latency_ms=resp.latency_ms,
            input_tokens=resp.usage.get("input_tokens"),
            output_tokens=resp.usage.get("output_tokens"),
            prompt_chars=len(facts),
            text_model_ok=text_model_ok,
            answers={
                "activity_unusual": resp.noul("activity_unusual"),
                "explained_by_supplied_fact": resp.noul("explained_by_supplied_fact"),
                "materiality": resp.score("materiality"),
                "materiality_norm": so.normalize_score(
                    materiality, len(_JEV_MATERIALITY_LEVELS)
                ),
                "evidence_thin": resp.noul("evidence_thin"),
                "driver": resp.choice("driver"),
                "driver_confidence": resp.confidence("driver"),
                "next_check": resp.choice("next_check"),
            },
            db_path=db_path,
        )
        bump(LANE, "ok", db_path=db_path or _default_db())
        bump(
            LANE,
            "text_model_ok" if text_model_ok else "text_model_failed_recovered",
            db_path=db_path or _default_db(),
        )
    except Exception as exc:
        # The shadow is an experiment hanging off a delivery lane: log and move on.
        logger.warning("attention jev shadow failed for %s %s: %s", sym, session, exc)
        record(
            sym,
            session,
            status="error",
            detail=str(exc)[:400],
            text_model_ok=text_model_ok,
            db_path=db_path,
        )
        bump(LANE, "error", db_path=db_path or _default_db())


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


def _session_bounds(session: str) -> tuple[str, str]:
    """``[start, end)`` ISO day bounds for one session, for half-open ts comparisons.

    ISO date prefixes compare correctly as strings, so this is enough to fence a
    query to the session (UTC day; the pre-existing lower bound already was).
    """
    from datetime import date, timedelta

    start = str(session).strip()
    try:
        end = (date.fromisoformat(start) + timedelta(days=1)).isoformat()
    except ValueError:
        return start, "9999-12-31"
    return start, end


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
    # Everything below is bounded to the SESSION being judged. Before the replay
    # harness existed this had only a LOWER bound, so rebuilding an old session would
    # have pulled prints and headlines from LATER sessions into the state — the
    # look-ahead contamination the prereg discipline exists to prevent. On a live run
    # (session == today) the upper bound excludes nothing that exists yet.
    session_start, session_end = _session_bounds(session)
    resolved = db_path if db_path is not None else DEFAULT_DB_PATH
    try:
        with _connect(resolved) as conn:
            evs = conn.execute(
                """
                SELECT ts, reason FROM options_flow_events
                WHERE underlying = ? AND rarity_p <= 0.05
                  AND ts >= ? AND ts < ?
                ORDER BY rarity_p LIMIT 3
                """,
                (sym, session_start, session_end),
            ).fetchall()
        for ev in evs:
            lines.append(f"Print: {ev['reason']}")
    except Exception:
        logger.debug("investigator: flow print read failed for %s", sym, exc_info=True)
    try:
        news = recent_news(
            symbol=sym,
            since=session_start,
            until=session_end,
            limit=3,
            db_path=db_path,
        )
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
    facts = ""
    text_produced = False
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
        # Set before delivery: the shadow's "text model ok" flag is about whether the
        # MODEL produced a note, not whether the notification landed.
        text_produced = True
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
    finally:
        # The Jev shadow runs LAST and on BOTH outcomes, so it can never touch the
        # note, the notification, or the queue state. Skipped only when the facts
        # themselves could not be read — nothing to judge. See db/attention_shadow.py.
        if facts:
            await _jev_shadow(
                sym, session, facts, text_model_ok=text_produced, db_path=db_path
            )


def _default_db() -> Path:
    from db.base import DEFAULT_DB_PATH

    return DEFAULT_DB_PATH
