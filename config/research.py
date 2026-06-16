"""Resolve research-related configuration."""

from __future__ import annotations

import os


def get_searxng_url() -> str:
    env = os.environ.get("SEARXNG_URL", "").strip().rstrip("/")
    if env:
        return env
    try:
        from config.settings import load_settings

        research = load_settings().get("research", {})
        if isinstance(research, dict):
            return str(research.get("searxng_url", "")).strip().rstrip("/")
    except Exception:
        pass
    return ""
