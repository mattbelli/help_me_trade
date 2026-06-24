"""Recent alert ring buffer and /api/alerts/recent."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import market_hub
from brokers.market_stream import MarketStreamHub


@pytest.mark.asyncio
async def test_broadcast_alert_enriches_and_records() -> None:
    hub = MarketStreamHub()
    await hub.broadcast_alert(
        {"source": "rule", "rule_name": "X", "action": {"type": "alert"}}
    )
    alerts = hub.recent_alerts()
    assert len(alerts) == 1
    assert alerts[0]["rule_name"] == "X"
    assert alerts[0]["id"]
    assert alerts[0]["ts"]


@pytest.mark.asyncio
async def test_recent_alerts_ring_buffer_newest_first() -> None:
    hub = MarketStreamHub()
    for i in range(55):
        await hub.broadcast_alert({"source": "rule", "rule_name": f"r{i}"})
    alerts = hub.recent_alerts()
    assert len(alerts) == 50
    assert alerts[0]["rule_name"] == "r54"
    assert alerts[-1]["rule_name"] == "r5"


def test_api_alerts_recent_returns_buffer() -> None:
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        market_hub._recent_alerts.clear()
        market_hub._recent_alerts.append(
            {
                "id": "alert-test123",
                "ts": "2026-06-19T12:00:00+00:00",
                "source": "rule",
                "rule_name": "Test Rule",
                "action": {"type": "alert"},
            }
        )
        response = client.get("/api/alerts/recent")

    assert response.status_code == 200
    body = response.json()
    assert "alerts" in body
    ids = {item["id"] for item in body["alerts"]}
    assert "alert-test123" in ids
    match = next(a for a in body["alerts"] if a["id"] == "alert-test123")
    assert match["rule_name"] == "Test Rule"
