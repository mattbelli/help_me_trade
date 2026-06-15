"""LLM provider factory — env LLM_PROVIDER or settings.ai.provider."""

from __future__ import annotations

import os

from ai.llm.anthropic import AnthropicLLM
from ai.llm.base import LLMClient
from ai.llm.config import get_resolved_ai_settings, provider_credentials
from ai.llm.mock import MockLLM
from ai.llm.ollama import OllamaLLM
from ai.llm.openai_compat import OpenAICompatibleLLM
from config.ai_providers import VALID_PROVIDERS

_cached: LLMClient | None = None
_cached_provider: str | None = None


def get_llm_provider_name() -> str:
    env_name = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if env_name:
        return env_name if env_name in VALID_PROVIDERS else "mock"
    settings_name = str(get_resolved_ai_settings().get("provider", "mock")).strip().lower()
    return settings_name if settings_name in VALID_PROVIDERS else "mock"


def clear_llm_cache() -> None:
    global _cached, _cached_provider
    _cached = None
    _cached_provider = None


def get_llm(*, force_refresh: bool = False) -> LLMClient:
    global _cached, _cached_provider
    if force_refresh:
        clear_llm_cache()

    provider = get_llm_provider_name()
    if _cached is not None and _cached_provider == provider:
        return _cached

    ai = get_resolved_ai_settings()
    creds = provider_credentials(provider, ai)

    if provider == "openai":
        client: LLMClient = OpenAICompatibleLLM(
            provider="openai",
            api_key=creds["api_key"],
            base_url=creds["base_url"],
            model=creds["model"],
        )
    elif provider == "deepseek":
        client = OpenAICompatibleLLM(
            provider="deepseek",
            api_key=creds["api_key"],
            base_url=creds["base_url"],
            model=creds["model"],
        )
    elif provider == "openrouter":
        client = OpenAICompatibleLLM(
            provider="openrouter",
            api_key=creds["api_key"],
            base_url=creds["base_url"],
            model=creds["model"],
            extra_headers={
                "HTTP-Referer": "https://github.com/BeerBelly69/help_me_trade",
                "X-Title": "Help Me Trade",
            },
        )
    elif provider == "anthropic":
        client = AnthropicLLM(
            api_key=creds["api_key"],
            base_url=creds["base_url"],
            model=creds["model"],
        )
    elif provider == "ollama":
        client = OllamaLLM(
            base_url=creds["base_url"],
            model=creds["model"],
        )
    else:
        client = MockLLM()

    _cached = client
    _cached_provider = provider
    return client
