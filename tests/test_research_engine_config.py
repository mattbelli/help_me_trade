"""Tests for Research Engine v2 config helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from config import settings as settings_mod
from config.research_engine import (
    EFFORT_PRESETS,
    resolve_effort,
    resolve_role_model,
)


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()
    yield settings_path
    settings_mod.clear_settings_cache()


def _patch_research(**kwargs: object) -> None:
    settings_mod.apply_settings_patch({"research": kwargs})


def test_resolve_role_model_explicit_override(isolated_settings) -> None:
    _patch_research(model_roles={"planner": "custom-planner"})
    assert resolve_role_model("planner") == "custom-planner"


def test_resolve_role_model_no_override_uses_default(isolated_settings) -> None:
    # Manual-only: without an explicit per-role model, resolve to the app
    # default (None). Parked strong/fast/auto keys must NOT be consulted.
    _patch_research(
        auto_delegate=True,
        strong_model="deepseek-v4-pro",
        fast_model="deepseek-v4-flash",
    )
    for role in ("planner", "critic", "editor", "specialists", "extraction"):
        assert resolve_role_model(role) is None


def test_resolve_role_model_partial_override(isolated_settings) -> None:
    _patch_research(model_roles={"editor": "deepseek-v4-pro"})
    assert resolve_role_model("editor") == "deepseek-v4-pro"
    assert resolve_role_model("specialists") is None


def test_resolve_role_model_missing_keys_use_default(isolated_settings) -> None:
    _patch_research()
    assert resolve_role_model("planner") is None
    assert resolve_role_model("unknown") is None


def test_resolve_role_model_empty_explicit_uses_default(isolated_settings) -> None:
    # An empty per-role string means "app default", not a fall-through to auto.
    _patch_research(model_roles={"planner": ""}, strong_model="strong-model")
    assert resolve_role_model("planner") is None


def test_resolve_effort_each_preset(isolated_settings) -> None:
    for name, preset in EFFORT_PRESETS.items():
        resolved = resolve_effort(name)
        assert resolved.name == preset.name
        assert resolved.specialist_count == preset.specialist_count
        assert resolved.synthesis_target_words == preset.synthesis_target_words


def test_resolve_effort_unknown_falls_back_to_standard(isolated_settings) -> None:
    _patch_research(effort="not-a-preset")
    resolved = resolve_effort()
    assert resolved.name == "standard"


def test_resolve_effort_arg_beats_setting(isolated_settings) -> None:
    _patch_research(effort="quick")
    assert resolve_effort("deep").name == "deep"
    assert resolve_effort().name == "quick"


def test_resolve_effort_setting_when_arg_missing(isolated_settings) -> None:
    _patch_research(effort="exhaustive")
    assert resolve_effort().name == "exhaustive"
    assert resolve_effort(None).name == "exhaustive"


def test_resolve_role_model_never_raises(isolated_settings) -> None:
    with patch(
        "config.research_engine.current_research_settings",
        side_effect=RuntimeError("boom"),
    ):
        assert resolve_role_model("planner") is None
