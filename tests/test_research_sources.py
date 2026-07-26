"""Research source behavior: mock marking and content excerpts."""

from __future__ import annotations

from unittest.mock import patch

from ai.research.source_text import source_content_excerpt, source_for_analysis
from ai.research.sources import (
    _searxng_search,
    summarize_web_sources,
    web_search,
    web_search_status,
)


def test_web_search_status_mock_when_unconfigured() -> None:
    with patch("ai.research.sources.get_searxng_url", return_value=""):
        status = web_search_status()
    assert status["mode"] == "mock"
    assert status["uses_mock_web"] is True
    assert status["warning"]


def test_mock_web_sources_are_labeled() -> None:
    with patch("ai.research.sources.get_searxng_url", return_value=""):
        hits = web_search("AAPL earnings", limit=2)
    assert len(hits) == 2
    assert all(hit.get("is_mock") for hit in hits)
    assert all(hit.get("mock_reason") for hit in hits)
    assert all("MOCK" in hit["title"] for hit in hits)


_HEALTHY: dict = {"reachable": True, "unresponsive_engines": []}


def test_searxng_failure_does_not_fall_back_to_mock() -> None:
    with (
        patch("ai.research.sources.get_searxng_url", return_value="http://searx.test"),
        patch("ai.research.sources._searxng_search", return_value=([], _HEALTHY)),
    ):
        hits = web_search("NVDA", limit=3)
    assert len(hits) == 1
    assert not hits[0].get("is_mock")


# --- an empty result set has three causes and they are NOT interchangeable ----
#
# A SearXNG whose engines are all suspended answers HTTP 200 with results:[].
# Reporting that as "no results for this query" is what left the copilot
# silently web-blind three separate times (07-02, 07-14, 07-25).


def _no_hits(health: dict) -> dict:
    with (
        patch("ai.research.sources.get_searxng_url", return_value="http://searx.test"),
        patch("ai.research.sources._searxng_search", return_value=([], health)),
    ):
        return web_search("NVDA", limit=3)[0]


def test_engines_down_is_not_reported_as_an_empty_query() -> None:
    hit = _no_hits({"reachable": True, "unresponsive_engines": ["bing", "google"]})
    assert hit["failure_kind"] == "engines_down"
    assert hit["unresponsive_engines"] == ["bing", "google"]
    assert "instance fault" in hit["snippet"]


def test_unreachable_instance_is_its_own_failure_kind() -> None:
    hit = _no_hits({"reachable": False, "unresponsive_engines": []})
    assert hit["failure_kind"] == "unreachable"


def test_a_genuine_zero_match_query_is_not_a_failure_kind() -> None:
    hit = _no_hits(_HEALTHY)
    assert hit["failure_kind"] == "no_results"
    assert hit["unresponsive_engines"] == []


def test_searxng_search_reads_unresponsive_engines_off_the_payload() -> None:
    """The live shape: HTTP 200, results:[], every engine suspended."""
    payload = {
        "results": [],
        "unresponsive_engines": [
            ["google", "HTTP connection error"],
            ["bing", "HTTP connection error"],
        ],
    }

    class _Resp:
        def raise_for_status(self) -> None: ...

        def json(self) -> dict:
            return payload

    class _Client:
        def __init__(self, *_a, **_k) -> None: ...

        def __enter__(self):
            return self

        def __exit__(self, *_a) -> None: ...

        def get(self, *_a, **_k):
            return _Resp()

    with patch("ai.research.sources.httpx.Client", _Client):
        results, health = _searxng_search("http://searx.test", "NVDA", limit=3)
    assert results == []
    assert health == {"reachable": True, "unresponsive_engines": ["bing", "google"]}


def test_summarize_web_sources_flags_mock() -> None:
    sources = [{"type": "web", "is_mock": True}, {"type": "portfolio"}]
    with patch("ai.research.sources.get_searxng_url", return_value=""):
        summary = summarize_web_sources(sources)
    assert summary["mock_source_count"] == 1
    assert summary["warning"]


def test_render_report_html_is_well_formed() -> None:
    from ai.research.report import render_report_html

    html = render_report_html(
        {
            "title": "Test Report",
            "symbol": "AAPL",
            "summary": "Summary line",
            "metrics": [{"label": "P/E", "value": "28"}],
            "sections": [{"heading": "Overview", "body": "Body text", "citations": []}],
            "risks": ["Risk one"],
            "next_steps": ["Step one"],
            "sources": [{"id": "src_1", "title": "Source", "url": None, "type": "web"}],
            "generated_at": "2026-06-16T12:00:00",
        }
    )
    assert "</style>" in html
    assert "<body>" in html
    assert "Summary line" in html
    assert "Body text" in html


def test_research_html_for_artifact_prefers_report_json() -> None:
    from ai.research.report import research_html_for_artifact

    html = research_html_for_artifact(
        {
            "html": "<html><head><style></head><body>broken</body></html>",
            "report": {
                "title": "Fixed",
                "symbol": None,
                "summary": "From JSON",
                "metrics": [],
                "sections": [],
                "risks": [],
                "next_steps": [],
                "sources": [],
                "generated_at": "2026-06-16T12:00:00",
            },
        }
    )
    assert html is not None
    assert "</style>" in html
    assert "From JSON" in html
    assert "broken" not in html

    from ai.research.sources import normalize_research_symbol

    assert normalize_research_symbol("TSLA, SPCX") == "TSLA"
    assert normalize_research_symbol("  nvda ") == "NVDA"
    assert normalize_research_symbol("") is None


def test_deepseek_json_mode_requires_json_in_prompt() -> None:
    from ai.llm.base import LLMMessage
    from ai.llm.openai_compat import OpenAICompatibleLLM

    client = OpenAICompatibleLLM(
        provider="deepseek",
        api_key="sk-test",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
    )
    assert client._json_mode_allowed(
        [LLMMessage(role="system", content="Return JSON only with queries.")]
    )
    assert not client._json_mode_allowed(
        [LLMMessage(role="system", content="One short sentence only.")]
    )

    reasoner = OpenAICompatibleLLM(
        provider="deepseek",
        api_key="sk-test",
        base_url="https://api.deepseek.com",
        model="deepseek-reasoner",
    )
    assert not reasoner._json_mode_allowed(
        [LLMMessage(role="system", content="Return JSON only.")]
    )

    source = {
        "id": "src_1",
        "type": "web",
        "title": "Article",
        "snippet": "short",
        "content": "x" * 2000,
    }
    payload = source_for_analysis(source, max_chars=100)
    assert payload["content_excerpt"]
    assert len(payload["content_excerpt"]) == 100
    assert source_content_excerpt(source, max_chars=50) == "x" * 50
