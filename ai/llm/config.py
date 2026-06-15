"""Build LLM clients from persisted settings."""

from __future__ import annotations

from typing import Any

from config.ai_providers import AI_PROVIDERS, VALID_PROVIDERS
from config.settings import load_settings


def get_resolved_ai_settings() -> dict[str, Any]:
    return load_settings().get("ai", {})


def provider_config(provider: str) -> dict[str, Any]:
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    return AI_PROVIDERS[provider]


def provider_credentials(provider: str, ai: dict[str, Any] | None = None) -> dict[str, str]:
    cfg = provider_config(provider)
    settings = ai if ai is not None else get_resolved_ai_settings()
    base_url = str(
        settings.get(cfg.get("base_url_field", ""), cfg.get("default_base_url", ""))
    ).strip()
    model = str(
        settings.get(cfg.get("model_field", ""), cfg.get("default_model", ""))
    ).strip()
    key = ""
    key_field = cfg.get("key_field")
    if key_field:
        key = str(settings.get(key_field, "")).strip()
    return {
        "api_key": key,
        "base_url": base_url or str(cfg.get("default_base_url", "")),
        "model": model or str(cfg.get("default_model", "")),
    }
