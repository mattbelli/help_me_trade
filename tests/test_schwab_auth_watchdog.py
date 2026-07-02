"""Schwab token watchdog — auth clock, health probe, factory cache, reauth guard."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

_ET = ZoneInfo("America/New_York")


def _write_token(path: Path, *, creation_timestamp: int, access_token: str = "tok") -> None:
    path.write_text(
        json.dumps(
            {
                "creation_timestamp": creation_timestamp,
                "token": {
                    "access_token": access_token,
                    "refresh_token": "refresh",
                    "expires_in": 1800,
                    "token_type": "Bearer",
                },
            }
        ),
        encoding="utf-8",
    )


def test_write_preserves_creation_timestamp_and_writes_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "schwab_token.json"
    original_ts = int(time.time()) - 6 * 86400
    _write_token(token_file, creation_timestamp=original_ts)

    captured: list = []

    def _fake_oauth(*args, **kwargs):
        captured.append(kwargs.get("update_token"))
        return MagicMock()

    monkeypatch.setattr("brokers.schwab_auth.OAuth2Client", _fake_oauth)
    monkeypatch.setattr("brokers.schwab_auth.Client", lambda *a, **k: MagicMock())
    monkeypatch.setattr(
        "brokers.schwab_auth.auth.TokenMetadata",
        lambda token, ts, write: SimpleNamespace(),
    )

    from brokers.schwab_auth import _client_from_token_basic

    _client_from_token_basic("key", "secret", token_file)
    write_fn = captured[0]
    assert callable(write_fn)

    write_fn({"access_token": "new1", "refresh_token": "r1"})
    payload1 = json.loads(token_file.read_text(encoding="utf-8"))
    assert payload1["creation_timestamp"] == original_ts
    assert "token" in payload1

    write_fn({"access_token": "new2", "refresh_token": "r2"})
    payload2 = json.loads(token_file.read_text(encoding="utf-8"))
    assert payload2["creation_timestamp"] == original_ts
    assert payload2["token"]["access_token"] == "new2"
    assert not (token_file.parent / "schwab_token.tmp").exists()


def test_write_prefers_fresh_on_disk_timestamp_after_remint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cron re-mint replaces the file while a client is alive; the stale
    client's next refresh write must keep the FRESH clock, not its snapshot."""
    token_file = tmp_path / "schwab_token.json"
    stale_ts = int(time.time()) - 6 * 86400
    _write_token(token_file, creation_timestamp=stale_ts)

    captured: list = []
    monkeypatch.setattr(
        "brokers.schwab_auth.OAuth2Client",
        lambda *a, **k: captured.append(k.get("update_token")) or MagicMock(),
    )
    monkeypatch.setattr("brokers.schwab_auth.Client", lambda *a, **k: MagicMock())
    monkeypatch.setattr(
        "brokers.schwab_auth.auth.TokenMetadata",
        lambda token, ts, write: SimpleNamespace(),
    )

    from brokers.schwab_auth import _client_from_token_basic

    _client_from_token_basic("key", "secret", token_file)
    write_fn = captured[0]

    fresh_ts = int(time.time())
    _write_token(token_file, creation_timestamp=fresh_ts, access_token="minted")

    write_fn({"access_token": "refreshed", "refresh_token": "r"})
    payload = json.loads(token_file.read_text(encoding="utf-8"))
    assert payload["creation_timestamp"] == fresh_ts


