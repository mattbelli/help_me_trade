"""AlpacaAdapter.aplace_order — body shape and idempotency recovery."""

from __future__ import annotations

import pytest

from brokers.alpaca import AlpacaAdapter, AlpacaConflictError
from brokers.base import Order, PlaceOrderRequest


def _adapter() -> AlpacaAdapter:
    return AlpacaAdapter(
        base_url="https://paper-api.alpaca.markets",
        key_id="test-key",
        secret="test-secret",
    )


@pytest.mark.asyncio
async def test_aplace_order_posts_expected_body(monkeypatch) -> None:
    adapter = _adapter()
    captured: dict = {}

    async def fake_apost(path: str, *, body: dict):
        captured["path"] = path
        captured["body"] = body
        return {
            "id": "ord-123",
            "status": "accepted",
            "symbol": "AAPL",
            "qty": "10",
            "side": "buy",
            "type": "market",
        }

    monkeypatch.setattr(adapter, "_apost", fake_apost)

    req = PlaceOrderRequest(
        client_order_id="exec_abc123",
        symbol="AAPL",
        side="buy",
        qty="10",
        order_type="market",
        limit_price=None,
        time_in_force="day",
    )
    resp = await adapter.aplace_order(req)

    assert captured["path"] == "/v2/orders"
    body = captured["body"]
    assert body["client_order_id"] == "exec_abc123"
    assert body["type"] == "market"
    assert body["qty"] == "10"
    assert body["time_in_force"] == "day"
    assert "limit_price" not in body
    assert resp.broker_order_id == "ord-123"
    assert resp.status == "accepted"


@pytest.mark.asyncio
async def test_aplace_order_includes_limit_price(monkeypatch) -> None:
    adapter = _adapter()
    captured: dict = {}

    async def fake_apost(path: str, *, body: dict):
        captured["body"] = body
        return {"id": "ord-456", "status": "accepted"}

    monkeypatch.setattr(adapter, "_apost", fake_apost)

    req = PlaceOrderRequest(
        client_order_id="exec_limit",
        symbol="MSFT",
        side="sell",
        qty="5",
        order_type="limit",
        limit_price="420.50",
        time_in_force="day",
    )
    await adapter.aplace_order(req)

    assert captured["body"]["limit_price"] == "420.50"
    assert captured["body"]["type"] == "limit"


@pytest.mark.asyncio
async def test_aplace_order_includes_position_intent(monkeypatch) -> None:
    adapter = _adapter()
    captured: dict = {}

    async def fake_apost(path: str, *, body: dict):
        captured["body"] = body
        return {"id": "ord-789", "status": "accepted"}

    monkeypatch.setattr(adapter, "_apost", fake_apost)

    req = PlaceOrderRequest(
        client_order_id="exec_stc",
        symbol="AAPL",
        side="sell",
        qty="5",
        order_type="market",
        limit_price=None,
        time_in_force="day",
        position_intent="sell_to_close",
    )
    await adapter.aplace_order(req)

    assert captured["body"]["position_intent"] == "sell_to_close"


@pytest.mark.asyncio
async def test_aplace_order_422_recovers_existing_order(monkeypatch) -> None:
    adapter = _adapter()
    apost_calls = 0

    async def fake_apost(path: str, *, body: dict):
        nonlocal apost_calls
        apost_calls += 1
        raise AlpacaConflictError("duplicate client_order_id")

    async def fake_aget_order_by_client_id(client_order_id: str):
        assert client_order_id == "exec_dup"
        return Order(
            id="ord-existing",
            symbol="AAPL",
            qty=10.0,
            side="buy",
            order_type="market",
            status="new",
            filled_qty=0.0,
            limit_price=None,
        )

    monkeypatch.setattr(adapter, "_apost", fake_apost)
    monkeypatch.setattr(adapter, "aget_order_by_client_id", fake_aget_order_by_client_id)

    req = PlaceOrderRequest(
        client_order_id="exec_dup",
        symbol="AAPL",
        side="buy",
        qty="10",
        order_type="market",
        limit_price=None,
        time_in_force="day",
    )
    resp = await adapter.aplace_order(req)

    assert apost_calls == 1
    assert resp.broker_order_id == "ord-existing"
    assert resp.status == "new"
    assert resp.raw.get("recovered") is True


@pytest.mark.asyncio
async def test_aget_order_by_client_id_uses_query_params(monkeypatch) -> None:
    adapter = _adapter()
    captured: dict = {}

    async def fake_arequest(base: str, path: str, *, params=None):
        captured["base"] = base
        captured["path"] = path
        captured["params"] = params
        return [
            {
                "id": "ord-existing",
                "symbol": "AAPL",
                "qty": "10",
                "side": "buy",
                "type": "market",
                "status": "new",
                "filled_qty": "0",
            }
        ]

    monkeypatch.setattr(adapter, "_arequest", fake_arequest)

    order = await adapter.aget_order_by_client_id("exec_dup")

    assert captured["path"] == "/v2/orders"
    assert captured["params"] == {"client_order_ids": "exec_dup"}
    assert order is not None
    assert order.id == "ord-existing"


@pytest.mark.asyncio
async def test_apost_raises_conflict_on_422(monkeypatch) -> None:
    adapter = _adapter()

    class FakeResponse:
        status_code = 422
        text = "client_order_id already exists"

        @staticmethod
        def json():
            return {}

    class FakeClient:
        async def post(self, url, json):
            return FakeResponse()

    monkeypatch.setattr(adapter, "_async_http", lambda: FakeClient())

    with pytest.raises(AlpacaConflictError):
        await adapter._apost("/v2/orders", body={"symbol": "AAPL"})
