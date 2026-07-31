"""AI provider metadata and defaults."""

from __future__ import annotations

from typing import Any

AI_PROVIDERS: dict[str, dict[str, Any]] = {
    "mock": {
        "label": "Mock (offline)",
        "requires_key": False,
        "fields": [],
    },
    "openai": {
        "label": "OpenAI",
        "requires_key": True,
        "key_field": "openai_api_key",
        "base_url_field": "openai_base_url",
        "model_field": "openai_model",
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "key_placeholder": "sk-…",
    },
    "anthropic": {
        "label": "Anthropic",
        "requires_key": True,
        "key_field": "anthropic_api_key",
        "base_url_field": "anthropic_base_url",
        "model_field": "anthropic_model",
        "default_base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-20250514",
        "key_placeholder": "sk-ant-…",
    },
    "deepseek": {
        "label": "DeepSeek",
        "requires_key": True,
        "key_field": "deepseek_api_key",
        "base_url_field": "deepseek_base_url",
        "model_field": "deepseek_model",
        "default_base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "key_placeholder": "sk-…",
    },
    "openrouter": {
        "label": "OpenRouter",
        "requires_key": True,
        "key_field": "openrouter_api_key",
        "base_url_field": "openrouter_base_url",
        "model_field": "openrouter_model",
        "default_base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4o",
        "key_placeholder": "sk-or-…",
    },
    "ollama": {
        "label": "Ollama (local)",
        "requires_key": False,
        "base_url_field": "ollama_base_url",
        "model_field": "ollama_model",
        "default_base_url": "http://localhost:11434",
        "default_model": "llama3.2",
    },
}

VALID_PROVIDERS = frozenset(AI_PROVIDERS.keys())

SECRET_AI_KEY_FIELDS = frozenset(
    {
        "openai_api_key",
        "anthropic_api_key",
        "deepseek_api_key",
        "openrouter_api_key",
    }
)

DEFAULT_AI_SETTINGS: dict[str, Any] = {
    "provider": "deepseek",
    "openai_api_key": "",
    "openai_base_url": AI_PROVIDERS["openai"]["default_base_url"],
    "openai_model": AI_PROVIDERS["openai"]["default_model"],
    "anthropic_api_key": "",
    "anthropic_base_url": AI_PROVIDERS["anthropic"]["default_base_url"],
    "anthropic_model": AI_PROVIDERS["anthropic"]["default_model"],
    "deepseek_api_key": "",
    "deepseek_base_url": AI_PROVIDERS["deepseek"]["default_base_url"],
    "deepseek_model": AI_PROVIDERS["deepseek"]["default_model"],
    "openrouter_api_key": "",
    "openrouter_base_url": AI_PROVIDERS["openrouter"]["default_base_url"],
    "openrouter_model": AI_PROVIDERS["openrouter"]["default_model"],
    "ollama_base_url": AI_PROVIDERS["ollama"]["default_base_url"],
    "ollama_model": AI_PROVIDERS["ollama"]["default_model"],
    # Provider id -> model ids shown in chat picker; omit or null = show all discovered.
    "chat_enabled_models": {},
    "default_llm": {"provider": "deepseek", "model": ""},
}