def test_get_broker_rebuilds_schwab_adapter_on_token_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The execution-side broker cache must heal on a token re-mint too."""
    import brokers.factory as factory

    token_file = tmp_path / "schwab_token.json"
    _write_token(token_file, creation_timestamp=int(time.time()))

    monkeypatch.setattr(factory, "_broker_cache", {})
    monkeypatch.setattr(factory, "_schwab_token_mtimes", {})
    monkeypatch.setattr(
        "brokers.schwab_auth.token_path", lambda: token_file
    )
    built: list = []

    def _fake_create(name: str):
        adapter = MagicMock(name=f"adapter{len(built)}")
        built.append(adapter)
        return adapter

    monkeypatch.setattr(factory, "_create_broker", _fake_create)

    first = factory.get_broker("schwab")
    assert factory.get_broker("schwab") is first

    import os as _os

    _write_token(token_file, creation_timestamp=int(time.time()) + 60)
    _os.utime(token_file, (time.time() + 5, time.time() + 5))

    second = factory.get_broker("schwab")
    assert second is not first
    assert len(built) == 2


def test_auth_status_reports_true_refresh_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "token.json"
    created_at = time.time() - 6.5 * 86400
    _write_token(token_file, creation_timestamp=int(created_at))

    monkeypatch.setenv("SCHWAB_TOKEN_PATH", str(token_file))

    from brokers.schwab_auth import auth_status

    status = auth_status()
    assert status["authenticated"] is True
    assert status["estimated_days_until_reauth"] == pytest.approx(0.5, abs=0.15)
    assert status["refresh_token_age_days"] == pytest.approx(6.5, abs=0.15)


@pytest.fixture
def reset_schwab_health_state() -> None:
    import api.main as main_mod

    main_mod._schwab_health_last_date.clear()
    main_mod._schwab_probe_failures = 0


@pytest.mark.asyncio
async def test_schwab_health_probe_alerts_after_three_failures(
    monkeypatch: pytest.MonkeyPatch, reset_schwab_health_state
) -> None:
    from api.main import _schwab_health_once

    notify_calls: list[dict] = []

    async def _fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "probe-test"}

    provider = SimpleNamespace(
        aget_quotes=AsyncMock(side_effect=RuntimeError("probe boom"))
    )

    monkeypatch.setattr("api.main.broker_is_configured", lambda name: name == "schwab")
    monkeypatch.setattr("api.main.get_market_data_provider", lambda name=None: provider)
    monkeypatch.setattr(
        "brokers.schwab_auth.auth_status",
        lambda: {
            "authenticated": True,
            "estimated_days_until_reauth": 5.0,
        },
    )
    monkeypatch.setattr(
        "api.main.datetime",
        type(
            "FixedDatetime",
            (),
            {
                "now": staticmethod(
                    lambda tz=None: datetime(2026, 6, 24, 11, 0, tzinfo=_ET)
                )
            },
        ),
    )
    monkeypatch.setattr("signals.notify.notify", _fake_notify)

    for _ in range(3):
        await _schwab_health_once()

    probe_calls = [c for c in notify_calls if c.get("data", {}).get("auth_status") == "probe_failed"]
    assert len(probe_calls) == 1
    assert probe_calls[0]["severity"] == "critical"

    await _schwab_health_once()
    assert len(probe_calls) == 1


@pytest.mark.asyncio
async def test_schwab_health_probe_success_resets_failure_streak(
    monkeypatch: pytest.MonkeyPatch, reset_schwab_health_state
) -> None:
    import api.main as main_mod
    from api.main import _schwab_health_once

    notify_calls: list[dict] = []

    async def _fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "probe-test"}

    fail = True

    async def _quotes(_symbols):
        if fail:
            raise RuntimeError("probe boom")
        return {"SPY": {"regular_last": 500.0}}

    provider = SimpleNamespace(aget_quotes=_quotes)

    monkeypatch.setattr("api.main.broker_is_configured", lambda name: name == "schwab")
    monkeypatch.setattr("api.main.get_market_data_provider", lambda name=None: provider)
    monkeypatch.setattr(
        "brokers.schwab_auth.auth_status",
        lambda: {"authenticated": True, "estimated_days_until_reauth": 5.0},
    )
    monkeypatch.setattr(
        "api.main.datetime",
        type(
            "FixedDatetime",
            (),
            {
                "now": staticmethod(
                    lambda tz=None: datetime(2026, 6, 24, 11, 0, tzinfo=_ET)
                )
            },
        ),
    )
    monkeypatch.setattr("signals.notify.notify", _fake_notify)

    for _ in range(2):
        await _schwab_health_once()
    assert main_mod._schwab_probe_failures == 2

    fail = False
    await _schwab_health_once()
    assert main_mod._schwab_probe_failures == 0
    assert not notify_calls


def test_factory_cache_invalidates_on_token_mtime_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "token.json"
    _write_token(token_file, creation_timestamp=int(time.time()))

    monkeypatch.setenv("SCHWAB_TOKEN_PATH", str(token_file))
    monkeypatch.setenv("SCHWAB_API_KEY", "key")
    monkeypatch.setenv("SCHWAB_APP_SECRET", "secret")
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "schwab")

    created: list[object] = []

    class _FakeSchwabMarketData:
        def __init__(self, client: object) -> None:
            self._client = client
            created.append(self)

        @classmethod
        def from_settings(cls):
            return cls(object())

    import brokers.factory as factory

    factory.clear_broker_cache()
    monkeypatch.setattr(
        "brokers.factory.SchwabMarketData",
        _FakeSchwabMarketData,
    )

    first = factory.get_market_data_provider("schwab")
    second = factory.get_market_data_provider("schwab")
    assert first is second
    assert len(created) == 1

    time.sleep(0.02)
    token_file.touch()
    third = factory.get_market_data_provider("schwab")
    assert third is not first
    assert len(created) == 2

    factory.clear_broker_cache()


def test_reauth_skip_if_fresh_exits_without_playwright(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "token.json"
    _write_token(token_file, creation_timestamp=int(time.time()) - 86400)

    monkeypatch.setenv("SCHWAB_TOKEN_PATH", str(token_file))

    called = {"reauth": False}

    def _boom():
        called["reauth"] = True
        raise AssertionError("should not run reauth")

    monkeypatch.setattr("scripts.schwab_reauth.run_headless_reauth", _boom)

    from scripts.schwab_reauth import main

    assert main([]) == 0
    assert called["reauth"] is False


def test_reauth_no_skip_if_fresh_runs_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "token.json"
    _write_token(token_file, creation_timestamp=int(time.time()) - 86400)

    monkeypatch.setenv("SCHWAB_TOKEN_PATH", str(token_file))

    called = {"reauth": False}

    def _ok():
        called["reauth"] = True

    monkeypatch.setattr("scripts.schwab_reauth.run_headless_reauth", _ok)

    from scripts.schwab_reauth import main

    assert main(["--no-skip-if-fresh"]) == 0
    assert called["reauth"] is True
