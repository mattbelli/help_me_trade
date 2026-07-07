"""AI API routes."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse, StreamingResponse

from ai.chat.agent import ChatRunResult, finalize_chat_turn, run_chat_stream
from ai.chat.brain import (
    build_turn_context,
    finalize_turn,
    is_ephemeral_mode,
    llm_runtime_config,
    normalize_chat_mode,
    run_tool,
    should_learn_from_turn,
    tool_catalog,
)
from ai.chat.memory_extract import extract_memories_from_turn
from ai.chat.messages import last_user_text
from ai.chat.mobile_agent import run_mobile_chat_stream
from ai.chat.mobile_stream import sse
from ai.llm.config import get_default_llm
from ai.llm.factory import get_llm, get_llm_provider_name
from ai.llm.models import list_chat_models, list_default_model_options
from ai.memories import get_memory_service
from ai.reflection.service import run_reflection
from ai.research.depth import RESEARCH_DEPTHS
from ai.research.graph import VALID_RECIPES
from ai.research.jobs import DEFAULT_JOB_EVENTS_RETURN, enqueue_research, get_job, list_jobs
from ai.research.report import research_html_for_artifact
from ai.research.stream import astream_deep_research
from ai.rules.engine import evaluate_on_event
from config.ai_providers import AI_PROVIDERS, DEFAULT_AI_SETTINGS
from config.research_engine import resolve_effort
from db.ai_tables import (
    MEMORY_TYPES,
    create_rule,
    delete_research_artifact,
    delete_rule,
    get_research_artifact,
    get_rule,
    list_reflection_runs,
    list_research_artifacts,
    list_rule_events,
    list_rules,
    update_rule,
)
from db.chat_sessions import (
    CHAT_MODES,
    create_chat_session,
    delete_chat_session,
    get_chat_session_with_messages,
    list_chat_sessions,
    set_chat_session_pinned,
    update_chat_session_title,
)
from db.sqlite import get_latest_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ai"])


@router.get("/ai/providers")
def api_ai_providers():
    """Provider catalog with default base URLs and models for the settings UI."""
    provider, model = get_default_llm()
    return {
        "default_llm": {"provider": provider, "model": model},
        "providers": [
            {
                "id": pid,
                "label": meta["label"],
                "requires_key": bool(meta.get("requires_key")),
                "key_field": meta.get("key_field"),
                "base_url_field": meta.get("base_url_field"),
                "model_field": meta.get("model_field"),
                "default_base_url": meta.get("default_base_url", ""),
                "default_model": meta.get("default_model", ""),
                "key_placeholder": meta.get("key_placeholder", ""),
            }
            for pid, meta in AI_PROVIDERS.items()
        ],
        "defaults": DEFAULT_AI_SETTINGS,
    }


@router.get("/ai/models")
def api_ai_models(
    provider: str | None = Query(None),
    catalog: bool = Query(False),
):
    """Chat model catalog for the default or requested provider."""
    return list_chat_models(provider, catalog=catalog)


@router.get("/ai/models/options")
def api_ai_model_options():
    """Grouped model options for the settings default-model picker."""
    return list_default_model_options()


@router.get("/ai/research-config")
def api_research_config():
    from ai.research.sources import web_search_status
    from config.research_engine import (
        EFFORT_PRESET_NAMES,
        ROLES,
        current_research_settings,
        get_default_model_name,
    )

    provider, default_model = get_default_llm()
    if not default_model:
        default_model = get_default_model_name()

    catalog = list_chat_models(provider, catalog=True)
    known_models: list[str] = []
    for row in catalog.get("catalog_models") or catalog.get("models") or []:
        model_id = str(row.get("id", "")).strip()
        if model_id and model_id not in known_models:
            known_models.append(model_id)
    if default_model and default_model not in known_models:
        known_models.insert(0, default_model)

    return {
        **web_search_status(),
        "roles": list(ROLES),
        "effort_presets": list(EFFORT_PRESET_NAMES),
        "current": current_research_settings(),
        "default_model": default_model,
        "provider": provider,
        "known_models": known_models,
    }


@router.get("/chat/sessions")
def api_list_chat_sessions(limit: int = Query(50, ge=1, le=200)):
    return {"sessions": list_chat_sessions(limit=limit)}


@router.get("/paper-orders")
def api_list_paper_orders(
    session_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    from db.paper_orders import list_paper_order_proposals

    return {
        "orders": list_paper_order_proposals(session_id=session_id, limit=limit),
        "note": (
            "Paper proposals only — not broker open orders. "
            "Live execution requires RiskEngine (Phase 4)."
        ),
    }


@router.post("/proposals")
async def api_create_proposal(body: dict = Body(...)):
    from trading.proposals import DEFAULT_PROPOSAL_TTL_SECONDS, create_phone_proposal

    max_ttl = 3600  # hard cap: never let a proposal carry stale terms for long
    ttl_raw = body.get("ttl_seconds", DEFAULT_PROPOSAL_TTL_SECONDS)
    try:
        ttl_seconds = int(ttl_raw)
    except (TypeError, ValueError):
        return JSONResponse({"error": "ttl_seconds must be an integer"}, status_code=400)
    if ttl_seconds <= 0:
        return JSONResponse({"error": "ttl_seconds must be positive"}, status_code=400)
    if ttl_seconds > max_ttl:
        return JSONResponse(
            {"error": f"ttl_seconds must be <= {max_ttl}"}, status_code=400
        )

    session_id = body.get("session_id") or body.get("sessionId")
    if session_id is not None:
        session_id = str(session_id).strip() or None

    order_args = {
        k: v
        for k, v in body.items()
        if k not in ("ttl_seconds", "session_id", "sessionId")
    }
    if not order_args.get("symbol") and not order_args.get("option_underlying"):
        return JSONResponse({"error": "symbol is required"}, status_code=400)

    result = await create_phone_proposal(
        order_args,
        session_id=session_id,
        ttl_seconds=ttl_seconds,
    )
    if "error" in result:
        return JSONResponse({"error": result["error"]}, status_code=400)

    return {
        "id": result["id"],
        "summary": result.get("summary") or result["proposal"].get("summary"),
        "expires_at": result.get("expires_at"),
        "payload_hash": result.get("payload_hash"),
        "status": result["status"],
    }


@router.get("/proposals/pending")
def api_list_pending_proposals():
    from db.paper_orders import list_pending_proposals

    return {"proposals": list_pending_proposals()}


@router.get("/proposals/missed")
def api_list_missed_proposals(
    since: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    from db.paper_orders import list_paper_order_proposals

    return {
        "proposals": list_paper_order_proposals(
            status="expired",
            since=since,
            limit=limit,
        )
    }


@router.get("/proposals/score")
def api_proposal_score(
    symbol: str = Query(...),
    side: str = Query("buy"),
):
    try:
        from signals.decision_scorer import score_symbol

        score = score_symbol(symbol.strip(), side=side)
        if score is None:
            return {"score": None}
        return score
    except Exception:
        logger.exception("Proposal score lookup failed for %s", symbol)
        return {"score": None}


@router.get("/proposals/{proposal_id}")
def api_get_proposal(proposal_id: str):
    from db.paper_orders import get_proposal

    row = get_proposal(proposal_id.strip())
    if row is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return row


@router.post("/proposals/{proposal_id}/approve")
async def api_approve_proposal(proposal_id: str, body: dict | None = Body(None)):
    from trading.proposals import approve_proposal

    # payload_hash is REQUIRED: it binds the approval to the exact terms the
    # phone was shown (integrity / anti-confused-deputy). The client gets it from
    # POST /proposals and GET /proposals/pending.
    if not isinstance(body, dict):
        return JSONResponse(
            {"error": "payload_hash is required"}, status_code=400
        )
    raw_hash = body.get("payload_hash")
    expected_hash = str(raw_hash).strip() if raw_hash is not None else ""
    if not expected_hash:
        return JSONResponse(
            {"error": "payload_hash is required"}, status_code=400
        )

    outcome = await approve_proposal(proposal_id.strip(), expected_hash=expected_hash)
    status = outcome.get("status")

    if status == "not_found":
        return JSONResponse(outcome, status_code=404)
    if status in ("expired", "hash_mismatch", "blocked", "failed"):
        return JSONResponse(outcome, status_code=409)
    if status == "already_decided":
        return JSONResponse(outcome, status_code=409)
    return outcome


@router.post("/proposals/{proposal_id}/reject")
async def api_reject_proposal(proposal_id: str):
    from trading.proposals import reject_proposal

    outcome = await reject_proposal(proposal_id.strip())
    status = outcome.get("status")

    if status == "not_found":
        return JSONResponse(outcome, status_code=404)
    if status == "already_decided":
        return JSONResponse(outcome, status_code=409)
    return outcome


@router.post("/chat/sessions")
def api_create_chat_session(body: dict = Body(...)):
    mode = str(body.get("mode", "standard")).strip().lower()
    title = str(body.get("title", "New chat")).strip()
    if mode not in CHAT_MODES:
        return JSONResponse(
            {"error": f"mode must be one of: {', '.join(sorted(CHAT_MODES))}"},
            status_code=400,
        )
    if mode in ("quick", "temporary"):
        return JSONResponse(
            {"error": "Use temporary chat without creating a session"},
            status_code=400,
        )
    try:
        session = create_chat_session(mode=mode, title=title)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return session


@router.get("/chat/sessions/{session_id}")
def api_get_chat_session(session_id: str):
    session = get_chat_session_with_messages(session_id)
    if session is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return session


@router.patch("/chat/sessions/{session_id}")
def api_patch_chat_session(session_id: str, body: dict = Body(...)):
    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        return JSONResponse({"error": "title is required"}, status_code=400)
    session = update_chat_session_title(session_id, title)
    if session is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return session


@router.post("/chat/sessions/{session_id}/pin")
def api_set_chat_session_pinned(session_id: str, body: dict = Body(...)):
    pinned = body.get("pinned")
    if not isinstance(pinned, bool):
        return JSONResponse({"error": "pinned must be a boolean"}, status_code=400)
    session = set_chat_session_pinned(session_id, pinned)
    if session is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return session


@router.delete("/chat/sessions/{session_id}")
def api_delete_chat_session(session_id: str):
    if not delete_chat_session(session_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"ok": True}


@router.get("/chat/llm-runtime")
def api_chat_llm_runtime(model: str | None = Query(None)):
    """LLM credentials for the Next.js chat route (server-to-server only)."""
    return llm_runtime_config(model=model)


@router.get("/chat/tools")
def api_chat_tools():
    return {"tools": tool_catalog()}


@router.post("/chat/context")
def api_chat_context(body: dict = Body(...)):
    user_text = str(body.get("userText") or body.get("user_text") or "").strip()
    if not user_text:
        return JSONResponse({"error": "userText is required"}, status_code=400)
    inject_memories = bool(
        body.get("injectMemories") or body.get("inject_memories")
    )
    inject_journal = bool(
        body.get("injectJournal") or body.get("inject_journal")
    )
    return build_turn_context(
        user_text,
        inject_memories=inject_memories,
        inject_journal=inject_journal,
    )


@router.post("/chat/tools/execute")
async def api_chat_tool_execute(body: dict = Body(...)):
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    arguments = body.get("arguments")
    if arguments is not None and not isinstance(arguments, dict):
        return JSONResponse({"error": "arguments must be an object"}, status_code=400)
    try:
        return {"result": await run_tool(name, arguments)}
    except Exception as exc:
        logger.exception("Tool execute failed: %s", name)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/chat/finalize")
def api_chat_finalize(body: dict = Body(...)):
    messages = body.get("messages")
    if not isinstance(messages, list):
        return JSONResponse({"error": "messages must be a list"}, status_code=400)
    assistant_text = str(body.get("assistantText") or body.get("assistant_text") or "")
    assistant_message = body.get("assistantMessage") or body.get("assistant_message")
    if assistant_message is not None and not isinstance(assistant_message, dict):
        return JSONResponse({"error": "assistantMessage must be an object"}, status_code=400)
    session_id = body.get("sessionId") or body.get("session_id")
    mode = str(body.get("mode", "temporary")).strip().lower()
    model = body.get("model")
    chat_model = str(model).strip() if model else None
    if session_id is not None:
        session_id = str(session_id).strip()
    if normalize_chat_mode(mode) == "saved" and not session_id:
        return JSONResponse(
            {"error": "sessionId is required for saved chats"},
            status_code=400,
        )
    if is_ephemeral_mode(mode):
        return {"ok": True, "memories_saved": []}
    return finalize_turn(
        messages=messages,
        assistant_text=assistant_text,
        session_id=session_id,
        mode=mode,
        model=chat_model,
        assistant_message=assistant_message,
    )


@router.post("/chat/stream")
async def api_chat_stream(body: dict = Body(...)):
    messages = body.get("messages")
    if not isinstance(messages, list):
        return JSONResponse({"error": "messages must be a list"}, status_code=400)

    session_id = body.get("sessionId") or body.get("session_id")
    mode = normalize_chat_mode(str(body.get("mode", "temporary")))
    model = body.get("model")
    chat_model = str(model).strip() if model else None
    if session_id is not None:
        session_id = str(session_id).strip()
    if mode == "saved" and not session_id:
        return JSONResponse(
            {"error": "sessionId is required for saved chats"},
            status_code=400,
        )

    run = ChatRunResult()
    user_text = last_user_text(messages)

    async def gen():
        try:
            async for event in run_mobile_chat_stream(
                messages,
                session_id=session_id,
                mode=mode,
                model=chat_model,
                result=run,
            ):
                yield sse(event)
                await asyncio.sleep(0)
        except Exception:
            # Never leave the mobile client hanging: always emit a terminal event.
            # (Can't yield from `finally`, so catch here.) Generic message — no leak.
            logging.getLogger(__name__).exception("mobile chat stream failed")
            yield sse({"type": "error", "message": "Chat stream failed."})
            yield sse(
                {"type": "done", "sessionId": session_id, "messageId": uuid.uuid4().hex}
            )
        finally:
            if user_text and run.assistant_text:
                finalize_chat_turn(
                    messages=messages,
                    user_text=user_text,
                    assistant_text=run.assistant_text,
                    session_id=session_id,
                    mode=mode,
                    model=chat_model,
                    run=run,
                    extract=should_learn_from_turn(mode=mode),
                )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat")
async def api_chat(body: dict = Body(...)):
    messages = body.get("messages")
    if not isinstance(messages, list):
        return JSONResponse({"error": "messages must be a list"}, status_code=400)

    session_id = body.get("sessionId") or body.get("session_id")
    mode = str(body.get("mode", "quick")).strip().lower()
    model = body.get("model")
    chat_model = str(model).strip() if model else None
    if mode not in CHAT_MODES:
        return JSONResponse(
            {"error": f"mode must be one of: {', '.join(sorted(CHAT_MODES))}"},
            status_code=400,
        )
    if session_id is not None:
        session_id = str(session_id).strip()
    if mode in ("standard", "private", "saved") and not session_id:
        return JSONResponse(
            {"error": "sessionId is required for saved chats"},
            status_code=400,
        )

    run = ChatRunResult()
    user_text = last_user_text(messages)

    async def event_generator():
        try:
            for chunk in run_chat_stream(
                messages,
                session_id=session_id,
                mode=mode,
                model=chat_model,
                result=run,
            ):
                yield chunk
                await asyncio.sleep(0)
        finally:
            if user_text and run.assistant_text:
                finalize_chat_turn(
                    messages=messages,
                    user_text=user_text,
                    assistant_text=run.assistant_text,
                    session_id=session_id,
                    mode=mode,
                    model=chat_model,
                    run=run,
                    extract=should_learn_from_turn(mode=mode),
                )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "x-vercel-ai-ui-message-stream": "v1",
        },
    )


@router.get("/insights/stream")
async def insights_stream():
    snapshot = get_latest_snapshot()
    if snapshot is None:
        return JSONResponse({"error": "No snapshot yet"}, status_code=503)

    async def event_generator():
        yield f"event: status\ndata: {json.dumps({'provider': get_llm_provider_name()})}\n\n"
        try:
            parts: list[str] = []
            async for chunk in _stream_insight(snapshot):
                parts.append(chunk)
                payload = json.dumps({"text": chunk})
                yield f"event: token\ndata: {payload}\n\n"
                await asyncio.sleep(0)
            final = "".join(parts)
            yield f"event: done\ndata: {json.dumps({'text': final})}\n\n"
        except Exception as exc:
            logger.exception("Insight stream failed")
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_insight(snapshot: dict):
    from ai.insights import build_insight_messages

    llm = get_llm()
    messages = build_insight_messages(snapshot)
    async for chunk in llm.astream(messages, max_tokens=300):
        yield chunk


@router.get("/memories")
def api_list_memories(
    type: str | None = Query(None, alias="type"),
    q: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    include_inactive: bool = Query(False),
):
    if type is not None and type not in MEMORY_TYPES:
        return JSONResponse(
            {
                "error": f"type must be one of: {', '.join(sorted(MEMORY_TYPES))}"
            },
            status_code=400,
        )
    service = get_memory_service()
    if q and q.strip():
        return {
            "memories": service.search(q.strip(), memory_type=type, limit=limit)
        }
    return {
        "memories": service.list(
            memory_type=type, limit=limit, include_inactive=include_inactive
        )
    }


@router.get("/memories/{memory_id}")
def api_get_memory(memory_id: int):
    record = get_memory_service().get(memory_id)
    if record is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return record


@router.post("/memories")
def api_create_memory(body: dict = Body(...)):
    memory_type = str(body.get("type", "")).strip()
    content = str(body.get("content", "")).strip()
    metadata = body.get("metadata")
    if memory_type not in MEMORY_TYPES:
        return JSONResponse(
            {
                "error": f"type must be one of: {', '.join(sorted(MEMORY_TYPES))}"
            },
            status_code=400,
        )
    if not content:
        return JSONResponse({"error": "content is required"}, status_code=400)
    if metadata is not None and not isinstance(metadata, dict):
        return JSONResponse({"error": "metadata must be an object"}, status_code=400)
    try:
        record = get_memory_service().add(
            content,
            memory_type=memory_type,
            metadata=metadata,
            source="widget_manual",
            authority="user_authored",
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return record


@router.patch("/memories/{memory_id}")
def api_patch_memory(memory_id: int, body: dict = Body(...)):
    content = body.get("content")
    memory_type = body.get("type")
    metadata = body.get("metadata")
    status = body.get("status")
    authority = body.get("authority")
    valid_until = body.get("valid_until")
    pinned_raw = body.get("pinned")
    pinned = None if pinned_raw is None else bool(pinned_raw)
    if memory_type is not None:
        memory_type = str(memory_type).strip()
        if memory_type not in MEMORY_TYPES:
            return JSONResponse(
                {
                    "error": f"type must be one of: {', '.join(sorted(MEMORY_TYPES))}"
                },
                status_code=400,
            )
    if status is not None:
        status = str(status).strip()
    if authority is not None:
        authority = str(authority).strip()
        # §6 authority model: the ONLY server-accepted transition is the
        # founder's Confirm tap — agent_inferred → user_confirmed. Nothing
        # else may raise (or rewrite) authority over HTTP.
        if authority != "user_confirmed":
            return JSONResponse(
                {"error": "authority may only be set to 'user_confirmed'"},
                status_code=400,
            )
        current = get_memory_service().get(memory_id)
        if current is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if current.get("authority") != "agent_inferred":
            return JSONResponse(
                {
                    "error": "only agent_inferred memories can be confirmed"
                },
                status_code=400,
            )
    if valid_until is not None:
        valid_until = str(valid_until).strip()
    if content is not None and not str(content).strip():
        return JSONResponse({"error": "content cannot be empty"}, status_code=400)
    if metadata is not None and not isinstance(metadata, dict):
        return JSONResponse({"error": "metadata must be an object"}, status_code=400)
    try:
        record = get_memory_service().update(
            memory_id,
            content=str(content).strip() if content is not None else None,
            memory_type=memory_type,
            metadata=metadata,
            status=status,
            pinned=pinned,
            authority=authority,
            valid_until=valid_until,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if record is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return record


@router.delete("/memories/{memory_id}")
def api_delete_memory(memory_id: int):
    if not get_memory_service().delete(memory_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"ok": True}


@router.post("/memories/{memory_id}/resolve-proposal")
def api_resolve_memory_proposal(memory_id: int, body: dict = Body(...)):
    """Founder resolution of a pending supersede proposal (M-1 review lane).

    ``action='accept'`` retires the targeted protected row and promotes this
    proposal to ``user_confirmed`` (this endpoint IS the founder Confirm gesture,
    so the mint is legal); ``action='reject'`` archives the proposal and leaves
    the protected row untouched. Mutating route — the security middleware already
    gates it behind the founder API token, so a non-founder request 401s before
    reaching here."""
    action = str(body.get("action", "")).strip().lower()
    if action not in ("accept", "reject"):
        return JSONResponse(
            {"error": "action must be 'accept' or 'reject'"}, status_code=400
        )
    record = get_memory_service().resolve_proposal(memory_id, action=action)
    if record is None:
        return JSONResponse(
            {"error": "no pending proposal for this memory"}, status_code=404
        )
    return record


@router.post("/memory/extract")
def api_memory_extract(body: dict = Body(...)):
    """Extract durable memories from a finished chat turn; return saved records.

    Called by the Next.js chat client after a saved-mode turn so the UI can show
    a "Learned: …" chip. Single extraction path for the OpenAI-compatible route.
    """
    messages = body.get("messages")
    assistant_text = str(
        body.get("assistantText") or body.get("assistant_text") or ""
    ).strip()
    session_id = body.get("sessionId") or body.get("session_id")
    if session_id is not None:
        session_id = str(session_id).strip()
    source_message_id = body.get("sourceMessageId") or body.get(
        "source_message_id"
    )
    if source_message_id is not None:
        source_message_id = str(source_message_id).strip() or None
    model = body.get("model")
    chat_model = str(model).strip() if model else None

    if isinstance(messages, list):
        user_text = last_user_text(messages)
    else:
        user_text = str(
            body.get("userText") or body.get("user_text") or ""
        ).strip()

    if not user_text or not assistant_text:
        return {"memories": []}

    memories = extract_memories_from_turn(
        user_text=user_text,
        assistant_text=assistant_text,
        session_id=session_id or "chat",
        model=chat_model,
        source_message_id=source_message_id,
    )
    return {"memories": memories}


@router.get("/memory/session/{session_id}")
def api_list_session_chat_memories(session_id: str):
    """List chat-extracted memories for a session (for chip rehydration)."""
    sid = session_id.strip()
    if not sid:
        return JSONResponse({"error": "session_id is required"}, status_code=400)

    service = get_memory_service()
    out: list[dict] = []
    for record in service.list(limit=200):
        meta = record.get("metadata") or {}
        if meta.get("source") != "chat":
            continue
        if meta.get("session_id") != sid:
            continue
        source_message_id = meta.get("source_message_id")
        out.append({**record, "source_message_id": source_message_id})

    return {"memories": out}


def _parse_research_symbols(raw: object) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    symbols: list[str] = []
    for item in raw:
        sym = str(item).strip().upper()
        if sym:
            symbols.append(sym)
    return symbols[:50]


def _parse_research_request(body: dict) -> tuple[dict[str, object] | None, str | None]:
    recipe = str(body.get("recipe", "single_stock")).strip().lower()
    if recipe not in VALID_RECIPES:
        return None, (
            f"recipe must be one of: {', '.join(sorted(VALID_RECIPES))}"
        )

    topic = str(body.get("topic", "")).strip()
    if recipe == "setup_scan":
        if not topic:
            topic = f"Setup scan — {datetime.now(UTC).strftime('%Y-%m-%d')}"
    elif not topic:
        return None, "topic is required"

    depth_raw = body.get("depth")
    if depth_raw is None or not str(depth_raw).strip():
        depth = resolve_effort(None).name
    else:
        depth = str(depth_raw).strip().lower()
    if depth not in RESEARCH_DEPTHS:
        return None, f"depth must be one of: {', '.join(sorted(RESEARCH_DEPTHS))}"

    symbol = body.get("symbol")
    sym = str(symbol).strip().upper() if symbol else None

    symbols_raw = body.get("symbols")
    if symbols_raw is not None:
        symbols = _parse_research_symbols(symbols_raw)
        if symbols is None:
            return None, "symbols must be a list of strings"
    else:
        symbols = None

    return {
        "topic": topic,
        "symbol": sym,
        "depth": depth,
        "recipe": recipe,
        "symbols": symbols,
    }, None


@router.post("/research/run")
async def api_run_research(body: dict = Body(...)):
    parsed, err = _parse_research_request(body)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    assert parsed is not None
    topic = str(parsed["topic"])
    sym = parsed.get("symbol")
    depth = str(parsed["depth"])
    recipe = str(parsed["recipe"])
    symbols = parsed.get("symbols")
    if not isinstance(symbols, list) and symbols is not None:
        symbols = None
    try:
        # Await the async enqueue directly so _run_job is scheduled on the
        # main event loop. The sync submit_research_job wrapper would run the
        # enqueue in a throwaway asyncio.run loop that tears down immediately,
        # cancelling the _run_job task (status tracking + completion notify)
        # while the detached executor thread keeps running the research.
        job_id = await enqueue_research(
            topic=topic,
            symbol=sym if isinstance(sym, str) else None,
            depth=depth,
            recipe=recipe,
            symbols=symbols if isinstance(symbols, list) else None,
        )
    except Exception as exc:
        logger.exception("Research enqueue failed")
        return JSONResponse({"error": str(exc)}, status_code=500)
    return {
        "job_id": job_id,
        "status": "queued",
        "topic": topic,
        "symbol": sym,
        "depth": depth,
        "recipe": recipe,
    }


@router.get("/research/jobs")
def api_list_research_jobs(limit: int = Query(10, ge=1, le=100)):
    return {"jobs": list_jobs(limit=limit)}


@router.get("/research/jobs/{job_id}")
def api_get_research_job(
    job_id: str,
    events: str | None = Query(None),
):
    job = get_job(job_id)
    if job is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if events == "all":
        job_events = job.events
    elif events is not None:
        try:
            event_limit = max(1, int(events))
            job_events = job.events[-event_limit:]
        except ValueError:
            job_events = job.events[-DEFAULT_JOB_EVENTS_RETURN:]
    else:
        job_events = job.events[-DEFAULT_JOB_EVENTS_RETURN:]
    phase = job_events[-1]["phase"] if job_events else job.status
    return {
        "job_id": job.id,
        "status": job.status,
        "topic": job.topic,
        "symbol": job.symbol,
        "depth": job.depth,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "artifact_id": job.artifact_id,
        "error": job.error,
        "result": job.result,
        "events": job_events,
        "phase": phase,
    }


@router.post("/research/stream")
async def api_research_stream(body: dict = Body(...)):
    parsed, err = _parse_research_request(body)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    assert parsed is not None
    topic = str(parsed["topic"])
    sym = parsed.get("symbol")
    depth = str(parsed["depth"])
    recipe = str(parsed["recipe"])
    symbols = parsed.get("symbols")
    if not isinstance(symbols, list) and symbols is not None:
        symbols = None

    async def event_generator():
        async for chunk in astream_deep_research(
            topic=topic,
            symbol=sym if isinstance(sym, str) else None,
            depth=depth,
            recipe=recipe,
            symbols=symbols if isinstance(symbols, list) else None,
        ):
            yield chunk
            await asyncio.sleep(0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/research")
def api_list_research(limit: int = Query(20, ge=1, le=100)):
    return {"artifacts": list_research_artifacts(limit=limit)}


@router.get("/research/{artifact_id}/html")
def api_get_research_html(artifact_id: int):
    artifact = get_research_artifact(artifact_id)
    if artifact is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    html = research_html_for_artifact(artifact)
    if not html:
        return JSONResponse({"error": "HTML report not available"}, status_code=404)
    from fastapi.responses import HTMLResponse

    return HTMLResponse(content=html)


@router.get("/research/{artifact_id}")
def api_get_research(artifact_id: int):
    artifact = get_research_artifact(artifact_id)
    if artifact is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return artifact


@router.delete("/research/{artifact_id}")
def api_delete_research(artifact_id: int):
    if not delete_research_artifact(artifact_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"ok": True}


@router.post("/reflection/run")
def api_run_reflection():
    try:
        result = run_reflection()
    except Exception as exc:
        logger.exception("Reflection failed")
        return JSONResponse({"error": str(exc)}, status_code=500)
    return result


@router.get("/reflection")
def api_list_reflection(limit: int = Query(20, ge=1, le=100)):
    return {"runs": list_reflection_runs(limit=limit)}


@router.get("/rules")
def api_list_rules():
    return {"rules": list_rules()}


@router.get("/rules/events")
def api_list_rule_events(limit: int = Query(50, ge=1, le=200)):
    return {"events": list_rule_events(limit=limit)}


@router.get("/rules/{rule_id}")
def api_get_rule(rule_id: str):
    rule = get_rule(rule_id)
    if rule is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return rule


@router.post("/rules")
def api_create_rule(body: dict = Body(...)):
    name = str(body.get("name", "")).strip()
    conditions = body.get("conditions")
    action = body.get("action")
    rule_id = str(body.get("id", "")).strip() or f"rule-{uuid.uuid4().hex[:8]}"
    enabled = body.get("enabled", True)
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    if not isinstance(conditions, dict):
        return JSONResponse({"error": "conditions must be an object"}, status_code=400)
    if not isinstance(action, dict):
        return JSONResponse({"error": "action must be an object"}, status_code=400)
    if get_rule(rule_id):
        return JSONResponse({"error": "Rule id already exists"}, status_code=409)
    try:
        rule = create_rule(
            rule_id=rule_id,
            name=name,
            conditions=conditions,
            action=action,
            enabled=bool(enabled),
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return rule


@router.put("/rules/{rule_id}")
def api_update_rule(rule_id: str, body: dict = Body(...)):
    patch: dict = {}
    if "name" in body:
        patch["name"] = str(body["name"]).strip()
    if "conditions" in body:
        if not isinstance(body["conditions"], dict):
            return JSONResponse({"error": "conditions must be an object"}, status_code=400)
        patch["conditions"] = body["conditions"]
    if "action" in body:
        if not isinstance(body["action"], dict):
            return JSONResponse({"error": "action must be an object"}, status_code=400)
        patch["action"] = body["action"]
    if "enabled" in body:
        patch["enabled"] = bool(body["enabled"])
    rule = update_rule(rule_id, **patch)
    if rule is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return rule


@router.delete("/rules/{rule_id}")
def api_delete_rule(rule_id: str):
    if not delete_rule(rule_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"ok": True}


@router.post("/rules/evaluate")
def api_evaluate_rules(body: dict = Body(...)):
    event = body.get("event")
    if not isinstance(event, dict):
        return JSONResponse({"error": "event must be an object"}, status_code=400)
    triggered = evaluate_on_event(event)
    return {"triggered": triggered}
