"""Tests for chat model catalog."""

from __future__ import annotations

from unittest.mock import patch

from ai.llm import models as chat_models_module


def test_list_chat_models_empty_without_api_key(monkeypatch):
    monkeypatch.setattr(
        chat_models_module,
        "get_resolved_ai_settings",
        lambda: {
            "provider": "deepseek",
            "deepseek_api_key": "",
            "deepseek_base_url": "https://api.deepseek.com",
            "deepseek_model": "deepseek-v4-flash",
            "chat_enabled_models": {},
        },
    )
    monkeypatch.setattr(
        chat_models_module,
        "get_default_llm",
        lambda _ai=None: ("deepseek", "deepseek-v4-flash"),
    )

    result = chat_models_module.list_chat_models("deepseek")

    assert result["models"] == []
    assert result["source"] == "unavailable"


def test_list_chat_models_live_deepseek(monkeypatch):
    monkeypatch.setattr(
        chat_models_module,
        "get_resolved_ai_settings",
        lambda: {
            "provider": "deepseek",
            "deepseek_api_key": "sk-test",
            "deepseek_base_url": "https://api.deepseek.com",
            "deepseek_model": "deepseek-v4-flash",
            "chat_enabled_models": {},
        },
    )
    monkeypatch.setattr(
        chat_models_module,
        "get_default_llm",
        lambda _ai=None: ("deepseek", "deepseek-v4-flash"),
    )

    def fake_fetch(provider, base_url, api_key):
        assert provider == "deepseek"
        assert api_key == "sk-test"
        return [
            {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
            {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
        ]

    monkeypatch.setattr(
        chat_models_module,
        "_fetch_openai_compatible_models",
        fake_fetch,
    )

    result = chat_models_module.list_chat_models("deepseek", catalog=True)

    ids = [m["id"] for m in result["models"]]
    assert ids == ["deepseek-v4-pro", "deepseek-v4-flash"]
    assert result["source"] == "live"
    assert "deepseek-chat" not in ids


def test_list_chat_models_picker_filter(monkeypatch):
    monkeypatch.setattr(
        chat_models_module,
        "get_resolved_ai_settings",
        lambda: {
            "provider": "deepseek",
            "deepseek_api_key": "sk-test",
            "deepseek_base_url": "https://api.deepseek.com",
            "deepseek_model": "deepseek-v4-flash",
            "chat_enabled_models": {
                "deepseek": ["deepseek-v4-flash"],
            },
        },
    )
    monkeypatch.setattr(
        chat_models_module,
        "get_default_llm",
        lambda _ai=None: ("deepseek", "deepseek-v4-flash"),
    )
    monkeypatch.setattr(
        chat_models_module,
        "_fetch_openai_compatible_models",
        lambda *_args, **_kwargs: [
            {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
            {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
        ],
    )

    result = chat_models_module.list_chat_models("deepseek")

    ids = [m["id"] for m in result["models"]]
    assert ids == ["deepseek-v4-flash"]


def test_list_chat_models_mock_provider_empty(monkeypatch):
    monkeypatch.setattr(
        chat_models_module,
        "get_default_llm",
        lambda _ai=None: ("mock", "mock"),
    )

    result = chat_models_module.list_chat_models("mock")

    assert result["models"] == []
    assert result["supports_custom"] is False
    assert result["source"] == "unavailable"


@patch.object(chat_models_module.httpx, "Client")
def test_fetch_openai_compatible_models_parses_data(mock_client_cls):
    response = mock_client_cls.return_value.__enter__.return_value.get.return_value
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": [
            {"id": "deepseek-chat"},
            {"id": "deepseek-reasoner"},
            {"id": "text-embedding-3-small"},
        ]
    }

    out = chat_models_module._fetch_openai_compatible_models(
        "deepseek",
        "https://api.deepseek.com",
        "sk-test",
    )

    assert [m["id"] for m in out] == ["deepseek-chat", "deepseek-reasoner"]
