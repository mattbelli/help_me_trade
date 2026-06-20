"""System prompt includes current Eastern date grounding."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ai.chat.agent import chat_system_prompt, current_date_context


def test_system_prompt_contains_today_eastern_date() -> None:
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    prompt = chat_system_prompt()
    assert today in prompt
    assert "Current date:" in prompt
    assert "never assume a date from training" in prompt


def test_current_date_context_includes_market_session() -> None:
    ctx = current_date_context()
    assert "market session:" in ctx
    assert any(
        label in ctx
        for label in (
            "regular hours open",
            "closed",
            "pre-market",
            "post-market",
        )
    )
