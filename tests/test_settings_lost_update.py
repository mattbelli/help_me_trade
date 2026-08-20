"""#242: a settings write from a stale per-process cache must never delete
another writer's keys. Every writer read-modify-writes against the FILE
(fresh read under the cross-process flock), never dumps its cache.

The incident this pins: options_flow.provider=databento was armed out-of-band;
a phone watchlist edit then saved the backend's pre-arming cache, rewriting
settings.json WITHOUT the key, and the collector silently fell back to Alpaca.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config.settings as settings_mod


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", path)
    settings_mod.clear_settings_cache()
    yield path
    settings_mod.clear_settings_cache()


def _out_of_band_write(path: Path, extra: dict) -> None:
    """Another PROCESS edits settings.json — this process's cache never hears."""
    on_disk = json.loads(path.read_text()) if path.exists() else {}
    on_disk.update(extra)
    path.write_text(json.dumps(on_disk))


def _on_disk(path: Path) -> dict:
    return json.loads(path.read_text())


def test_watchlist_save_preserves_out_of_band_key(settings_file: Path) -> None:
    settings_mod.load_settings()  # cache built BEFORE the out-of-band arming
    _out_of_band_write(settings_file, {"options_flow": {"provider": "databento"}})

    settings_mod.add_watchlist_symbol("SPY")  # the exact write that lost the key

    on_disk = _on_disk(settings_file)
    assert on_disk["options_flow"] == {"provider": "databento"}
    assert "SPY" in on_disk["watchlist"]


def test_chart_layout_save_preserves_out_of_band_key(settings_file: Path) -> None:
    settings_mod.load_settings()
    _out_of_band_write(settings_file, {"options_flow": {"provider": "databento"}})

    settings_mod.save_chart_layout([{"symbol": "SPY", "interval": "1D"}])

    on_disk = _on_disk(settings_file)
    assert on_disk["options_flow"] == {"provider": "databento"}
    assert on_disk["chart_layout"] == [{"symbol": "SPY", "interval": "1D"}]


def test_settings_patch_preserves_out_of_band_key(settings_file: Path) -> None:
    settings_mod.load_settings()
    _out_of_band_write(settings_file, {"options_flow": {"provider": "databento"}})

    settings_mod.apply_settings_patch({"alerts": {"max_push_per_day": 7}})

    on_disk = _on_disk(settings_file)
    assert on_disk["options_flow"] == {"provider": "databento"}
    assert on_disk["alerts"]["max_push_per_day"] == 7


def test_fresh_file_value_beats_stale_cache_value(settings_file: Path) -> None:
    """Not just deleted keys — a stale cache must not roll back a VALUE either."""
    settings_mod.apply_settings_patch({"alerts": {"max_push_per_day": 5}})  # cache in sync
    _out_of_band_write(settings_file, {"alerts": {"max_push_per_day": 7}})

    settings_mod.save_chart_layout([])  # unrelated write from the stale process

    assert _on_disk(settings_file)["alerts"]["max_push_per_day"] == 7


def test_patch_watchlist_branch_reads_fresh_state(settings_file: Path) -> None:
    """The legacy {"watchlist": [...]} patch derives watchlists state from the
    fresh file, not the cache — a stale cache must not resurrect an old list."""
    settings_mod.create_watchlist("Swing", ["AAPL"])
    fresh = json.loads(settings_file.read_text())
    settings_mod.clear_settings_cache()
    settings_mod.load_settings()
    # Out-of-band: another process renames the ACTIVE list on disk.
    fresh["watchlists"]["lists"][-1]["name"] = "Swing v2"
    settings_file.write_text(json.dumps(fresh))
    settings_mod.clear_settings_cache()
    settings_mod.load_settings()  # rebuild, then go stale again
    settings_file.write_text(json.dumps(fresh))

    settings_mod.apply_settings_patch({"watchlist": ["MSFT"]})

    on_disk = _on_disk(settings_file)
    active = [
        item
        for item in on_disk["watchlists"]["lists"]
        if item["id"] == on_disk["watchlists"]["active_id"]
    ][0]
    assert active["name"] == "Swing v2"
    assert active["symbols"] == ["MSFT"]


