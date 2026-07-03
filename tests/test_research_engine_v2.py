"""Tests for Research Engine v2 orchestrator and specialists."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from ai.llm.base import LLMMessage
from ai.research.errors import ResearchCancelled
from ai.research.graph import run_research
from ai.research.report import build_report_from_sections, render_report_html
from ai.research.sources import _next_source_id
from ai.research.specialists import (
    SPECIALIST_ROSTER,
    SpecialistResult,
    run_specialist,
    run_specialist_follow_up,
)
from config import settings as settings_mod
from config.research_engine import EFFORT_PRESETS, resolve_effort
from db import ai_tables


def _mock_web_hit(query: str, i: int = 1) -> dict[str, Any]:
    return {
        "id": f"web_{query[:8]}_{i}",
        "type": "web",
        "title": f"Mock hit for {query[:30]}",
        "url": f"https://research.local/mock/{i}",
        "snippet": f"Snippet about {query} with factual context.",
        "content": f"Content about {query}.",
        "is_mock": True,
    }


def _distill_payload(*, with_gaps: bool = False) -> str:
    gaps = ["need more peer comparison data"] if with_gaps else []
    return json.dumps(
        {
            "section_markdown": (
                "Analysis notes with a cited claim "
                "[Mock hit](https://research.local/mock/1)."
            ),
            "key_facts": [
                {
                    "source_id": "web_test_1",
                    "fact": "Elevated volume versus the 20-day average.",
                }
            ],
            "gaps_remaining": gaps,
        }
    )


def _editor_report(*, word_count: int) -> str:
    body = " ".join(["sourced"] * word_count)
    return (
        "# Test Research Report\n\n"
        f"{body}\n\n"
        f"## Fundamentals\n\n{body}\n\n"
        "## Risks\n\n- Macro sensitivity\n\n"
        "## Next steps\n\n- Monitor support\n\n"
        "## Bottom line\n\nNeutral stance pending confirmation.\n"
    )


class ScriptedLLM:
    def __init__(
        self,
        *,
        planner_response: str | None = None,
        always_gaps: bool = False,
        editor_first_words: int = 200,
        editor_second_words: int = 200,
        editor_second_response: str | None = None,
        editor_all_garbage: bool = False,
        distill_fail_after_round: int | None = None,
    ) -> None:
        self.model: str | None = None
        self.calls: list[dict[str, Any]] = []
        self.planner_response = planner_response
        self.always_gaps = always_gaps
        self.editor_calls = 0
        self.editor_first_words = editor_first_words
        self.editor_second_words = editor_second_words
        self.editor_second_response = editor_second_response
        self.editor_all_garbage = editor_all_garbage
        self.distill_fail_after_round = distill_fail_after_round
        self.distill_round = 0

    def complete(self, messages: list[LLMMessage], *, max_tokens: int = 1024) -> str:
        combined = "\n".join(m.content for m in messages)
        lowered = combined.lower()
        self.calls.append(
            {"model": self.model, "combined": combined, "max_tokens": max_tokens}
        )

        if "trading research planner" in lowered:
            if self.planner_response is not None:
                return self.planner_response
            keys = [
                s.key
                for s in SPECIALIST_ROSTER[: resolve_effort("standard").specialist_count]
            ]
            return json.dumps(
                {
                    "report_focus": "AAPL outlook",
                    "success_criteria": ["Cover fundamentals", "Cover risks"],
                    "specialists": [
                        {
                            "key": key,
                            "directive": f"Analyze {key}",
                            "seed_queries": [f"AAPL {key} research"],
                        }
                        for key in keys
                    ],
                }
            )

        if "distill new sources" in lowered:
            self.distill_round += 1
            if (
                self.distill_fail_after_round is not None
                and self.distill_round >= self.distill_fail_after_round
            ):
                raise RuntimeError("distill failed")
            return _distill_payload(with_gaps=self.always_gaps)

        if "research query planner" in lowered:
            return json.dumps({"queries": ["AAPL follow-up query"]})

        if "adversarial research critic" in lowered:
            return json.dumps(
                {
                    "missing_angles": [],
                    "unverified_claims": [],
                    "follow_ups": [],
                }
            )

        if "chief editor" in lowered:
            self.editor_calls += 1
            if self.editor_all_garbage:
                return ""
            if self.editor_calls == 2 and self.editor_second_response is not None:
                return self.editor_second_response
            words = (
                self.editor_first_words
                if self.editor_calls == 1
                else self.editor_second_words
            )
            return _editor_report(word_count=words)

        return json.dumps({"section_markdown": "fallback", "key_facts": [], "gaps_remaining": []})


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()
    yield settings_path
    settings_mod.clear_settings_cache()


@pytest.fixture
def research_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "research.db"
    monkeypatch.setattr(ai_tables, "DEFAULT_DB_PATH", db)
    return db


@pytest.fixture
def patch_llm(monkeypatch: pytest.MonkeyPatch):
    llm = ScriptedLLM()

    def _get_llm(*, model: str | None = None):
        llm.model = model
        return llm

    for target in (
        "ai.research.engine_v2.get_llm",
        "ai.research.specialists.get_llm",
        "ai.research.report.get_llm",
    ):
        monkeypatch.setattr(target, _get_llm)
    return llm


@pytest.fixture
def patch_sources(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "ai.research.specialists.web_search",
        lambda query, **kwargs: [_mock_web_hit(query)],
    )
    monkeypatch.setattr("ai.research.specialists.fetch_url", lambda url, **kwargs: "")
    monkeypatch.setattr("ai.research.specialists.fetch_memory_sources", lambda *a, **k: [])

    def _noop_symbol(_symbol: str | None = None) -> None:
        return None

    def _noop() -> None:
        return None

    monkeypatch.setattr(
        "ai.research.specialists._DATA_SOURCE_REGISTRY",
        {
            "earnings": _noop_symbol,
            "fundamentals": _noop_symbol,
            "technicals": _noop_symbol,
            "news": _noop_symbol,
            "market_regime": _noop,
            "calibration": _noop,
            "bars": _noop_symbol,
            "portfolio": lambda _symbol=None: _noop(),
        },
    )


@pytest.fixture
def patch_memory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "ai.research.engine_v2.get_memory_service",
        lambda: type("MS", (), {"add": lambda *a, **k: None})(),
    )


def test_standard_happy_path(
    patch_llm,
    patch_sources,
    patch_memory,
    research_db,
) -> None:
    from ai.research.engine_v2 import run_research_v2
    from db.ai_tables import get_research_artifact

    result = run_research_v2(topic="AAPL outlook", symbol="AAPL", effort="standard")
    assert result["status"] == "completed"
    assert result["artifact_id"] is not None
    for key in (
        "artifact_id",
        "topic",
        "symbol",
        "depth",
        "markdown",
        "html",
        "report",
        "sources",
        "status",
    ):
        assert key in result
    assert result["depth"] == "standard"
    assert result["markdown"]
    assert result["report"]["sections"]

    artifact = get_research_artifact(int(result["artifact_id"]))
    assert artifact is not None
    assert artifact["status"] == "completed"


def test_effort_scaling_quick_and_deep(
    patch_llm,
    patch_sources,
    patch_memory,
    research_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai.research.engine_v2 import run_research_v2

    seen: list[str] = []

    def _track_run_specialist(spec, **kwargs):
        seen.append(spec.key)
        return SpecialistResult(
            key=spec.key,
            title=spec.title,
            section_markdown=f"{spec.key} section",
            key_facts=[{"source_id": "web_1", "fact": "fact"}],
            sources=[_mock_web_hit(spec.key)],
            rounds_run=1,
            status="ok",
        )

    monkeypatch.setattr("ai.research.engine_v2.run_specialist", _track_run_specialist)

    run_research_v2(topic="AAPL outlook", symbol="AAPL", effort="quick")
    assert seen == ["fundamentals", "technicals"]

    seen.clear()
    run_research_v2(topic="AAPL outlook", symbol="AAPL", effort="deep")
    assert sorted(seen) == sorted(s.key for s in SPECIALIST_ROSTER)
    assert len(seen) == 5


def test_self_stop_cap_on_exhaustive(
    patch_llm,
    patch_sources,
    patch_memory,
    research_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_llm.always_gaps = True
    rounds_seen: list[int] = []

    def _wrap(spec, **kwargs):
        result = run_specialist(spec, **kwargs)
        rounds_seen.append(result.rounds_run)
        return result

    monkeypatch.setattr("ai.research.engine_v2.run_specialist", _wrap)
    from ai.research.engine_v2 import run_research_v2

    run_research_v2(topic="AAPL outlook", symbol="AAPL", effort="exhaustive")
    cap = EFFORT_PRESETS["exhaustive"].self_stop_cap
    assert rounds_seen
    assert max(rounds_seen) == cap


def test_standard_never_exceeds_rounds_per_specialist(
    patch_llm,
    patch_sources,
    patch_memory,
    research_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_llm.always_gaps = True
    rounds_seen: list[int] = []

    def _wrap(spec, **kwargs):
        result = run_specialist(spec, **kwargs)
        rounds_seen.append(result.rounds_run)
        return result

    monkeypatch.setattr("ai.research.engine_v2.run_specialist", _wrap)
    from ai.research.engine_v2 import run_research_v2

    run_research_v2(topic="AAPL outlook", symbol="AAPL", effort="standard")
    limit = EFFORT_PRESETS["standard"].rounds_per_specialist
    assert rounds_seen
    assert max(rounds_seen) <= limit


def test_one_specialist_failure_does_not_kill_run(
    patch_llm,
    patch_sources,
    patch_memory,
    research_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai.research.engine_v2 import run_research_v2

    def _maybe_fail(spec, **kwargs):
        if spec.key == "technicals":
            return SpecialistResult(
                key=spec.key,
                title=spec.title,
                status="failed",
                error="boom",
            )
        return SpecialistResult(
            key=spec.key,
            title=spec.title,
            section_markdown=f"{spec.key} ok",
            key_facts=[{"source_id": "web_1", "fact": "fact"}],
            sources=[_mock_web_hit(spec.key)],
            status="ok",
        )

    monkeypatch.setattr("ai.research.engine_v2.run_specialist", _maybe_fail)
    result = run_research_v2(topic="AAPL outlook", symbol="AAPL", effort="quick")
    assert result["status"] == "completed"
    assert result["report"]["sections"]
    assert result["markdown"]


def test_all_specialists_fail_marks_artifact_failed(
    patch_llm,
    patch_sources,
    patch_memory,
    research_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai.research.engine_v2 import run_research_v2
    from db.ai_tables import create_research_run, get_research_artifact

    run = create_research_run(topic="Fail test", symbol="AAPL", depth="quick")
    artifact_id = int(run["id"])

    monkeypatch.setattr(
        "ai.research.engine_v2.run_specialist",
        lambda spec, **kwargs: SpecialistResult(
            key=spec.key,
            title=spec.title,
            status="failed",
            error="fail",
        ),
    )

    with pytest.raises(RuntimeError, match="All specialists failed"):
        run_research_v2(
            topic="Fail test",
            symbol="AAPL",
            effort="quick",
            artifact_id=artifact_id,
        )

    artifact = get_research_artifact(artifact_id)
    assert artifact is not None
    assert artifact["status"] == "failed"


def test_role_model_resolution(
    isolated_settings,
    patch_llm,
    patch_sources,
    patch_memory,
    research_db,
) -> None:
    settings_mod.apply_settings_patch(
        {"research": {"model_roles": {"editor": "big-model"}}}
    )
    from ai.research.engine_v2 import run_research_v2

    run_research_v2(topic="AAPL outlook", symbol="AAPL", effort="quick")

    editor_models = [
        c["model"]
        for c in patch_llm.calls
        if "chief editor" in c["combined"].lower()
    ]
    specialist_models = [
        c["model"]
        for c in patch_llm.calls
        if "distill new sources" in c["combined"].lower()
    ]
    assert "big-model" in editor_models
    assert specialist_models
    assert specialist_models[0] is None


def test_planner_garbage_uses_fallback(
    patch_llm,
    patch_sources,
    patch_memory,
    research_db,
) -> None:
    patch_llm.planner_response = "<<<not json>>>"
    from ai.research.engine_v2 import run_research_v2

    result = run_research_v2(topic="AAPL outlook", symbol="AAPL", effort="quick")
    assert result["status"] == "completed"
    assert result["report"]["sections"]


def test_editor_auto_expand(
    patch_llm,
    patch_sources,
    research_db,
) -> None:
    patch_llm.editor_first_words = 50
    patch_llm.editor_second_words = 200

    report = build_report_from_sections(
        topic="AAPL outlook",
        symbol="AAPL",
        sections=[
            {
                "key": "fundamentals",
                "title": "Fundamentals analyst",
                "section_markdown": "Analyst section with detail.",
                "status": "ok",
            }
        ],
        critic_notes={"missing_angles": [], "unverified_claims": []},
        report_focus="AAPL outlook",
        target_words=200,
        sources=[_mock_web_hit("AAPL")],
    )
    assert patch_llm.editor_calls == 2
    assert _count_words(report) >= 100


def test_graph_run_research_depth_mapping(
    isolated_settings,
    patch_llm,
    patch_sources,
    patch_memory,
    research_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ai.research.graph.run_research_v2",
        lambda **kwargs: {"depth": kwargs.get("effort"), "status": "completed", "markdown": "x"},
    )

    settings_mod.apply_settings_patch({"research": {"effort": "deep"}})
    assert run_research(topic="t", depth="exhaustive")["depth"] == "exhaustive"
    assert run_research(topic="t", depth="unknown-depth")["depth"] == "standard"
    assert run_research(topic="t", depth="")["depth"] == "deep"


def test_source_id_thread_safety() -> None:
    ids: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        local: list[str] = []
        for _ in range(25):
            local.append(_next_source_id())
        with lock:
            ids.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(ids) == 200
    assert len(set(ids)) == 200


def _count_words(report: dict[str, Any]) -> int:
    parts = [str(report.get("summary", ""))]
    for sec in report.get("sections") or []:
        parts.append(str(sec.get("body", "")))
    return len(" ".join(parts).split())


def test_render_report_html_markdown_links() -> None:
    html_out = render_report_html(
        {
            "title": "Link test",
            "summary": "See [Example](https://example.com/path?x=1&y=2)",
            "metrics": [],
            "sections": [
                {
                    "heading": "Section",
                    "body": "[Reuters](https://example.com/a?b=1&c=2)",
                    "citations": [],
                }
            ],
            "risks": [],
            "next_steps": [],
            "sources": [],
            "generated_at": "2026-07-03T00:00:00",
        }
    )
    assert 'href="https://example.com/a?b=1&amp;c=2"' in html_out
    assert "b=1&amp;c=2" in html_out
    assert "&amp;amp;" not in html_out
    assert 'href="https://example.com/path?x=1&amp;y=2"' in html_out
    assert 'target="_blank"' in html_out


def test_render_report_html_summary_link() -> None:
    html_out = render_report_html(
        {
            "title": "Summary link",
            "summary": "see [x](https://e.com/a?b=1&c=2)",
            "sections": [],
            "sources": [],
        }
    )
    assert '<a href="https://e.com/a?b=1&amp;c=2"' in html_out
    assert "[x]" not in html_out


def test_render_report_html_blocks_non_http_links() -> None:
    html_out = render_report_html(
        {
            "title": "Safe links",
            "summary": "",
            "sections": [
                {
                    "heading": "Section",
                    "body": "[x](javascript:alert(1))",
                    "citations": [],
                }
            ],
            "sources": [],
        }
    )
    section_part = html_out.split("<h2>Section</h2>")[1].split("</section>")[0]
    assert "x" in section_part
    assert "javascript:" not in html_out
    assert "<a " not in section_part


def test_specialist_cumulative_source_cap(
    patch_llm,
    patch_sources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_llm.always_gaps = True
    effort = resolve_effort("exhaustive")
    per_spec_cap = max(4, effort.source_cap // effort.specialist_count)
    hit_counter = {"n": 0}

    def _many_hits(query, **kwargs):
        hits = []
        for i in range(10):
            hit_counter["n"] += 1
            hits.append(
                {
                    "id": f"web_{hit_counter['n']}",
                    "type": "web",
                    "title": f"Hit {hit_counter['n']}",
                    "url": f"https://research.local/{hit_counter['n']}",
                    "snippet": f"Snippet {hit_counter['n']}",
                    "is_mock": True,
                }
            )
        return hits

    monkeypatch.setattr("ai.research.specialists.web_search", _many_hits)
    spec = SPECIALIST_ROSTER[0]
    result = run_specialist(
        spec,
        topic="AAPL outlook",
        symbol="AAPL",
        plan_directive="Analyze fundamentals",
        seed_queries=["AAPL fundamentals"],
        effort=effort,
        specialist_count=effort.specialist_count,
    )
    assert len(result.sources) <= per_spec_cap


def test_specialist_partial_credit_on_distill_failure(
    patch_llm,
    patch_sources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_llm.distill_fail_after_round = 2
    patch_llm.always_gaps = True
    spec = SPECIALIST_ROSTER[0]
    effort = resolve_effort("standard")
    result = run_specialist(
        spec,
        topic="AAPL outlook",
        symbol="AAPL",
        plan_directive="Analyze fundamentals",
        seed_queries=["AAPL fundamentals"],
        effort=effort,
        specialist_count=effort.specialist_count,
    )
    assert result.status == "ok"
    assert result.section_markdown
    assert result.rounds_run == 1
    assert result.error


def test_cancel_event_stops_specialist_and_fails_run(
    patch_llm,
    patch_sources,
    patch_memory,
    research_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai.research.engine_v2 import run_research_v2
    from db.ai_tables import create_research_run, get_research_artifact

    patch_llm.always_gaps = True
    cancel_event = threading.Event()
    original_run_specialist = run_specialist

    def _cancel_on_round_two(spec, **kwargs):
        emit = kwargs.get("emit")

        def _wrapped_emit(phase, payload):
            if phase == "specialist_round" and payload.get("round") == 2:
                cancel_event.set()
            if emit:
                emit(phase, payload)

        kwargs["emit"] = _wrapped_emit
        kwargs["cancel_event"] = cancel_event
        return original_run_specialist(spec, **kwargs)

    monkeypatch.setattr("ai.research.engine_v2.run_specialist", _cancel_on_round_two)
    run = create_research_run(topic="Cancel test", symbol="AAPL", depth="standard")
    artifact_id = int(run["id"])

    with pytest.raises(ResearchCancelled):
        run_research_v2(
            topic="Cancel test",
            symbol="AAPL",
            effort="standard",
            artifact_id=artifact_id,
            cancel_event=cancel_event,
        )

    artifact = get_research_artifact(artifact_id)
    assert artifact is not None
    assert artifact["status"] == "failed"


def test_follow_up_at_cap_can_add_sources(
    patch_llm,
    patch_sources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effort = resolve_effort("standard")
    per_spec_cap = max(4, effort.source_cap // effort.specialist_count)
    spec = SPECIALIST_ROSTER[0]
    existing = [
        {
            "id": f"web_existing_{i}",
            "type": "web",
            "title": f"Existing {i}",
            "url": f"https://research.local/existing/{i}",
            "snippet": f"Existing snippet {i}",
            "is_mock": True,
        }
        for i in range(per_spec_cap)
    ]
    result = SpecialistResult(
        key=spec.key,
        title=spec.title,
        section_markdown="Existing analysis",
        sources=existing,
        rounds_run=2,
        status="ok",
    )
    new_hit_counter = {"n": 0}

    def _new_hits(query, **kwargs):
        new_hit_counter["n"] += 1
        return [
            {
                "id": f"web_follow_{new_hit_counter['n']}",
                "type": "web",
                "title": f"Follow hit {new_hit_counter['n']}",
                "url": f"https://research.local/follow/{new_hit_counter['n']}",
                "snippet": "Fresh follow-up snippet",
                "is_mock": True,
            }
        ]

    monkeypatch.setattr("ai.research.specialists.web_search", _new_hits)
    updated = run_specialist_follow_up(
        result,
        spec,
        topic="AAPL outlook",
        symbol="AAPL",
        plan_directive="Analyze fundamentals",
        effort=effort,
        specialist_count=effort.specialist_count,
        queries=["AAPL follow-up risk"],
    )
    assert len(updated.sources) > len(existing)
    assert new_hit_counter["n"] >= 1


def test_expand_parse_failure_keeps_original_report(
    patch_llm,
    patch_sources,
) -> None:
    patch_llm.editor_first_words = 50
    patch_llm.editor_second_response = "This is prose, not JSON, " + ("word " * 300)

    report = build_report_from_sections(
        topic="AAPL outlook",
        symbol="AAPL",
        sections=[
            {
                "key": "fundamentals",
                "title": "Fundamentals analyst",
                "section_markdown": "Analyst section with detail.",
                "status": "ok",
            }
        ],
        critic_notes={"missing_angles": [], "unverified_claims": []},
        report_focus="AAPL outlook",
        target_words=200,
        sources=[_mock_web_hit("AAPL")],
    )
    assert report["risks"] == ["Macro sensitivity"]
    assert report["sections"]


def test_editor_sources_overwritten_from_catalog(
    patch_llm,
    patch_sources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_source = _mock_web_hit("AAPL")
    real_source["id"] = "web_real_catalog"

    class BogusEditorLLM(ScriptedLLM):
        def complete(self, messages, *, max_tokens=1024):
            if "chief editor" in "\n".join(m.content for m in messages).lower():
                return json.dumps(
                    {
                        "title": "Test",
                        "summary": "Summary text here " + ("word " * 200),
                        "metrics": [],
                        "sections": [
                            {
                                "heading": "Fundamentals",
                                "body": "Body " + ("word " * 200),
                                "citations": [],
                            }
                        ],
                        "risks": [],
                        "next_steps": [],
                        "sources": [{"id": "bogus", "title": "Fake", "url": "", "type": "web"}],
                    }
                )
            return super().complete(messages, max_tokens=max_tokens)

    bogus = BogusEditorLLM()
    monkeypatch.setattr("ai.research.report.get_llm", lambda **k: bogus)
    report = build_report_from_sections(
        topic="AAPL outlook",
        symbol="AAPL",
        sections=[
            {
                "key": "fundamentals",
                "title": "Fundamentals analyst",
                "section_markdown": "Analyst section.",
                "status": "ok",
            }
        ],
        critic_notes={"missing_angles": [], "unverified_claims": []},
        report_focus="AAPL outlook",
        target_words=200,
        sources=[real_source],
    )
    assert report["sources"]
    assert report["sources"][0]["id"] == "web_real_catalog"


def test_editor_all_garbage_falls_back_to_analyst_sections(
    patch_llm,
    patch_sources,
    patch_memory,
    research_db,
) -> None:
    patch_llm.editor_all_garbage = True
    from ai.research.engine_v2 import run_research_v2

    result = run_research_v2(topic="AAPL outlook", symbol="AAPL", effort="quick")
    assert result["status"] == "completed"
    assert result["markdown"]
    assert result["report"]["sections"]
