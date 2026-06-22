"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_auth_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("api.auth.API_TOKEN", "")


@pytest.fixture(autouse=True)
def _neutralize_ambient_alpaca_suffixes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any APCA_* connection-suffix vars leaked from the real .env.

    config.settings calls load_dotenv() at import, so the developer's .env
    (which may hold a 2nd account's APCA_API_KEY_ID_STRAT1 etc.) populates
    os.environ for the whole test process. That makes
    discover_alpaca_credential_suffixes() find real suffixes and register
    unexpected accounts in throwaway DBs. Remove them by default; a test that
    wants a suffixed connection sets it explicitly via monkeypatch.setenv.
    """
    import os
    import re

    suffix_re = re.compile(
        r"^APCA_(API_KEY_ID|API_SECRET_KEY|API_BASE_URL|DATA_FEED|DATA_BASE_URL)_(.+)$"
    )
    for key in list(os.environ):
        if suffix_re.match(key):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _clear_broker_caches():
    """Clear module-global broker adapter caches (incl. the new per-connection
    cache) between tests so a stale adapter never leaks across tests."""
    from brokers import factory

    factory._broker_cache.clear()
    factory._broker_by_connection.clear()
    yield
    factory._broker_cache.clear()
    factory._broker_by_connection.clear()


@pytest.fixture(autouse=True)
def _isolate_poll_health(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset global poll health between tests."""
    import sys

    from api.poll_health import PollHealth

    fresh = PollHealth()
    monkeypatch.setattr("api.poll_health.poll_health", fresh)
    if "api.main" in sys.modules:
        monkeypatch.setattr("api.main.poll_health", fresh)