def test_cache_stays_coherent_after_mutate(settings_file: Path) -> None:
    """In-process readers see the write immediately (cache refreshed on save)."""
    settings_mod.add_watchlist_symbol("QQQ")
    assert "QQQ" in settings_mod.get_watchlist()


def test_corrupt_file_write_repairs_from_cache_and_keeps_breaker(settings_file: Path) -> None:
    """Review F1 (CRITICAL): an unreadable settings.json must never become
    'defaults' on disk — that would silently disarm the breakers."""
    settings_mod.apply_settings_patch({"trading": {"entries_halted": True}})
    settings_file.write_text('{"trading": {"entries_halted": tr')  # torn write

    settings_mod.save_chart_layout([])  # any unrelated write

    on_disk = _on_disk(settings_file)  # parses again = repaired
    assert on_disk["trading"]["entries_halted"] is True


def test_corrupt_file_cold_cache_refuses_to_write(settings_file: Path) -> None:
    garbage = '{"trading": {"kill_switch_active": tr'
    settings_file.write_text(garbage)
    settings_mod.clear_settings_cache()

    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        settings_mod.save_chart_layout([])

    assert settings_file.read_text() == garbage  # untouched


def test_breaker_readers_fail_closed_on_corrupt_file(settings_file: Path) -> None:
    settings_file.write_text("not json at all")
    settings_mod.clear_settings_cache()

    assert settings_mod.is_kill_switch_active() is True
    assert settings_mod.is_entries_halted() is True


def test_out_of_band_watchlist_survives_symbol_add(settings_file: Path) -> None:
    """Review F2 (HIGH): the watchlists SUBTREE itself must not be a stale-cache
    wholesale replace — a list another process added has to survive."""
    settings_mod.create_watchlist("Main2", ["AAPL"])  # cache in sync
    on_disk = _on_disk(settings_file)
    on_disk["watchlists"]["lists"].append(
        {"id": "swing", "name": "Swing", "symbols": ["TSLA"]}
    )
    settings_file.write_text(json.dumps(on_disk))  # out-of-band new list

    symbols = settings_mod.add_watchlist_symbol("QQQ")  # from the stale process

    lists = {item["id"]: item for item in _on_disk(settings_file)["watchlists"]["lists"]}
    assert "swing" in lists and lists["swing"]["symbols"] == ["TSLA"]
    assert "QQQ" in symbols


def test_reentrant_settings_write_raises(settings_file: Path) -> None:
    def mutate(current: dict) -> None:
        settings_mod.save_chart_layout([])  # would self-deadlock; must raise

    with pytest.raises(RuntimeError, match="re-entrant"):
        settings_mod._mutate_settings(mutate)


def test_cross_process_flock_serializes_writers(settings_file: Path) -> None:
    """A foreign PROCESS holding the lock: our write waits, then lands on top of
    the foreign write instead of clobbering it (threading.Lock can't do this)."""
    import subprocess
    import sys
    import time as _time

    child_src = (
        "import fcntl, json, sys, time\n"
        "path = sys.argv[1]\n"
        "fh = open(path + '.lock', 'w')\n"
        "fcntl.flock(fh, fcntl.LOCK_EX)\n"
        "print('locked', flush=True)\n"
        "time.sleep(0.8)\n"
        "d = json.load(open(path)) if __import__('os').path.exists(path) else {}\n"
        "d['foreign'] = 1\n"
        "open(path, 'w').write(json.dumps(d))\n"
        "fcntl.flock(fh, fcntl.LOCK_UN)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_src, str(settings_file)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "locked"
        t0 = _time.monotonic()
        settings_mod.save_chart_layout([{"symbol": "SPY"}])
        elapsed = _time.monotonic() - t0
    finally:
        child.wait(timeout=10)

    assert elapsed > 0.4  # we actually waited for the foreign holder
    on_disk = _on_disk(settings_file)
    assert on_disk["foreign"] == 1  # foreign write survived under ours
    assert on_disk["chart_layout"] == [{"symbol": "SPY"}]
