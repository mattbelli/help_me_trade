"""Tests for default LLM resolution."""

from __future__ import annotations

from unittest.mock import patch

from ai.llm.config import get_default_llm, maybe_upgrade_default_llm, sync_default_llm_fields


def test_get_default_llm_prefers_default_llm_block():
    ai = {
        "provider": "openai",
        "default_llm": {"provider": "deepseek", "model": "deepseek-v4-flash"},
        "deepseek_model": "deepseek-chat",
        "openai_model": "gpt-4o",
    }
    assert get_default_llm(ai) == ("deepseek", "deepseek-v4-flash")


def test_get_default_llm_legacy_provider_fallback():
    ai = {
        "provider": "deepseek",
        "deepseek_model": "deepseek-v4-pro",
    }
    assert get_default_llm(ai) == ("deepseek", "deepseek-v4-pro")


def test_sync_default_llm_fields():
    ai = {
        "provider": "openai",
        "default_llm": {"provider": "deepseek", "model": "deepseek-v4-flash"},
        "deepseek_model": "old",
    }
    synced = sync_default_llm_fields(ai)
    assert synced["provider"] == "deepseek"
    assert synced["deepseek_model"] == "deepseek-v4-flash"


def test_maybe_upgrade_default_llm_when_empty():
    ai = {
        "provider": "deepseek",
        "default_llm": {"provider": "deepseek", "model": ""},
        "deepseek_api_key": "sk-test",
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model": "deepseek-chat",
    }

    with patch("ai.llm.models.list_chat_models") as list_models:
        list_models.return_value = {
            "models": [{"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"}],
        }
        upgraded = maybe_upgrade_default_llm(ai)

    assert upgraded["default_llm"] == {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
    }
    assert upgraded["provider"] == "deepseek"
    assert upgraded["deepseek_model"] == "deepseek-v4-flash"
