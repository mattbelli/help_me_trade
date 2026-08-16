"""Fusion read + action routes: episodes, dossiers, radar, idea actions, outcome grading.

Extracted verbatim from ``api/main.py`` (2026-08-16 god-module split). Registered by
``api/main`` via ``app.include_router`` (behind SecurityMiddleware). The fusion
*jobs* (intake / ATR / levels / reason / lifecycle) live in ``api/fusion_jobs.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from api.auth import verify_mutation_token
from db.base import DEFAULT_DB_PATH, connect

logger = logging.getLogger(__name__)
router = APIRouter(tags=["fusion"])


@router.get("/api/fusion/episodes")
def api_fusion_episodes(
    day: str | None = None,
    limit: int = Query(50, ge=1, le=100),
):
    from db.fusion import list_recent_episodes

    rows = list_recent_episodes(
        market_day=day,
        limit=limit,
        db_path=DEFAULT_DB_PATH,
    )
    return {
        "episodes": rows,
        "day": day if day is not None else (rows[0]["market_day"] if rows else None),
    }


@router.get("/api/fusion/episodes/{episode_id}")
def api_fusion_episode(episode_id: str):
    from db.fusion import episode_dots

    with connect(DEFAULT_DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT id, symbol, market_day, opened_at, updated_at, anchor_dot_id,
                   anchor_price, anchor_atr14, anchor_direction,
                   registration_called, score, direction_state, contested,
                   signature, market_shock
            FROM fusion_episodes
            WHERE id = ?
            """,
            (episode_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Fusion episode not found")
    return {
        "episode": dict(row),
        "dots": episode_dots(episode_id, db_path=DEFAULT_DB_PATH),
    }


@router.get("/api/fusion/episodes/{episode_id}/dossier")
def api_fusion_episode_dossier(episode_id: str):
    """The episode's deterministic claim matrix, built on demand (~150ms store
    reads, zero LLM/network). Ephemeral — put_dossier stays REASON-lane-only."""
    from signals.dossier import build_dossier

    with connect(DEFAULT_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM fusion_episodes WHERE id = ?",
            (episode_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Fusion episode not found")
    return {"dossier": build_dossier(dict(row), db_path=DEFAULT_DB_PATH)}


def _fusion_radar_sync(sessions: int, now_iso_str: str) -> list[dict]:
    """Compute-on-read fusion radar: per symbol with evidence in the last
    ``sessions`` market-days, the v2 decayed thesis/trigger masses at ``now``.

    Read-only, no writes/caching (dot volumes are small). DTE is recovered from
    options_flow_events for option dots exactly like tools/fusion_rescore_replay
    so pre-stamp historical dots route thesis-vs-trigger correctly (newer dots
    also carry dte in their detail). Rows sort by thesis z desc (the ranking
    statistic — FUSION_SCORING_NORMALIZATION_2026-07-22), capped at 30."""
    from signals.fusion.scorer import score_decayed

    with connect(DEFAULT_DB_PATH) as conn:
        day_rows = conn.execute(
            "SELECT DISTINCT market_day FROM fusion_episodes ORDER BY market_day DESC LIMIT ?",
            (int(sessions),),
        ).fetchall()
        days = [row["market_day"] for row in day_rows]
        if not days:
            return []
        placeholders = ",".join("?" * len(days))
        dot_rows = conn.execute(
            f"""
            SELECT d.* FROM evidence_dots d
            JOIN fusion_episodes e ON e.id = d.episode_id
            WHERE e.market_day IN ({placeholders})
            ORDER BY d.symbol, d.ts, d.id
            """,
            days,
        ).fetchall()
        option_ids = [
            str(row["source_event_id"]) for row in dot_rows if row["source_store"] == "options_flow"
        ]
        dte_by_id: dict[str, int] = {}
        for start in range(0, len(option_ids), 400):
            chunk = option_ids[start : start + 400]
            ph = ",".join("?" * len(chunk))
            for row in conn.execute(
                f"SELECT id, dte FROM options_flow_events WHERE id IN ({ph})", chunk
            ).fetchall():
                if row["dte"] is not None:
                    dte_by_id[str(row["id"])] = int(row["dte"])

    by_symbol: dict[str, list[dict]] = {}
    for row in dot_rows:
        dot = dict(row)
        try:
            dot["detail"] = json.loads(dot.pop("detail_json"))
        except (TypeError, json.JSONDecodeError):
            dot["detail"] = {}
        event_id = str(dot.get("source_event_id"))
        if event_id in dte_by_id:
            dot["dte"] = dte_by_id[event_id]
        by_symbol.setdefault(str(dot["symbol"]), []).append(dot)

    from signals.fusion.attention import attention_tier
    from signals.fusion.scorer import NOISE_FLOOR

    try:
        from signals.fusion.attention import hot_lane_rows

        hot_by_symbol = {row["symbol"]: row["reason"] for row in hot_lane_rows(DEFAULT_DB_PATH)}
    except Exception:
        # A broken lane read degrades tier to poll/scan — never a radar 500.
        logger.warning("radar: hot-lane read failed", exc_info=True)
        hot_by_symbol = {}
    rows: list[dict] = []
    for symbol, dots in by_symbol.items():
        state = score_decayed(dots, now_iso_str)
        last_evidence_at = max((str(dot["ts"]) for dot in dots), default=None)
        rows.append(
            {
                "symbol": symbol,
                "tier": attention_tier(
                    hot_reason=hot_by_symbol.get(symbol),
                    thesis_gross=state["thesis"]["gross"],
                    noise_floor=NOISE_FLOOR,
                ),
                "maturity": state["maturity"],
                "thesis": {
                    "bull_mass": state["thesis"]["bull_mass"],
                    "bear_mass": state["thesis"]["bear_mass"],
                    "net": state["thesis"]["net"],
                    "conviction": state["thesis"]["conviction"],
                    "z": state["thesis"]["z"],
                    "contested": state["thesis"]["contested"],
                },
                "trigger": {
                    "bull_mass": state["trigger"]["bull_mass"],
                    "bear_mass": state["trigger"]["bear_mass"],
                },
                "last_evidence_at": last_evidence_at,
                "dot_count": len(dots),
            }
        )
    rows.sort(key=lambda row: row["thesis"]["z"], reverse=True)
    return rows[:30]


@router.get("/api/fusion/radar")
async def api_fusion_radar(sessions: int = Query(5, ge=1, le=30)):
    """Live warming radar: v2 decayed masses per recently-active symbol,
    ranked by thesis z. Read-only compute-on-read (offloaded off the loop)."""
    now_iso_str = datetime.now(timezone.utc).isoformat()
    rows = await asyncio.to_thread(_fusion_radar_sync, sessions, now_iso_str)
    return {"rows": rows, "now": now_iso_str, "sessions": sessions}


@router.post("/api/fusion/ideas/{idea_id}/action")
def api_fusion_idea_action(idea_id: int, request: Request, body: dict = Body(...)):
    """Record the founder's take/skip decision on a fusion idea (forward-only).

    Body: ``{"action": "taken"|"skipped", "skip_reason"?: <one of the frozen set>}``.
    Grading is UNAFFECTED — an idea is outcome-tracked whether taken or not
    (calibration counts everything); this only records what the founder chose."""
    verify_mutation_token(request)
    from db.idea_outcomes import set_user_action

    action = str(body.get("action") or "").strip().lower()
    skip_reason = body.get("skip_reason")
    result = set_user_action(idea_id, action, skip_reason=skip_reason)
    if result == "not_found":
        return JSONResponse({"error": "Idea not found"}, status_code=404)
    if result == "bad_action":
        return JSONResponse({"error": "action must be 'taken' or 'skipped'"}, status_code=400)
    if result == "bad_reason":
        return JSONResponse(
            {
                "error": (
                    "skip_reason must be one of already_exposed/wrong_horizon/"
                    "disliked_thesis/no_capital/timing and only with action=skipped"
                )
            },
            status_code=400,
        )
    return {
        "ok": True,
        "action": action,
        "skip_reason": skip_reason if action == "skipped" else None,
    }


@router.post("/api/ideas/outcomes/{outcome_id}/grade")
def api_grade_idea_outcome(outcome_id: int, request: Request, body: dict = Body(...)):
    """Founder-only 5b judging: grade a RESOLVED idea against the rubric.
    Re-grading overwrites (the founder can revise)."""
    verify_mutation_token(request)
    from db.idea_outcomes import grade_outcome, normalize_verdict

    try:
        verdict = normalize_verdict(body)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    result = grade_outcome(outcome_id, verdict)
    if result == "not_found":
        return JSONResponse({"error": "Idea outcome not found"}, status_code=404)
    if result == "not_gradeable":
        return JSONResponse(
            {"error": "Only resolved ideas (won/lost/expired) can be graded"},
            status_code=400,
        )
    return {"ok": True, "verdict": verdict}
