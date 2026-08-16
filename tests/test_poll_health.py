"""Poll loop resilience and /health staleness."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.poll_health import PollHealth


def test_poll_health_records_and_staleness() -> None:
    health = PollHealth()
    assert health.age_seconds() is None
    assert health.is_healthy(20) is False

    past = datetime.now(UTC) - timedelta(seconds=10)
    health.last_successful_poll_at = past
    age = health.age_seconds()
    assert age is not None
    assert 9 <= age <= 15
    assert health.is_healthy(20) is True

    stale = datetime.now(UTC) - timedelta(seconds=50)
    health.last_successful_poll_at = stale
    assert health.is_healthy(20) is False


def test_health_endpoint_includes_poll_fields() -> None:
    from api.main import app

    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "poll_interval_seconds" in body
    assert "last_poll_age_seconds" in body
    assert "poll_healthy" in body
    assert isinstance(body["poll_healthy"], bool)


@pytest.mark.asyncio
async def test_poll_once_continues_after_broker_failure() -> None:
    from api import poll_jobs

    poll_jobs.poll_health.record_success()

    default_account = {
        "id": "acct_default",
        "broker": "alpaca",
        "connection_id": "conn_alpaca",
        "broker_external_id": None,
        "enabled": True,
    }

    good_broker = MagicMock()
    good_broker.afetch_snapshot = AsyncMock(
        return_value=(MagicMock(id="schwab-acct"), [], [])
    )

    def mock_get_broker(name: str | None = None) -> MagicMock:
        if name == "alpaca":
            raise RuntimeError("broker down")
        return good_broker

    with (
        patch.object(poll_jobs, "list_accounts", return_value=[default_account]),
        patch.object(poll_jobs, "connection_is_configured", return_value=True),
        patch.object(poll_jobs, "get_broker_for_account", side_effect=RuntimeError("broker down")),
        patch.object(poll_jobs, "get_enabled_broker_names", return_value=["alpaca", "schwab"]),
        patch.object(poll_jobs, "broker_is_configured", return_value=True),
        patch.object(poll_jobs, "get_broker", side_effect=mock_get_broker),
        patch.object(poll_jobs, "save_snapshot", return_value="2026-01-01T00:00:00+00:00"),
        patch.object(poll_jobs, "get_latest_snapshot", return_value=None),
    ):
        await poll_jobs._poll_once()

    assert poll_jobs.poll_health.last_successful_poll_at is not None


@pytest.mark.asyncio
async def test_poll_loop_survives_iteration_exception() -> None:
    from api import poll_jobs

    iterations = 0

    async def flaky_poll() -> None:
        nonlocal iterations
        iterations += 1
        if iterations == 1:
            raise RuntimeError("database is locked")

    sleep_calls = 0

    async def stop_after_second_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError()

    with (
        patch.object(poll_jobs, "_poll_once", side_effect=flaky_poll),
        patch.object(poll_jobs.asyncio, "sleep", side_effect=stop_after_second_sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await poll_jobs._poll_loop()

    assert iterations >= 2


@pytest.mark.asyncio
async def test_alert_symbol_loop_survives_exception() -> None:
    from api import trade_jobs

    iterations = 0

    async def flaky_ensure(_symbols: set[str]) -> None:
        nonlocal iterations
        iterations += 1
        if iterations == 1:
            raise RuntimeError("hub unavailable")

    sleep_calls = 0

    async def stop_after_second_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError()

    with (
        patch.object(trade_jobs, "list_price_alerts", return_value=[]),
        patch.object(trade_jobs.market_hub, "ensure_symbols", side_effect=flaky_ensure),
        patch.object(trade_jobs.asyncio, "sleep", side_effect=stop_after_second_sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await trade_jobs._alert_symbol_loop()

    assert iterations >= 2
