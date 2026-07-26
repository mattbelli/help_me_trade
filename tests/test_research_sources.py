"""Research source behavior: mock marking and content excerpts."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from ai.research import sources
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

    with (
        patch("ai.research.sources.SEARXNG_MIN_INTERVAL_S", 0.0),
        patch("ai.research.sources.httpx.Client", _Client),
    ):
        results, health = _searxng_search("http://searx.test", "NVDA", limit=3)
    assert results == []
    assert health == {"reachable": True, "unresponsive_engines": ["bing", "google"]}


# --- outbound pacing: engines throttle by requests-per-IP, not by user agent --
#
# SearXNG already rotates a Firefox UA pool and brave still answers "Too many
# request", so the lever is volume. _collect_web_for_queries fans queries out
# with no spacing, which is what flagged this host's IP on 07-14.


def test_outbound_searches_are_paced(monkeypatch) -> None:
    slept: list[float] = []
    # monotonic() is read twice per call: once for the gap, once to re-stamp.
    ticks = iter([100.0, 100.0, 100.3, 100.3])
    monkeypatch.setattr(sources, "SEARXNG_MIN_INTERVAL_S", 1.5)
    monkeypatch.setattr(sources, "_last_search_at", 0.0)
    monkeypatch.setattr(sources.time, "sleep", slept.append)
    monkeypatch.setattr(sources.time, "monotonic", lambda: next(ticks))

    sources._pace_outbound_search()  # first search of the process — no wait
    assert slept == []
    sources._pace_outbound_search()  # 0.3s later — owes the remaining 1.2s
    assert slept == [pytest.approx(1.2)]


# --- page extraction: the char budget must reach the article, not the nav bar --
#
# Measured 2026-07-26 across 7 finance pages: the tag strip led with boilerplate
# on 7/7 and was pinned at the char cap on 7/7, so the LLM read menus.

_PAGE = """
<html><head><title>T</title><style>.x{color:red}</style></head><body>
<nav><a href="/">Skip to main content</a><a href="/login">Log In</a>
<a href="/sub">Subscribe to our newsletter</a></nav>
<article><h1>NVIDIA Announces Financial Results</h1>
<p>Record quarterly revenue of $22.1 billion, up 22% from Q3, up 265% from a year
ago, driven by Data Center demand. Full-year revenue reached $60.9 billion as
the company shipped record volumes to cloud providers and enterprises.</p>
<p>Management guided to continued growth and raised its quarterly dividend.</p>
</article>
<footer>Privacy policy. All rights reserved.</footer>
<script>tracker()</script></body></html>
"""


def test_extraction_keeps_the_article_and_drops_the_furniture() -> None:
    body = sources._extract_main_content(_PAGE)
    assert "Record quarterly revenue of $22.1 billion" in body
    assert "Skip to main content" not in body
    assert "Subscribe to our newsletter" not in body
    assert "All rights reserved" not in body


def test_tag_strip_is_the_thing_extraction_replaces() -> None:
    """Pins WHY this changed: the fallback path really does lead with the nav."""
    stripped = sources._strip_tags(_PAGE)
    assert stripped.index("Skip to main content") < stripped.index("Record quarterly")
    assert "tracker()" not in stripped  # <script> still goes


def test_fetch_url_falls_back_when_there_is_no_main_content(monkeypatch) -> None:
    """Link-list pages have no article. Junk beats nothing when the caller
    asked for page context, so the tag strip stays as the floor."""
    monkeypatch.setattr(sources, "_is_fetch_url_safe", lambda _u: True)
    monkeypatch.setattr(sources, "_extract_main_content", lambda _h: "")

    class _Resp:
        status_code = 200
        text = _PAGE
        headers: dict = {}

        def raise_for_status(self) -> None: ...

    class _Client:
        def __init__(self, *_a, **_k) -> None: ...

        def __enter__(self):
            return self

        def __exit__(self, *_a) -> None: ...

        def get(self, *_a, **_k):
            return _Resp()

    monkeypatch.setattr(sources.httpx, "Client", _Client)
    body = sources.fetch_url("https://example.test/links")
    assert "Skip to main content" in body  # the fallback ran
    assert "Record quarterly revenue" in body


def test_missing_extractor_degrades_and_latches(monkeypatch) -> None:
    """A missing dependency must degrade to the tag strip, not raise — and the
    miss latches so every later page skips the retry."""
    monkeypatch.setattr(sources, "_trafilatura_failed", False)
    monkeypatch.setitem(sys.modules, "trafilatura", None)  # import → ImportError

    assert sources._extract_main_content(_PAGE) == ""
    assert sources._trafilatura_failed is True


def test_pacing_can_be_switched_off(monkeypatch) -> None:
    """The tunable is a real escape hatch, not decoration."""
    monkeypatch.setattr(sources, "SEARXNG_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(sources, "_last_search_at", 0.0)
    monkeypatch.setattr(
        sources.time, "sleep", lambda _s: pytest.fail("paced with the interval off")
    )
    sources._pace_outbound_search()
    sources._pace_outbound_search()


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
