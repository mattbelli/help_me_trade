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
