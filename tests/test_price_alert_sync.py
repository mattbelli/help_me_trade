"""Cross-client price-alert invalidation after REST mutations."""

from __future__ import annotations

import pytest
from fastapi import BackgroundTasks

from api import alert_drawing_routes as routes
from brokers.market_stream import MarketStreamHub


class _FakeWS:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.frames.append(payload)


@pytest.mark.asyncio
async def test_transient_event_reaches_all_clients_without_entering_alert_history() -> None:
    hub = MarketStreamHub()
    first = _FakeWS()
    second = _FakeWS()
    hub._clients[first] = set()  # type: ignore[index]
    hub._clients[second] = {"AMAT"}  # type: ignore[index]

    event = {
        "type": "price_alerts_changed",
        "action": "created",
        "alert_id": 109,
        "symbol": "AMAT",
    }
    await hub.broadcast_event(event)

    assert first.frames == [event]
    assert second.frames == [event]
    assert hub.recent_alerts() == []


@pytest.mark.asyncio
async def test_rest_mutations_queue_cross_client_invalidations(monkeypatch) -> None:
    row = {
        "id": 17,
        "symbol": "AMAT",
        "price": 450.76,
        "direction": "below",
        "note": "",
        "repeat": 0,
        "triggered": 0,
    }
    monkeypatch.setattr(routes, "create_price_alert", lambda **kwargs: row)
    monkeypatch.setattr(routes, "update_price_alert", lambda *args, **kwargs: row)
    monkeypatch.setattr(routes, "delete_price_alert", lambda alert_id: True)
    monkeypatch.setattr(routes, "list_price_alerts", lambda **kwargs: [row])
    monkeypatch.setattr(routes, "mark_alert_triggered", lambda *args, **kwargs: True)

    changes: list[tuple[str, int, str | None]] = []

    async def capture_change(
        action: str,
        *,
        alert_id: int,
        symbol: str | None = None,
    ) -> None:
        changes.append((action, alert_id, symbol))

    monkeypatch.setattr(routes, "broadcast_price_alert_change", capture_change)

    created_tasks = BackgroundTasks()
    assert (
        routes.api_create_alert(
            created_tasks,
            {"symbol": "AMAT", "price": 450.76, "direction": "below"},
        )
        == row
    )
    await created_tasks()

    updated_tasks = BackgroundTasks()
    assert routes.api_update_alert(17, updated_tasks, {"price": 451}) == row
    await updated_tasks()

    deleted_tasks = BackgroundTasks()
    assert routes.api_delete_alert(17, deleted_tasks) == {"ok": True}
    await deleted_tasks()

    triggered_tasks = BackgroundTasks()
    assert routes.api_trigger_alert(17, triggered_tasks)["ok"] is True
    await triggered_tasks()

    assert changes == [
        ("created", 17, "AMAT"),
        ("updated", 17, "AMAT"),
        ("deleted", 17, None),
        ("triggered", 17, "AMAT"),
    ]
