"""Research Engine v2 configuration: model roles and effort presets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ROLES: tuple[str, ...] = (
    "planner",
    "specialists",
    "extraction",
    "critic",
    "editor",
    # Fusion Phase C3 investigation lane (research/reason.py): T1 batch triage and
    # REASON v1. Per-role model pinning resolves from settings.research.model_roles;
    # unpinned falls back to the app default model.
    "triage",
    "reason",
)

DEFAULT_EFFORT = "standard"


@dataclass(frozen=True)
class ResearchEffort:
    name: str
    specialist_count: int
    rounds_per_specialist: int
    self_stop: bool
    self_stop_cap: int
    per_source_content_limit: int
    source_cap: int
    web_results_per_query: int
    synthesis_target_words: int


EFFORT_PRESETS: dict[str, ResearchEffort] = {
    "quick": ResearchEffort(
        name="quick",
        specialist_count=2,
        rounds_per_specialist=1,
        self_stop=False,
        self_stop_cap=1,
        per_source_content_limit=2500,
        source_cap=20,
        web_results_per_query=4,
        synthesis_target_words=700,
    ),
    "standard": ResearchEffort(
        name="standard",
        specialist_count=4,
        rounds_per_specialist=2,
        self_stop=False,
        self_stop_cap=3,
        per_source_content_limit=4000,
        source_cap=40,
        web_results_per_query=5,
        synthesis_target_words=1200,
    ),
    "deep": ResearchEffort(
        name="deep",
        specialist_count=5,
        rounds_per_specialist=3,
        self_stop=False,
        self_stop_cap=5,
        per_source_content_limit=6000,
        source_cap=60,
        web_results_per_query=6,
        synthesis_target_words=1800,
    ),
    "exhaustive": ResearchEffort(
        name="exhaustive",
        specialist_count=5,
        rounds_per_specialist=3,
        self_stop=True,
        self_stop_cap=20,
        per_source_content_limit=8000,
        source_cap=120,
        web_results_per_query=8,
        synthesis_target_words=2500,
    ),
}

EFFORT_PRESET_NAMES: tuple[str, ...] = tuple(EFFORT_PRESETS.keys())

_DEFAULT_RESEARCH: dict[str, Any] = {
    "searxng_url": "",
    "model_roles": {},
    "strong_model": "",
    "fast_model": "",
    "auto_delegate": True,
    "effort": DEFAULT_EFFORT,
}


def _research_block() -> dict[str, Any]:
    try:
        from config.settings import load_settings

        raw = load_settings().get("research", {})
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return {}


def get_default_model_name() -> str:
    try:
        from ai.llm.config import get_default_llm

        _, model = get_default_llm()
        return str(model or "").strip()
    except Exception:
        return ""


def _non_empty(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def current_research_settings() -> dict[str, Any]:
    """Normalized research settings for API/UI consumption."""
    raw = _research_block()
    model_roles_raw = raw.get("model_roles")
    model_roles: dict[str, str] = {}
    if isinstance(model_roles_raw, dict):
        for role in ROLES:
            if role in model_roles_raw:
                model_roles[role] = str(model_roles_raw.get(role, "") or "").strip()

    effort = str(raw.get("effort", DEFAULT_EFFORT) or DEFAULT_EFFORT).strip() or DEFAULT_EFFORT
    if effort not in EFFORT_PRESETS:
        effort = DEFAULT_EFFORT

    auto_delegate = raw.get("auto_delegate", True)
    if not isinstance(auto_delegate, bool):
        auto_delegate = True

    return {
        "searxng_url": str(raw.get("searxng_url", "") or "").strip().rstrip("/"),
        "model_roles": model_roles,
        "strong_model": str(raw.get("strong_model", "") or "").strip(),
        "fast_model": str(raw.get("fast_model", "") or "").strip(),
        "auto_delegate": auto_delegate,
        "effort": effort,
    }


def resolve_role_model(role: str) -> str | None:
    """Resolve a research role to a model name, or None for the app default.

    Manual-only: an explicit per-role model wins; otherwise the app default.
    (Automatic strong/fast delegation was removed — the ``strong_model`` /
    ``fast_model`` / ``auto_delegate`` keys stay parked in settings for a future
    real-auto revisit, but are not consulted here.)
    """
    try:
        role_key = str(role or "").strip().lower()
        if role_key not in ROLES:
            return None
        cfg = current_research_settings()
        return _non_empty(cfg.get("model_roles", {}).get(role_key))
    except Exception:
        return None


def resolve_effort(name: str | None = None) -> ResearchEffort:
    chosen = str(name or "").strip()
    if not chosen:
        try:
            chosen = str(current_research_settings().get("effort", DEFAULT_EFFORT)).strip()
        except Exception:
            chosen = DEFAULT_EFFORT
    if chosen not in EFFORT_PRESETS:
        chosen = DEFAULT_EFFORT
    return EFFORT_PRESETS[chosen]
