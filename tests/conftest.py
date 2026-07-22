"""Shared pytest fixtures."""

from __future__ import annotations

# SAFETY NET (must run before any `db.base` import): never let a test touch the
# real production DB. Routes/TestClient and any code using DEFAULT_DB_PATH get a
# throwaway DB instead of data/trading.db. Forced (not setdefault) so a stray env
# var can't point tests at production. Tests that pass an explicit tmp db_path are
# unaffected. (The build's tests twice wrote to the real DB via DEFAULT_DB_PATH —
# this closes that hole.)
import os  # noqa: E402
import tempfile  # noqa: E402

_TEST_DEFAULT_DB = os.path.join(tempfile.gettempdir(), "hmt_pytest_default.db")
os.environ["TRADING_DB_PATH"] = _TEST_DEFAULT_DB
# Deterministic broker baseline: the box's .env sets BROKER=schwab (inert in
# production, where data/settings.json overrides it — but tests hide the
# settings file, so the env leaked through and proposal tests defaulted to the
# read-only Schwab broker: 'blocked'/'rejected'). Tests that want schwab set it
# explicitly via monkeypatch.setenv.
os.environ["BROKER"] = "alpaca"
# Minimal app lifespan under TestClient: schema init + job registration only —
# no market_hub, no broker reconciliation, no inline network poll, no
# background loops. The full production lifespan is the suite-hang class
# (startup does real network; shutdown awaits cancels that can't interrupt
# asyncio.to_thread). Forced, not setdefault — read lazily in api.main so a
# test that truly needs the full lifespan can monkeypatch it away.
os.environ["HMT_TEST_LIFESPAN"] = "minimal"
# Hermetic Ollama: point the base URL at a dead port so provider auto-resolution
# (maybe_upgrade_default_llm probes localhost:11434 for live models — no key
# needed) gets connection-refused on every box. Without this, running the suite
# on a machine with a live Ollama daemon (serv, or any dev box) silently flips
# the app-wide default LLM to a real local model and tests do live inference.
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9"
for _suffix in ("", "-wal", "-shm"):
    try:
        os.remove(_TEST_DEFAULT_DB + _suffix)
    except OSError:
        pass

# Hermetic LLM: point persistent settings at a nonexistent temp file so tests
# never inherit the box's data/settings.json (whose default_llm may be a live
# local Ollama model — the Phase-1 suite silently ran ~40 live gemma calls and
# graded tests with a real model). With no settings file, get_llm() falls back
# to MockLLM everywhere; a test that wants a specific LLM stubs it explicitly.
import pathlib  # noqa: E402

from config import settings as _settings_mod  # noqa: E402

_settings_mod.SETTINGS_PATH = pathlib.Path(tempfile.gettempdir()) / (
    "hmt_pytest_settings_missing.json"
)
try:
    _settings_mod.SETTINGS_PATH.unlink()
except OSError:
    pass
_settings_mod.clear_settings_cache()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TEST_API_TOKEN = "pytest-test-token"


def auth_headers(token: str = TEST_API_TOKEN) -> dict[str, str]:
    return {"X-API-Token": token}


@pytest.fixture(autouse=True)
def _disable_auth_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("api.auth.API_TOKEN", "")
    monkeypatch.setattr("api.auth.ADMIN_TOKEN", "")


@pytest.fixture(autouse=True)
def _clear_settings_cache_after_test() -> None:
    yield
    from config import settings as settings_mod

    settings_mod.clear_settings_cache()


@pytest.fixture
def mutation_client(monkeypatch: pytest.MonkeyPatch):
    """TestClient with API_TOKEN set and auth header for mutating routes."""
    monkeypatch.setattr("api.auth.API_TOKEN", TEST_API_TOKEN)
    from api.main import app

    with TestClient(
        app, base_url="http://127.0.0.1", headers=auth_headers()
    ) as client:
        yield client


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
    factory._market_data_cache.clear()
    yield
    factory._broker_cache.clear()
    factory._broker_by_connection.clear()
    factory._market_data_cache.clear()


@pytest.fixture(autouse=True)
def _isolate_poll_health(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset global poll health between tests."""
    import sys

    from api.poll_health import PollHealth

    fresh = PollHealth()
    monkeypatch.setattr("api.poll_health.poll_health", fresh)
    if "api.main" in sys.modules:
        monkeypatch.setattr("api.main.poll_health", fresh)
