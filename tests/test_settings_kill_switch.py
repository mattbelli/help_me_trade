"""Kill switch settings must reflect writes immediately."""

from __future__ import annotations

from pathlib import Path

from config import settings as settings_mod


def test_set_kill_switch_is_visible_immediately(tmp_path: Path, monkeypatch) -> None:
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    assert settings_mod.is_kill_switch_active() is False

    settings_mod.set_kill_switch(True)

    assert settings_mod.is_kill_switch_active() is True

    settings_mod.set_kill_switch(False)

    assert settings_mod.is_kill_switch_active() is False
