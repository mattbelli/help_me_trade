"""AlpacaAdapter.aplace_order — body shape and idempotency recovery."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from brokers.alpaca import AlpacaAdapter, AlpacaConflictError, AlpacaError
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


def _order_json(order_id: str, *, client_order_id: str, status: str = "new") -> dict:
    return {
        "id": order_id,
        "client_order_id": client_order_id,
        "symbol": "AAPL",
        "qty": "10",
        "side": "buy",
        "type": "market",
        "status": status,
        "filled_qty": "0",
    }


def _install_order_api(adapter, monkeypatch, *, open_orders, by_client_id) -> list[dict]:
    """Stand in for Alpaca's two order-read routes, as MEASURED 2026-08-02.

    The list route (`GET /v2/orders`) **ignores** a `client_order_ids` param —
    against the live paper API a total-garbage value returned the same four open
    orders as no filter at all — and defaults to open orders only. The singular
    route (`GET /v2/orders:by_client_order_id`) matches exactly and 404s when the
    broker has never seen the id.
    """
    seen: list[dict] = []

    class _FakeHttp:
        async def get(self, url: str, params=None):
            seen.append({"url": url, "params": params})
            if url.endswith("/v2/orders:by_client_order_id"):
                cid = (params or {}).get("client_order_id")
                if cid not in by_client_id:
                    return httpx.Response(
                        404, json={"code": 40410000, "message": f"order not found for {cid}"}
                    )
                return httpx.Response(200, json=by_client_id[cid])
            if url.endswith("/v2/orders"):
                return httpx.Response(200, json=list(open_orders))
            raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(adapter, "_async_http", lambda: _FakeHttp())
    return seen


@pytest.mark.asyncio
async def test_client_id_lookup_does_not_hand_back_an_unrelated_order(
    monkeypatch,
) -> None:
    """An execution the broker never saw must read as absent, not as some other order.

    The list route ignores the filter, so asking it returns whatever open order
    happens to be first — typically a resting protective stop on another symbol.
    Three callers act on that: the orphan sweep would bind our row to that
    stranger's broker id, the 409 recovery would report it as the placed order,
    and `_exit_may_be_live` would read it as "the exit is resting" and leave a
    position bare.
    """
    adapter = _adapter()
    _install_order_api(
        adapter,
        monkeypatch,
        open_orders=[_order_json("ord-someone-elses-stop", client_order_id="exec_other")],
        by_client_id={},
    )

    assert await adapter.aget_order_by_client_id("exec_never_placed") is None


@pytest.mark.asyncio
async def test_client_id_lookup_uses_the_singular_route(monkeypatch) -> None:
    adapter = _adapter()
    seen = _install_order_api(
        adapter,
        monkeypatch,
        open_orders=[],
        by_client_id={"exec_dup": _order_json("ord-existing", client_order_id="exec_dup")},
    )

    order = await adapter.aget_order_by_client_id("exec_dup")

    assert order is not None
    assert order.id == "ord-existing"
    assert seen[0]["url"].endswith("/v2/orders:by_client_order_id")
    assert seen[0]["params"] == {"client_order_id": "exec_dup"}


@pytest.mark.asyncio
async def test_client_id_lookup_finds_an_order_that_is_already_closed(
    monkeypatch,
) -> None:
    """The list route defaults to open orders; a filled order is invisible there.

    That direction is the dangerous one for the orphan sweep: a pending row whose
    order filled between the POST and the status write would read as "not found
    at broker" and be rejected on top of a real fill.
    """
    adapter = _adapter()
    _install_order_api(
        adapter,
        monkeypatch,
        open_orders=[],
        by_client_id={
            "exec_filled": _order_json(
                "ord-filled", client_order_id="exec_filled", status="filled"
            )
        },
    )

    order = await adapter.aget_order_by_client_id("exec_filled")

    assert order is not None
    assert order.status == "filled"


@pytest.mark.asyncio
async def test_client_id_lookup_raises_rather_than_reporting_absence(
    monkeypatch,
) -> None:
    """A broker-side failure must not read as "the order isn't there".

    `_exit_may_be_live` treats None as proof the exit never reached the broker and
    puts the protective stop back — on top of a live full-size sell if the lookup
    merely errored.
    """
    adapter = _adapter()

    class _FakeHttp:
        async def get(self, url: str, params=None):
            return httpx.Response(500, text="upstream unavailable")

    monkeypatch.setattr(adapter, "_async_http", lambda: _FakeHttp())

    with pytest.raises(AlpacaError):
        await adapter.aget_order_by_client_id("exec_dup")


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


@pytest.mark.asyncio
async def test_snapshot_reads_stay_concurrent(monkeypatch) -> None:
    """Pins the CONCURRENT snapshot against a well-meaning reorder.

    Serialising positions after the orders read looks like it buys an ordering
    invariant and does not (measured 2026-08-02: ~21 ms of probability shift).
    Worse, `_uncommitted_shares` compensates for positions being the OLDER read,
    so making it the fresher one double-counts the exit, zeroes the re-stop
    budget and leaves a position bare with no page. Move both or neither.
    """
    adapter = _adapter()
    in_flight = 0
    max_in_flight = 0

    async def fake_arequest(base: str, path: str, *, params=None):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        if path == "/v2/positions" or path.startswith("/v2/orders"):
            return []
        return {"id": "acct"}

    monkeypatch.setattr(adapter, "_arequest", fake_arequest)
    monkeypatch.setattr(adapter, "_parse_account", lambda _d: object())

    await adapter.afetch_snapshot()

    assert max_in_flight == 3


@pytest.mark.asyncio
async def test_open_order_read_is_not_silently_capped(monkeypatch, caplog) -> None:
    """RiskEngine derives pending_sell from this list, so a dropped resting sell
    reads as free shares. Alpaca defaults the list to 50 and truncates in
    silence; ask for its maximum and shout if we still hit it."""
    adapter = _adapter()
    captured: dict = {}

    async def fake_arequest(base: str, path: str, *, params=None):
        captured["path"] = path
        return [
            _order_json(f"ord-{i}", client_order_id=f"exec_{i}") for i in range(500)
        ]

    monkeypatch.setattr(adapter, "_arequest", fake_arequest)

    with caplog.at_level("ERROR"):
        orders = await adapter.aget_open_orders()

    assert "limit=500" in captured["path"]
    assert len(orders) == 500
    assert "hit the 500 cap" in caplog.text


@pytest.mark.asyncio
async def test_a_route_level_404_is_not_read_as_an_absent_order(monkeypatch) -> None:
    """Alpaca answers a missing ROUTE with the same 404 and the same numeric code
    as a missing order — only the message differs (measured). Reading "endpoint
    not found" as "the broker has never seen this order" would restore a
    protective stop on top of a live sell."""
    adapter = _adapter()

    class _FakeHttp:
        async def get(self, url: str, params=None):
            return httpx.Response(
                404, json={"code": 40410000, "message": "endpoint not found"}
            )

    monkeypatch.setattr(adapter, "_async_http", lambda: _FakeHttp())

    with pytest.raises(AlpacaError):
        await adapter.aget_order_by_client_id("exec_dup")
