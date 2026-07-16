"""Hermetic tests for setup scan assessment and ranking helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ai.research.setup_scan import (
    _apply_ranker_order,
    _assess_candidate,
    _build_no_structure_reason,
    _web_queries_for_symbol,
)


def _technicals_with_structure() -> dict:
    return {
        "id": "t1",
        "type": "technicals_internal",
        "content": {
            "latest_close": 1005.0,
            "sma50": 990.0,
            "atr14": 15.0,
            "levels": {
                "support_prices": [1000.08, 950.0],
                "resistance_prices": [1095.69, 1124.55],
                "dist_to_support_pct": 0.49,
                "dist_to_resistance_pct": 9.0,
            },
        },
    }


def test_build_no_structure_reason_from_distances():
    content = {
        "levels": {
            "dist_to_support_pct": 6.2,
            "dist_to_resistance_pct": 1.4,
            "support_prices": [731.57],
            "resistance_prices": [755.52],
        }
    }
    reason = _build_no_structure_reason(content)
    assert "6.2% above nearest support 731.57" in reason
    assert "1.4% below resistance 755.52" in reason


def test_build_no_structure_reason_fallback():
    assert _build_no_structure_reason(None) == "no structure: no qualifying levels"
    assert _build_no_structure_reason({}) == "no structure: no qualifying levels"


def test_web_queries_quick_returns_one():
    assert _web_queries_for_symbol("AAPL", "quick") == ["AAPL stock catalyst news"]


def test_apply_ranker_order_preserves_unlisted():
    ideas = [
        {"symbol": "AAPL", "thesis": "a"},
        {"symbol": "MSFT", "thesis": "b"},
        {"symbol": "NVDA", "thesis": "c"},
    ]
    ordered = _apply_ranker_order(ideas, ["NVDA", "AAPL"])
    assert [i["symbol"] for i in ordered] == ["NVDA", "AAPL", "MSFT"]


@pytest.mark.asyncio
async def test_assess_candidate_no_technicals_skips_llm():
    with patch(
        "ai.research.setup_scan.fetch_technicals_source",
        return_value=None,
    ):
        result = await _assess_candidate(
            "AAPL",
            effort_name="quick",
            effort_preset=type("P", (), {"per_source_content_limit": 4000})(),
            market_regime=None,
            cancel_event=None,
        )
    assert result["has_setup"] is False
    assert result["no_structure_reason"] == "no technicals data"
    assert result["error"] is None


@pytest.mark.asyncio
async def test_assess_candidate_no_structure_skips_llm_and_fetches():
    with (
        patch(
            "ai.research.setup_scan.fetch_technicals_source",
            return_value={
                "content": {
                    "latest_close": 1070.0,
                    "sma50": 990.0,
                    "atr14": 15.0,
                    "levels": {
                        "support_prices": [1000.08],
                        "resistance_prices": [1095.69],
                        "dist_to_support_pct": 6.5,
                        "dist_to_resistance_pct": 2.4,
                    },
                }
            },
        ),
        patch("ai.research.setup_scan.fetch_news_source") as news,
        patch("ai.research.setup_scan.fetch_earnings_source") as earnings,
        patch("ai.research.setup_scan.fetch_fundamentals_source") as fundamentals,
        patch("ai.research.setup_scan._collect_web_sources") as web,
    ):
        result = await _assess_candidate(
            "AAPL",
            effort_name="quick",
            effort_preset=type("P", (), {"per_source_content_limit": 4000})(),
            market_regime=None,
            cancel_event=None,
        )
    assert result["has_setup"] is False
    assert result["no_structure_reason"].startswith("no structure:")
    news.assert_not_called()
    earnings.assert_not_called()
    fundamentals.assert_not_called()
    web.assert_not_called()


@pytest.mark.asyncio
async def test_assess_candidate_veto_deny_by_default_on_bad_json():
    with (
        patch(
            "ai.research.setup_scan.fetch_technicals_source",
            return_value=_technicals_with_structure(),
        ),
        patch("ai.research.setup_scan.fetch_news_source", return_value=None),
        patch("ai.research.setup_scan.fetch_earnings_source", return_value=None),
        patch("ai.research.setup_scan.fetch_fundamentals_source", return_value=None),
        patch("ai.research.setup_scan._collect_web_sources", return_value=[]),
        patch(
            "ai.research.setup_scan.complete_for_extraction",
            return_value="not json",
        ),
        patch("ai.research.setup_scan.parse_json_object", return_value=None),
        patch("ai.research.setup_scan.get_llm"),
    ):
        result = await _assess_candidate(
            "AAPL",
            effort_name="quick",
            effort_preset=type("P", (), {"per_source_content_limit": 4000})(),
            market_regime=None,
            cancel_event=None,
        )
    assert result["has_setup"] is False
    assert "invalid JSON" in result["error"]


@pytest.mark.asyncio
async def test_assess_candidate_accept_builds_raw_idea_from_structure():
    llm_response = {
        "decision": "accept",
        "structure_type": "pullback_to_support",
        "thesis": "Support holds with constructive tape.",
        "confidence": "medium",
        "confidence_reason": "Trend intact above SMA50.",
        "key_risks": ["Macro shock"],
        "evidence_urls": ["https://example.com/aapl"],
    }
    with (
        patch(
            "ai.research.setup_scan.fetch_technicals_source",
            return_value=_technicals_with_structure(),
        ),
        patch("ai.research.setup_scan.fetch_news_source", return_value=None),
        patch("ai.research.setup_scan.fetch_earnings_source", return_value=None),
        patch("ai.research.setup_scan.fetch_fundamentals_source", return_value=None),
        patch("ai.research.setup_scan._collect_web_sources", return_value=[]),
        patch(
            "ai.research.setup_scan.complete_for_extraction",
            return_value='{"decision":"accept"}',
        ),
        patch(
            "ai.research.setup_scan.parse_json_object",
            return_value=llm_response,
        ),
        patch("ai.research.setup_scan.get_llm"),
    ):
        result = await _assess_candidate(
            "AAPL",
            effort_name="quick",
            effort_preset=type("P", (), {"per_source_content_limit": 4000})(),
            market_regime=None,
            cancel_event=None,
        )
    assert result["has_setup"] is True
    raw = result["raw_idea"]
    assert raw["symbol"] == "AAPL"
    assert raw["entry_zone"]["low"] == "1000.08"
    assert raw["structure_type"] == "pullback_to_support"
    assert raw["entry_state"] == "ready"
    assert raw["why"]
    assert raw["thesis"] == llm_response["thesis"]


@pytest.mark.asyncio
async def test_assess_candidate_veto_records_reason():
    with (
        patch(
            "ai.research.setup_scan.fetch_technicals_source",
            return_value=_technicals_with_structure(),
        ),
        patch("ai.research.setup_scan.fetch_news_source", return_value=None),
        patch("ai.research.setup_scan.fetch_earnings_source", return_value=None),
        patch("ai.research.setup_scan.fetch_fundamentals_source", return_value=None),
        patch("ai.research.setup_scan._collect_web_sources", return_value=[]),
        patch(
            "ai.research.setup_scan.complete_for_extraction",
            return_value='{"decision":"veto"}',
        ),
        patch(
            "ai.research.setup_scan.parse_json_object",
            return_value={
                "decision": "veto",
                "veto_reason": "earnings in two days",
            },
        ),
        patch("ai.research.setup_scan.get_llm"),
    ):
        result = await _assess_candidate(
            "AAPL",
            effort_name="quick",
            effort_preset=type("P", (), {"per_source_content_limit": 4000})(),
            market_regime=None,
            cancel_event=None,
        )
    assert result["has_setup"] is False
    assert result["veto_reason"] == "earnings in two days"
