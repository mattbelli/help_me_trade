"""LLM provider factory — env LLM_PROVIDER or settings.ai.provider."""

from __future__ import annotations

import os

from ai.llm.anthropic import AnthropicLLM
from ai.llm.base import LLMClient
from ai.llm.config import (
    get_default_llm,
    get_resolved_ai_settings,
    provider_config,
    provider_credentials,
)
from ai.llm.mock import MockLLM
from ai.llm.ollama import OllamaLLM
from ai.llm.openai_compat import OpenAICompatibleLLM
from config.ai_providers import VALID_PROVIDERS

_cached: LLMClient | None = None
_cached_provider: str | None = None


def get_llm_provider_name() -> str:
    provider, _ = get_default_llm()
    if provider in VALID_PROVIDERS and provider != "mock":
        return provider
    env_name = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if env_name and env_name in VALID_PROVIDERS:
        return env_name
    return provider if provider in VALID_PROVIDERS else "mock"


def clear_llm_cache() -> None:
    global _cached, _cached_provider
    _cached = None
    _cached_provider = None


def _build_llm_client(
    provider: str, creds: dict[str, str], *, model: str | None = None
) -> LLMClient:
    resolved_model = (model or creds.get("model") or "").strip()
    if provider == "openai":
        return OpenAICompatibleLLM(
            provider="openai",
            api_key=creds["api_key"],
            base_url=creds["base_url"],
            model=resolved_model,
        )
    if provider == "deepseek":
        return OpenAICompatibleLLM(
            provider="deepseek",
            api_key=creds["api_key"],
            base_url=creds["base_url"],
            model=resolved_model,
        )
    if provider == "openrouter":
        return OpenAICompatibleLLM(
            provider="openrouter",
            api_key=creds["api_key"],
            base_url=creds["base_url"],
            model=resolved_model,
            extra_headers={
                "HTTP-Referer": "https://github.com/BeerBelly69/help_me_trade",
                "X-Title": "Help Me Trade",
            },
        )
    if provider == "anthropic":
        return AnthropicLLM(
            api_key=creds["api_key"],
            base_url=creds["base_url"],
            model=resolved_model,
        )
    if provider == "lmstudio":
        return OpenAICompatibleLLM(
            provider="lmstudio",
            api_key=creds.get("api_key") or "lm-studio",  # server has no auth; header is cosmetic
            base_url=creds["base_url"],
            model=resolved_model,
            require_api_key=False,
        )
    if provider == "ollama":
        return OllamaLLM(
            base_url=creds["base_url"],
            model=resolved_model,
        )
    return MockLLM()


def get_llm_for_provider(provider: str, *, model: str | None = None) -> LLMClient:
    """A client PINNED to one provider, ignoring the app default.

    For callers whose identity IS the provider (e.g. the Deep Seek paper book —
    research/book_driver.py): switching the app's default LLM must never
    silently switch what runs that book. Returns MockLLM when the provider is
    unknown or its key is missing — callers treat mock as 'unconfigured'."""
    provider = (provider or "").strip().lower()
    if provider not in VALID_PROVIDERS:
        return MockLLM()
    ai = get_resolved_ai_settings()
    creds = provider_credentials(provider, ai)
    if provider_config(provider).get("requires_key") and not creds.get("api_key"):
        return MockLLM()
    return _build_llm_client(provider, creds, model=(model or "").strip() or None)


def get_llm(*, force_refresh: bool = False, model: str | None = None) -> LLMClient:
    global _cached, _cached_provider
    if force_refresh:
        clear_llm_cache()

    provider = get_llm_provider_name()
    ai = get_resolved_ai_settings()
    creds = provider_credentials(provider, ai)

    cfg = provider_config(provider)
    if cfg.get("requires_key") and not creds.get("api_key"):
        return MockLLM()

    if model:
        return _build_llm_client(provider, creds, model=model.strip())

    if _cached is not None and _cached_provider == provider:
        return _cached

    client = _build_llm_client(provider, creds)
    _cached = client
    _cached_provider = provider
    return client
