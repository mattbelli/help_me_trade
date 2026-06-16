"""AI API routes."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse, StreamingResponse

from ai.chat.agent import ChatRunResult, finalize_chat_turn, run_chat_stream
from ai.chat.messages import last_user_text
from ai.insights import generate_insight_paragraph
from ai.llm.factory import get_llm, get_llm_provider_name
from ai.llm.models import list_chat_models
from config.ai_providers import AI_PROVIDERS, DEFAULT_AI_SETTINGS
from ai.memories import get_memory_service
from ai.reflection.service import run_reflection
from ai.research.depth import RESEARCH_DEPTHS
from ai.research.graph import run_research
from ai.research.stream import astream_deep_research
from ai.rules.engine import evaluate_on_event
from db.ai_tables import (
    MEMORY_TYPES,
    create_rule,
    delete_rule,
    get_research_artifact,
    get_rule,
    list_memories,
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
    update_chat_session_title,
)
from db.sqlite import get_latest_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ai"])


@router.get("/ai/providers")
def api_ai_providers():
    """Provider catalog with default base URLs and models for the settings UI."""
    return {
        "active_provider": get_llm_provider_name(),
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
def api_ai_models():
    """Chat model catalog for the active provider."""
    return list_chat_models()


@router.get("/ai/research-config")
def api_research_config():
    from config.research import get_searxng_url

    url = get_searxng_url()
    return {
        "searxng_url": url,
        "web_search": "searxng" if url else "mock",
    }


@router.get("/chat/sessions")
def api_list_chat_sessions(limit: int = Query(50, ge=1, le=200)):
    return {"sessions": list_chat_sessions(limit=limit)}


@router.post("/chat/sessions")
def api_create_chat_session(body: dict = Body(...)):
    mode = str(body.get("mode", "standard")).strip().lower()
    title = str(body.get("title", "New chat")).strip()
    if mode not in CHAT_MODES:
        return JSONResponse(
            {"error": f"mode must be one of: {', '.join(sorted(CHAT_MODES))}"},
            status_code=400,
        )
    if mode == "quick":
        return JSONResponse(
            {"error": "Use POST /api/chat with mode=quick for ephemeral chats"},
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


@router.delete("/chat/sessions/{session_id}")
def api_delete_chat_session(session_id: str):
    if not delete_chat_session(session_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"ok": True}


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
    if mode in ("standard", "private") and not session_id:
        return JSONResponse(
            {"error": "sessionId is required for standard and private chats"},
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
    from ai.llm.factory import get_llm

    llm = get_llm()
    messages = build_insight_messages(snapshot)
    async for chunk in llm.astream(messages, max_tokens=300):
        yield chunk


@router.get("/memories")
def api_list_memories(
    type: str | None = Query(None, alias="type"),
    q: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
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
    return {"memories": service.list(memory_type=type, limit=limit)}


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
            content, memory_type=memory_type, metadata=metadata
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return record


@router.patch("/memories/{memory_id}")
def api_patch_memory(memory_id: int, body: dict = Body(...)):
    content = body.get("content")
    memory_type = body.get("type")
    metadata = body.get("metadata")
    if memory_type is not None:
        memory_type = str(memory_type).strip()
        if memory_type not in MEMORY_TYPES:
            return JSONResponse(
                {
                    "error": f"type must be one of: {', '.join(sorted(MEMORY_TYPES))}"
                },
                status_code=400,
            )
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


@router.post("/research/run")
def api_run_research(body: dict = Body(...)):
    topic = str(body.get("topic", "")).strip()
    symbol = body.get("symbol")
    depth = str(body.get("depth", "standard")).strip().lower()
    if not topic:
        return JSONResponse({"error": "topic is required"}, status_code=400)
    if depth not in RESEARCH_DEPTHS:
        return JSONResponse(
            {"error": f"depth must be one of: {', '.join(sorted(RESEARCH_DEPTHS))}"},
            status_code=400,
        )
    sym = str(symbol).strip().upper() if symbol else None
    try:
        result = run_research(topic=topic, symbol=sym, depth=depth)
    except Exception as exc:
        logger.exception("Research run failed")
        return JSONResponse({"error": str(exc)}, status_code=500)
    return result


@router.post("/research/stream")
async def api_research_stream(body: dict = Body(...)):
    topic = str(body.get("topic", "")).strip()
    symbol = body.get("symbol")
    depth = str(body.get("depth", "standard")).strip().lower()
    if not topic:
        return JSONResponse({"error": "topic is required"}, status_code=400)
    if depth not in RESEARCH_DEPTHS:
        return JSONResponse(
            {"error": f"depth must be one of: {', '.join(sorted(RESEARCH_DEPTHS))}"},
            status_code=400,
        )
    sym = str(symbol).strip().upper() if symbol else None

    async def event_generator():
        async for chunk in astream_deep_research(topic=topic, symbol=sym, depth=depth):
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
    html = artifact.get("html")
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
