"""Odysseus-style deep research LangGraph: plan → collect → analyze → loop → synthesize."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm
from ai.memories import get_memory_service
from ai.research.depth import DEPTH_CONFIG, RESEARCH_DEPTHS
from ai.research.report import (
    build_report_from_findings,
    render_report_html,
    report_to_markdown,
)
from ai.research.sources import collect_for_query
from db.ai_tables import create_research_run, save_research_artifact, update_research_status

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, dict[str, Any]], None]


class DeepResearchState(TypedDict, total=False):
    topic: str
    symbol: str | None
    depth: str
    artifact_id: int
    iteration: int
    max_iterations: int
    max_queries: int
    queries: list[str]
    pending_queries: list[str]
    sources: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    gaps: str
    report: dict[str, Any]
    markdown: str
    html: str
    done: bool


def _emit(cb: ProgressCallback | None, phase: str, payload: dict[str, Any]) -> None:
    if cb:
        cb(phase, payload)


def _source_key(source: dict[str, Any]) -> str:
    return str(source.get("id") or source.get("title", ""))


def _merge_sources(
    existing: list[dict[str, Any]], new_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    seen = {_source_key(s) for s in existing}
    merged = list(existing)
    for item in new_items:
        key = _source_key(item)
        if key and key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def _plan_queries(state: DeepResearchState, *, cb: ProgressCallback | None) -> list[str]:
    iteration = int(state.get("iteration", 0)) + 1
    max_queries = int(state.get("max_queries", 4))
    topic = str(state.get("topic", ""))
    symbol = state.get("symbol")
    gaps = str(state.get("gaps", "")).strip()

    _emit(
        cb,
        "planning",
        {
            "iteration": iteration,
            "max_iterations": state.get("max_iterations"),
            "topic": topic,
        },
    )

    if iteration == 1 and not gaps:
        base = [topic]
        if symbol:
            base.append(f"{symbol} price action and fundamentals")
        return base[:max_queries]

    llm = get_llm()
    messages = [
        LLMMessage(
            role="system",
            content=(
                "You are a trading research planner. Return JSON only with a "
                "queries array. Each query should target a distinct angle "
                "(technicals, fundamentals, sentiment, risks). Max 3 queries."
            ),
        ),
        LLMMessage(
            role="user",
            content=json.dumps(
                {
                    "topic": topic,
                    "symbol": symbol,
                    "gaps": gaps,
                    "prior_findings": (state.get("findings") or [])[-5:],
                },
                default=str,
            ),
        ),
    ]
    raw = llm.complete(messages, max_tokens=300)
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(raw[start : end + 1])
            queries = parsed.get("queries", [])
            if isinstance(queries, list):
                cleaned = [str(q).strip() for q in queries if str(q).strip()]
                if cleaned:
                    return cleaned[:max_queries]
    except json.JSONDecodeError:
        pass
    return [gaps or topic][:max_queries]


def _extract_findings(
    sources: list[dict[str, Any]],
    *,
    topic: str,
    symbol: str | None,
) -> list[dict[str, Any]]:
    if not sources:
        return []
    llm = get_llm()
    catalog = [
        {
            "id": s.get("id"),
            "type": s.get("type"),
            "title": s.get("title"),
            "snippet": s.get("snippet"),
        }
        for s in sources[-12:]
    ]
    messages = [
        LLMMessage(
            role="system",
            content=(
                "Extract trading research findings from sources. Return JSON only "
                "with a findings array (source_id, claim, confidence). "
                "Be concise and factual."
            ),
        ),
        LLMMessage(
            role="user",
            content=json.dumps(
                {"topic": topic, "symbol": symbol, "sources": catalog},
                indent=2,
                default=str,
            )[:10000],
        ),
    ]
    raw = llm.complete(messages, max_tokens=800)
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(raw[start : end + 1])
            findings = parsed.get("findings", [])
            if isinstance(findings, list):
                return [f for f in findings if isinstance(f, dict)]
    except json.JSONDecodeError:
        pass
    return [
        {
            "source_id": s.get("id"),
            "claim": str(s.get("snippet", ""))[:200],
            "confidence": "medium",
        }
        for s in sources[-3:]
        if s.get("snippet")
    ]


def _evaluate_gaps(state: DeepResearchState) -> str:
    llm = get_llm()
    messages = [
        LLMMessage(
            role="system",
            content=(
                "Identify remaining research gaps in one short sentence, or return "
                "empty gaps if coverage is sufficient."
            ),
        ),
        LLMMessage(
            role="user",
            content=json.dumps(
                {
                    "topic": state.get("topic"),
                    "findings_count": len(state.get("findings") or []),
                    "sources_count": len(state.get("sources") or []),
                    "findings": (state.get("findings") or [])[-8:],
                },
                default=str,
            ),
        ),
    ]
    raw = llm.complete(messages, max_tokens=120)
    if "sufficient" in raw.lower() or '"gaps": ""' in raw:
        return ""
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(raw[start : end + 1])
            return str(parsed.get("gaps", "")).strip()
    except json.JSONDecodeError:
        pass
    return raw.strip()[:200]


def make_deep_research_nodes(
    *, on_progress: ProgressCallback | None = None
) -> dict[str, Any]:
    def init_node(state: DeepResearchState) -> DeepResearchState:
        depth = str(state.get("depth", "standard"))
        if depth not in RESEARCH_DEPTHS:
            depth = "standard"
        cfg = DEPTH_CONFIG[depth]
        artifact_id = state.get("artifact_id")
        if artifact_id is None:
            run = create_research_run(
                topic=str(state.get("topic", "Research")),
                symbol=state.get("symbol"),
                depth=depth,
            )
            artifact_id = int(run["id"])
        _emit(
            on_progress,
            "started",
            {"artifact_id": artifact_id, "depth": depth},
        )
        return {
            **state,
            "depth": depth,
            "artifact_id": artifact_id,
            "iteration": 0,
            "max_iterations": cfg["max_iterations"],
            "max_queries": cfg["max_queries"],
            "sources": state.get("sources") or [],
            "findings": state.get("findings") or [],
            "done": False,
        }

    def plan_node(state: DeepResearchState) -> DeepResearchState:
        queries = _plan_queries(state, cb=on_progress)
        return {**state, "pending_queries": queries, "queries": queries}

    def collect_node(state: DeepResearchState) -> DeepResearchState:
        symbol = state.get("symbol")
        sources = list(state.get("sources") or [])
        for query in state.get("pending_queries") or []:
            _emit(
                on_progress,
                "searching",
                {"query": query, "symbol": symbol},
            )
            batch = collect_for_query(query, symbol=symbol, include_web=True)
            sources = _merge_sources(sources, batch)
            _emit(
                on_progress,
                "sources",
                {"query": query, "count": len(batch), "total": len(sources)},
            )
        iteration = int(state.get("iteration", 0)) + 1
        return {**state, "sources": sources, "iteration": iteration, "pending_queries": []}

    def analyze_node(state: DeepResearchState) -> DeepResearchState:
        _emit(on_progress, "analyzing", {"sources": len(state.get("sources") or [])})
        new_findings = _extract_findings(
            state.get("sources") or [],
            topic=str(state.get("topic", "")),
            symbol=state.get("symbol"),
        )
        findings = list(state.get("findings") or [])
        seen = {json.dumps(f, sort_keys=True, default=str) for f in findings}
        for f in new_findings:
            key = json.dumps(f, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                findings.append(f)
        return {**state, "findings": findings}

    def evaluate_node(state: DeepResearchState) -> DeepResearchState:
        iteration = int(state.get("iteration", 0))
        max_iter = int(state.get("max_iterations", 1))
        gaps = ""
        if iteration < max_iter:
            gaps = _evaluate_gaps(state)
        done = iteration >= max_iter or not gaps
        _emit(
            on_progress,
            "evaluating",
            {"iteration": iteration, "max_iterations": max_iter, "done": done, "gaps": gaps},
        )
        return {**state, "gaps": gaps, "done": done}

    def synthesize_node(state: DeepResearchState) -> DeepResearchState:
        topic = str(state.get("topic", "Research"))
        symbol = state.get("symbol")
        sources = state.get("sources") or []
        findings = state.get("findings") or []
        _emit(on_progress, "writing", {"findings": len(findings)})

        report = build_report_from_findings(
            topic=topic,
            symbol=symbol,
            sources=sources,
            findings=findings,
        )
        markdown = report_to_markdown(report)
        html = render_report_html(report)
        artifact_id = int(state["artifact_id"])
        save_research_artifact(
            topic=topic,
            symbol=symbol,
            markdown=markdown,
            status="completed",
            report_json=report,
            html=html,
            sources=sources,
            depth=str(state.get("depth", "standard")),
            artifact_id=artifact_id,
        )
        summary = str(report.get("summary", markdown))[:500]
        get_memory_service().add(
            summary,
            memory_type="research",
            metadata={
                "artifact_id": artifact_id,
                "symbol": symbol,
                "depth": state.get("depth"),
            },
        )
        _emit(
            on_progress,
            "completed",
            {
                "artifact_id": artifact_id,
                "topic": topic,
                "symbol": symbol,
                "sources": len(sources),
            },
        )
        return {
            **state,
            "report": report,
            "markdown": markdown,
            "html": html,
            "done": True,
        }

    def fail_node(state: DeepResearchState) -> DeepResearchState:
        artifact_id = state.get("artifact_id")
        if artifact_id is not None:
            update_research_status(int(artifact_id), status="failed")
        _emit(on_progress, "error", {"artifact_id": artifact_id})
        return state

    return {
        "init": init_node,
        "plan": plan_node,
        "collect": collect_node,
        "analyze": analyze_node,
        "evaluate": evaluate_node,
        "synthesize": synthesize_node,
        "fail": fail_node,
    }


def _should_continue(state: DeepResearchState) -> str:
    if state.get("done"):
        return "synthesize"
    return "plan"


def build_deep_research_graph(*, on_progress: ProgressCallback | None = None):
    nodes = make_deep_research_nodes(on_progress=on_progress)
    graph = StateGraph(DeepResearchState)
    for name, fn in nodes.items():
        if name != "fail":
            graph.add_node(name, fn)
    graph.set_entry_point("init")
    graph.add_edge("init", "plan")
    graph.add_edge("plan", "collect")
    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "evaluate")
    graph.add_conditional_edges(
        "evaluate",
        _should_continue,
        {"plan": "plan", "synthesize": "synthesize"},
    )
    graph.add_edge("synthesize", END)
    return graph.compile()


def run_deep_research(
    *,
    topic: str,
    symbol: str | None = None,
    depth: str = "standard",
    artifact_id: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    if depth not in RESEARCH_DEPTHS:
        depth = "standard"
    app = build_deep_research_graph(on_progress=on_progress)
    initial: DeepResearchState = {
        "topic": topic.strip(),
        "symbol": (symbol or "").strip().upper() or None,
        "depth": depth,
    }
    if artifact_id is not None:
        initial["artifact_id"] = artifact_id
    try:
        result = app.invoke(initial)
    except Exception:
        if artifact_id is not None:
            update_research_status(artifact_id, status="failed")
        elif initial.get("artifact_id"):
            update_research_status(int(initial["artifact_id"]), status="failed")
        raise
    aid = result.get("artifact_id")
    return {
        "artifact_id": aid,
        "topic": topic,
        "symbol": symbol,
        "depth": depth,
        "markdown": result.get("markdown", ""),
        "html": result.get("html"),
        "report": result.get("report"),
        "sources": result.get("sources", []),
        "status": "completed",
    }
