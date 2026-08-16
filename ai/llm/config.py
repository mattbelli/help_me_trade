"""Build LLM clients from persisted settings."""

from __future__ import annotations

from typing import Any

from config.ai_providers import AI_PROVIDERS, VALID_PROVIDERS
from config.settings import load_settings

_CONFIGURED_PROVIDER_ORDER = (
    "deepseek",
    "openai",
    "anthropic",
    "openrouter",
    "ollama",
    # lmstudio deliberately NOT here: it is reachable by default (founder's PC), so
    # auto-picking it would silently switch the app default — and JIT-load whatever
    # model lists first on his GPU (it loaded gemma-4 during a test run on 08-16).
    # Explicit only: get_llm_for_provider("lmstudio") / the user choosing it in settings.
)


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


def _first_configured_provider(settings: dict[str, Any]) -> str:
    for pid in _CONFIGURED_PROVIDER_ORDER:
        creds = provider_credentials(pid, settings)
        if pid == "ollama":
            if creds.get("base_url"):
                return pid
            continue
        if creds.get("api_key"):
            return pid
    return ""


def get_default_llm(ai: dict[str, Any] | None = None) -> tuple[str, str]:
    """Return (provider, model) for the app-wide default LLM."""
    settings = ai if ai is not None else get_resolved_ai_settings()
    block = settings.get("default_llm")
    if isinstance(block, dict):
        provider = str(block.get("provider", "")).strip().lower()
        model = str(block.get("model", "")).strip()
        if provider in VALID_PROVIDERS and provider != "mock":
            if model:
                return provider, model
            return provider, ""

    provider = str(settings.get("provider", "")).strip().lower()
    if provider in VALID_PROVIDERS and provider != "mock":
        creds = provider_credentials(provider, settings)
        model = creds.get("model", "")
        if model:
            return provider, model

    resolved = _first_configured_provider(settings)
    if resolved:
        creds = provider_credentials(resolved, settings)
        model = creds.get("model", "")
        if model:
            return resolved, model
    return "", ""


def sync_default_llm_fields(ai: dict[str, Any]) -> dict[str, Any]:
    """Keep legacy provider + per-provider model fields aligned with default_llm."""
    provider, model = get_default_llm(ai)
    out = dict(ai)
    if provider and provider in VALID_PROVIDERS and provider != "mock":
        out["provider"] = provider
        out["default_llm"] = {"provider": provider, "model": model}
        model_field = AI_PROVIDERS[provider].get("model_field")
        if model_field and model:
            out[model_field] = model
    return out


def maybe_upgrade_default_llm(ai: dict[str, Any]) -> dict[str, Any]:
    """When default has no model yet, promote the first configured provider with live models."""
    from ai.llm.models import list_chat_models

    out = dict(ai)
    provider, model = get_default_llm(out)
    if provider and model:
        return sync_default_llm_fields(out)

    for pid in _CONFIGURED_PROVIDER_ORDER:
        creds = provider_credentials(pid, out)
        if pid != "ollama" and not creds.get("api_key"):
            continue
        rows = list_chat_models(pid).get("models") or []
        if not rows:
            continue
        upgraded = {
            **out,
            "default_llm": {"provider": pid, "model": rows[0]["id"]},
        }
        return sync_default_llm_fields(upgraded)
    return out
