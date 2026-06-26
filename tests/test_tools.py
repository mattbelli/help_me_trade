"""Tool planning behavior."""

from __future__ import annotations

from unittest.mock import patch

from ai.chat.tools import plan_tools
from ai.llm.mock import MockLLM


def test_plan_tools_does_not_start_research_on_analyze_alone() -> None:
    with patch("ai.chat.tools.get_llm", return_value=MockLLM()):
        planned = plan_tools("analyze my risk exposure")
    names = [name for name, _ in planned]
    assert "start_research" not in names
    assert "run_research" not in names


def test_plan_tools_can_start_research_on_explicit_request() -> None:
    with patch("ai.chat.tools.get_llm", return_value=MockLLM()):
        planned = plan_tools("Run a deep dive research report on NVDA")
    names = [name for name, _ in planned]
    assert "start_research" in names


def test_plan_tools_offers_propose_order_only_when_allowed() -> None:
    """propose_order is in the planner menu ONLY with allow_propose_order=True (mobile path),
    so a default plan_tools->execute_tool caller can never auto-execute a trade."""
    captured: dict[str, str] = {}

    def _capture(_llm, messages, **_kwargs):
        captured["system"] = messages[0].content
        return '{"tools": []}'

    with (
        patch("ai.chat.tools.get_llm", return_value=MockLLM()),
        patch("ai.chat.tools.complete_for_extraction", _capture),
    ):
        plan_tools("buy 1 AAPL")
        assert "propose_order" not in captured["system"]
        plan_tools("buy 1 AAPL", allow_propose_order=True)
        assert "propose_order" in captured["system"]
