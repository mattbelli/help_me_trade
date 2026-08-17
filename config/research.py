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


def get_firecrawl_url() -> str:
    """Base URL of a self-hosted Firecrawl, or "" when it is not configured.

    Opt-in like SearXNG: absent config means the plain httpx reader is used, so
    a checkout without the local stack still works.
    """
    env = os.environ.get("FIRECRAWL_URL", "").strip().rstrip("/")
    if env:
        return env
    try:
        from config.settings import load_settings

        research = load_settings().get("research", {})
        if isinstance(research, dict):
            return str(research.get("firecrawl_url", "")).strip().rstrip("/")
    except Exception:
        pass
    return ""
