"""Kill switch settings must reflect writes immediately."""

from __future__ import annotations

import threading
import time
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


def test_concurrent_patch_cannot_clobber_armed_kill_switch(
    tmp_path: Path, monkeypatch
) -> None:
    """A concurrent unrelated PUT must not lost-update an armed breaker flag.

    Reproduces the read-modify-write race: thread A is stalled mid-RMW (inside
    save) while thread B arms the kill switch. Before the fix (lock released
    between load and save) A's stale save clobbered B's arm back to False;
    now the whole RMW is held under the reentrant lock so B blocks until A
    finishes and its arm survives.
    """
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    real_save = settings_mod.save_settings
    a_in_save = threading.Event()
    release_a = threading.Event()
    saves = {"n": 0}

    def blocking_save(payload: dict) -> None:
        saves["n"] += 1
        if saves["n"] == 1:  # A's write — stall it mid-RMW
            a_in_save.set()
            release_a.wait(timeout=5)
        real_save(payload)

    monkeypatch.setattr(settings_mod, "save_settings", blocking_save)

    def thread_a() -> None:
        settings_mod.apply_settings_patch({"trading": {"max_open_orders": 7}})

    def thread_b() -> None:
        settings_mod.set_kill_switch(True)  # arm the breaker concurrently

    ta = threading.Thread(target=thread_a)
    ta.start()
    assert a_in_save.wait(timeout=5)  # A now holds the lock, stalled in save

    tb = threading.Thread(target=thread_b)
    tb.start()
    time.sleep(0.1)  # B must be blocked on the lock, not clobbering
    release_a.set()  # let A finish and release

    ta.join(timeout=5)
    tb.join(timeout=5)

    assert settings_mod.is_kill_switch_active() is True
    assert settings_mod.load_settings()["trading"]["max_open_orders"] == 7
