"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_auth_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("api.auth.API_TOKEN", "")


@pytest.fixture(autouse=True)
def _isolate_poll_health(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset global poll health between tests."""
    import sys

    from api.poll_health import PollHealth

    fresh = PollHealth()
    monkeypatch.setattr("api.poll_health.poll_health", fresh)
    if "api.main" in sys.modules:
        monkeypatch.setattr("api.main.poll_health", fresh)
