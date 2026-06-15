"""Chat model catalog for the UI."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ai.llm.config import get_resolved_ai_settings, provider_credentials
from ai.llm.factory import get_llm_provider_name
from config.chat_models import CHAT_MODEL_PRESETS

logger = logging.getLogger(__name__)


def _merge_models(
    presets: list[dict[str, str]],
    *,
    default_model: str,
    extra: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    seen: set[str] = set()
    merged: list[dict[str, str]] = []

    def add(item: dict[str, str]) -> None:
        model_id = str(item.get("id", "")).strip()
        if not model_id or model_id in seen:
            return
        seen.add(model_id)
        merged.append({"id": model_id, "label": str(item.get("label", model_id))})

    if default_model:
        add(
            {
                "id": default_model,
                "label": f"{default_model} (settings default)",
            }
        )
    for item in presets:
        add(item)
    for item in extra or []:
        add(item)
    return merged


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


def list_chat_models(provider: str | None = None) -> dict[str, Any]:
    active = provider or get_llm_provider_name()
    ai = get_resolved_ai_settings()
    creds = provider_credentials(active, ai)
    default_model = creds.get("model", "")
    presets = list(CHAT_MODEL_PRESETS.get(active, []))

    extra: list[dict[str, str]] = []
    if active == "ollama":
        extra = _fetch_ollama_models(creds.get("base_url", ""))

    models = _merge_models(presets, default_model=default_model, extra=extra)
    if not models and default_model:
        models = [{"id": default_model, "label": default_model}]

    return {
        "provider": active,
        "default_model": default_model,
        "models": models,
        "supports_custom": active != "mock",
    }
