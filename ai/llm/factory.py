"""LLM provider factory — LLM_PROVIDER env (mock | openai | ollama)."""

from __future__ import annotations

import os

from ai.llm.base import LLMClient
from ai.llm.mock import MockLLM
from ai.llm.ollama import OllamaLLM
from ai.llm.openai import OpenAILLM

_cached: LLMClient | None = None


def get_llm_provider_name() -> str:
    name = (
        os.environ.get("LLM_PROVIDER")
        or os.environ.get("AI_PROVIDER")
        or "mock"
    ).strip().lower()
    if name not in ("mock", "openai", "ollama"):
        return "mock"
    return name


def get_llm(*, force_refresh: bool = False) -> LLMClient:
    global _cached
    if _cached is not None and not force_refresh:
        return _cached

    provider = get_llm_provider_name()
    if provider == "openai":
        _cached = OpenAILLM()
    elif provider == "ollama":
        _cached = OllamaLLM()
    else:
        _cached = MockLLM()
    return _cached
