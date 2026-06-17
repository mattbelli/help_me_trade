"""Chat model catalog for the UI."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from ai.llm.config import get_default_llm, get_resolved_ai_settings, provider_credentials
from config.ai_providers import AI_PROVIDERS, VALID_PROVIDERS

logger = logging.getLogger(__name__)

_OPENAI_COMPAT_PROVIDERS = frozenset({"openai", "deepseek", "openrouter"})

_OPENAI_EXCLUDE_PREFIXES = (
    "text-embedding",
    "dall-e",
    "tts-",
    "whisper",
    "omni-moderation",
    "davinci",
    "babbage",
)

_OPENROUTER_CHAT_PREFIXES = (
    "openai/",
    "anthropic/",
    "google/",
    "deepseek/",
    "meta-llama/",
    "mistralai/",
    "qwen/",
)


def _merge_models(
    *,
    default_model: str,
    live: list[dict[str, str]],
) -> list[dict[str, str]]:
    seen: set[str] = set()
    merged: list[dict[str, str]] = []

    def add(item: dict[str, str]) -> None:
        model_id = str(item.get("id", "")).strip()
        if not model_id or model_id in seen:
            return
        seen.add(model_id)
        merged.append({"id": model_id, "label": str(item.get("label", model_id))})

    for item in live:
        add(item)
    if default_model:
        add({"id": default_model, "label": default_model})
    return merged


def _models_list_urls(base_url: str) -> list[str]:
    root = base_url.rstrip("/")
    candidates: list[str] = []
    if root.endswith("/v1"):
        candidates.append(f"{root}/models")
        root = root[:-3].rstrip("/")
    candidates.append(f"{root}/v1/models")
    if not root.endswith("/v1"):
        candidates.append(f"{root}/models")
    seen: set[str] = set()
    out: list[str] = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _should_include_model(provider: str, model_id: str) -> bool:
    mid = model_id.strip().lower()
    if not mid:
        return False
    if provider == "openai":
        if any(mid.startswith(p) for p in _OPENAI_EXCLUDE_PREFIXES):
            return False
        return bool(
            re.match(r"^(gpt-|o\d|chatgpt|ft:gpt-)", mid)
        )
    if provider == "deepseek":
        return mid.startswith("deepseek")
    if provider == "openrouter":
        return any(mid.startswith(p) for p in _OPENROUTER_CHAT_PREFIXES)
    return True


def _model_label(provider: str, model_id: str, item: dict[str, Any]) -> str:
    for key in ("name", "display_name", "title"):
        label = str(item.get(key, "")).strip()
        if label and label != model_id:
            return label
    if provider == "openrouter" and "/" in model_id:
        return model_id.split("/", 1)[1].replace("-", " ").title()
    return model_id


def _fetch_openai_compatible_models(
    provider: str,
    base_url: str,
    api_key: str,
) -> list[dict[str, str]]:
    if not api_key:
        return []

    headers = {"Authorization": f"Bearer {api_key}"}
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/BeerBelly69/help_me_trade"
        headers["X-Title"] = "Help Me Trade"

    last_error: Exception | None = None
    for url in _models_list_urls(base_url):
        try:
            with httpx.Client(timeout=6.0) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            last_error = exc
            continue

        rows = data.get("data") or data.get("models") or []
        out: list[dict[str, str]] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or item.get("name") or "").strip()
            if not model_id or not _should_include_model(provider, model_id):
                continue
            out.append(
                {
                    "id": model_id,
                    "label": _model_label(provider, model_id, item),
                }
            )
        if out:
            out.sort(key=lambda m: m["id"].lower())
            return out

    if last_error is not None:
        logger.debug(
            "OpenAI-compatible model list unavailable for %s: %s",
            provider,
            last_error,
        )
    return []


def _fetch_ollama_models(base_url: str) -> list[dict[str, str]]:
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    try:
        with httpx.Client(timeout=4.0) as client:
            resp = client.get(f"{root}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.debug("Ollama model list unavailable: %s", exc)
        return []

    out: list[dict[str, str]] = []
    for item in data.get("models") or []:
        name = str(item.get("name", "")).strip()
        if name:
            out.append({"id": name, "label": name})
    return out


def _picker_enabled_ids(ai: dict[str, Any], provider: str) -> list[str] | None:
    raw = ai.get("chat_enabled_models")
    if not isinstance(raw, dict):
        return None
    ids = raw.get(provider)
    if ids is None:
        return None
    if not isinstance(ids, list):
        return None
    cleaned = [str(x).strip() for x in ids if str(x).strip()]
    return cleaned or None


def _apply_picker_filter(
    models: list[dict[str, str]],
    *,
    enabled_ids: list[str] | None,
    default_model: str,
) -> list[dict[str, str]]:
    if not enabled_ids:
        return models
    allowed = set(enabled_ids)
    if default_model:
        allowed.add(default_model)
    filtered = [m for m in models if m["id"] in allowed]
    return filtered


_CONFIGURED_PROVIDER_ORDER = (
    "deepseek",
    "openai",
    "anthropic",
    "openrouter",
    "ollama",
)


def _first_configured_provider(ai: dict[str, Any]) -> str:
    for pid in _CONFIGURED_PROVIDER_ORDER:
        creds = provider_credentials(pid, ai)
        if pid == "ollama":
            if _fetch_ollama_models(creds.get("base_url", "")):
                return pid
            continue
        if creds.get("api_key"):
            return pid
    return ""


def _resolve_list_provider(ai: dict[str, Any], requested: str) -> str:
    if requested == "mock":
        return ""
    if requested and requested in VALID_PROVIDERS:
        return requested
    block = ai.get("default_llm")
    if isinstance(block, dict):
        provider = str(block.get("provider", "")).strip().lower()
        if provider and provider != "mock":
            return provider
    provider = str(ai.get("provider", "")).strip().lower()
    if provider and provider != "mock":
        return provider
    return _first_configured_provider(ai)


def list_chat_models(
    provider: str | None = None,
    *,
    catalog: bool = False,
) -> dict[str, Any]:
    ai = get_resolved_ai_settings()
    requested = (provider or "").strip().lower()
    active = _resolve_list_provider(ai, requested)

    if not active or active == "mock":
        empty: dict[str, Any] = {
            "provider": active or "",
            "default_model": "",
            "models": [],
            "supports_custom": False,
            "source": "unavailable",
        }
        if catalog:
            empty["catalog_models"] = []
            empty["picker_enabled_ids"] = None
        return empty

    creds = provider_credentials(active, ai)
    default_model = creds.get("model", "")
    enabled_ids = _picker_enabled_ids(ai, active)

    live: list[dict[str, str]] = []
    if active in _OPENAI_COMPAT_PROVIDERS:
        live = _fetch_openai_compatible_models(
            active,
            creds.get("base_url", ""),
            creds.get("api_key", ""),
        )
    elif active == "ollama":
        live = _fetch_ollama_models(creds.get("base_url", ""))

    if not live:
        catalog_models: list[dict[str, str]] = []
    else:
        catalog_models = _merge_models(default_model=default_model, live=live)
    source = "live" if catalog_models else "unavailable"

    picker_models = _apply_picker_filter(
        catalog_models,
        enabled_ids=enabled_ids,
        default_model=default_model,
    )

    result: dict[str, Any] = {
        "provider": active,
        "default_model": default_model,
        "models": picker_models,
        "supports_custom": True,
        "source": source,
    }
    if catalog:
        result["catalog_models"] = catalog_models
        result["picker_enabled_ids"] = enabled_ids
    return result


def list_default_model_options() -> dict[str, Any]:
    ai = get_resolved_ai_settings()
    provider, model = get_default_llm(ai)
    options: list[dict[str, str]] = []
    for pid in _CONFIGURED_PROVIDER_ORDER:
        meta = AI_PROVIDERS[pid]
        catalog = list_chat_models(pid, catalog=True)
        rows = catalog.get("catalog_models") or catalog.get("models") or []
        for row in rows:
            options.append(
                {
                    "provider": pid,
                    "model": row["id"],
                    "label": row["label"],
                    "provider_label": str(meta.get("label", pid)),
                }
            )
    return {
        "default_llm": {"provider": provider, "model": model},
        "options": options,
    }
