"""`tools/flow_collector._main_rust_spine` argv/env for the two spines (no network, no binary run)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fc(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("flow_collector_under_test", ROOT / "tools" / "flow_collector.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    binary = tmp_path / "flow_rs"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setattr(mod, "FLOW_RS_BIN", binary)

    class FakeRunner:
        def __init__(self, **_kw):
            pass

    class FakeBackHalf:
        async def run(self):
            await asyncio.sleep(3600)

        def stop(self):
            pass

    async def no_poll(_runner):
        await asyncio.sleep(3600)

    monkeypatch.setattr(mod, "OptionsFlowRunner", FakeRunner)
    monkeypatch.setattr(mod, "_poll_loop", no_poll)
    monkeypatch.setattr("signals.options_flow.backhalf.BackHalf", FakeBackHalf)
    monkeypatch.setattr("config.settings.alpaca_config", lambda: {"api_key_id": "AK", "api_secret_key": "AS"})
    for k in ("OPTIONS_FLOW_SPINE", "OPTIONS_FLOW_PAIRING_SHADOW", "DATABENTO_API_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)

    calls = []

    class FakeProc:
        pid = 4242
        returncode = None

        async def wait(self):
            self.returncode = 0
            return 0

    async def fake_exec(*argv, env=None):
        calls.append((list(argv), env))
        return FakeProc()

    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", fake_exec)
    mod.calls = calls
    yield mod
    sys.modules.pop(spec.name, None)


def run(mod):
    return asyncio.run(mod._main_rust_spine())


def test_alpaca_spine_needs_no_databento_key_and_passes_creds_by_env(fc, monkeypatch):
    monkeypatch.setenv("OPTIONS_FLOW_SPINE", "alpaca")
    monkeypatch.setenv("OPTIONS_FLOW_PAIRING_SHADOW", "1")  # ignored: no dbn tape to pair with
    assert run(fc) == 0
    [(argv, env)] = fc.calls
    assert argv[1:] == ["live", "--db", argv[3], "--spine", "alpaca"]
    assert "--pairing" not in argv and "AK" not in argv and "AS" not in argv
    assert (env["APCA_API_KEY_ID"], env["APCA_API_SECRET_KEY"]) == ("AK", "AS")


def test_alpaca_spine_without_creds_exits_2(fc, monkeypatch):
    monkeypatch.setenv("OPTIONS_FLOW_SPINE", "ALPACA")
    monkeypatch.setenv("DATABENTO_API_KEY", "db-key")
    monkeypatch.setattr("config.settings.alpaca_config", lambda: {"api_key_id": "", "api_secret_key": ""})
    assert run(fc) == 2
    assert fc.calls == []


def test_dbn_spine_unchanged(fc, monkeypatch):
    assert run(fc) == 2  # no DATABENTO_API_KEY → refuses, as before
    assert fc.calls == []
    monkeypatch.setenv("DATABENTO_API_KEY", "db-key")
    monkeypatch.setenv("OPTIONS_FLOW_SPINE", "dbn")
    assert run(fc) == 0
    monkeypatch.setenv("OPTIONS_FLOW_PAIRING_SHADOW", "1")
    assert run(fc) == 0
    (plain, env0), (paired, env1) = fc.calls
    assert plain[1:] == ["live", "--db", plain[3]]
    assert "APCA_API_KEY_ID" not in env0
    assert paired[1:] == ["live", "--db", paired[3], "--pairing"]
    assert (env1["APCA_API_KEY_ID"], env1["APCA_API_SECRET_KEY"]) == ("AK", "AS")
