"""Price-alert dispatch: flat WS shape + APNs push fan-out.

Locks in the contract the phone client + Mac agent rely on (HANDOFF NEXT items #2
and #3). Triggered chart-price alerts now broadcast as a FLAT alert (matching the
rule-alert shape) and ALSO fire an APNs push so a closed app still gets notified.
"""

from __future__ import annotations

import pytest

from brokers.market_stream import MarketStreamHub


@pytest.mark.asyncio
async def test_price_alert_broadcasts_flat_and_pushes(monkeypatch) -> None:
    hub = MarketStreamHub()

    triggered_alert = {
        "id": 42,
        "symbol": "AAPL",
        "price": 200.0,
        "direction": "above",
        "note": "breakout",
        "trigger_price": 200.5,
    }

    monkeypatch.setattr(
        "api.alert_checker.check_alerts",
        lambda quotes, prev_quotes: [triggered_alert],
    )

    pushes: list[tuple[str, str, dict]] = []

    async def _fake_push(title, body, *, data=None, **kw):
        pushes.append((title, body, data or {}))
        return 1

    monkeypatch.setattr("signals.push.send_alert_push", _fake_push)

    await hub._check_price_alerts("AAPL", prev=199.0, current=200.5)

    # WS broadcast: flat (no nested "alert": {...}), matches rule-alert shape.
    recent = hub.recent_alerts()
    assert len(recent) == 1
    enriched = recent[0]
    assert enriched["source"] == "price_alert"
    assert enriched["symbol"] == "AAPL"
    assert enriched["alert_id"] == 42
    assert enriched["target_price"] == pytest.approx(200.0)
    assert enriched["trigger_price"] == pytest.approx(200.5)
    assert enriched["direction"] == "above"
    assert enriched["note"] == "breakout"
    # broadcast_alert auto-stamps id+ts (same as rule alerts).
    assert enriched["id"]
    assert enriched["ts"]
    # No nested "alert" key — the bug we're fixing.
    assert "alert" not in enriched

    # APNs push fired (phone gets notified even with the app closed).
    assert len(pushes) == 1
    title, body, data = pushes[0]
    assert title == "Price alert"
    assert "AAPL" in body and "above" in body
    # Flat custom keys for tap-to-deep-link to the symbol detail screen.
    assert data["type"] == "price_alert"
    assert data["symbol"] == "AAPL"
    assert data["alert_id"] == 42


@pytest.mark.asyncio
async def test_price_alert_no_triggers_no_push(monkeypatch) -> None:
    hub = MarketStreamHub()
    monkeypatch.setattr(
        "api.alert_checker.check_alerts",
        lambda quotes, prev_quotes: [],
    )
    push_calls = 0

    async def _fake_push(title, body, **kw):
        nonlocal push_calls
        push_calls += 1
        return 1

    monkeypatch.setattr("signals.push.send_alert_push", _fake_push)

    await hub._check_price_alerts("AAPL", prev=199.0, current=200.5)
    assert push_calls == 0
    assert hub.recent_alerts() == []


class _FakeWS:
    """Minimal Starlette-like WS stand-in capturing all send_json calls."""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.frames.append(payload)


@pytest.mark.asyncio
async def test_broadcast_throttle_coalesces_hot_ticks(monkeypatch) -> None:
    """A burst of ticks for the same symbol collapses to 1 immediate + 1 trailing send.

    Locks in the ~10 Hz/symbol/client ceiling: first tick fires now, the next N
    ticks inside the window collapse into a single trailing broadcast that
    reflects the LATEST price.
    """
    import asyncio

    from brokers.market_stream import BROADCAST_THROTTLE_MS, QuoteState

    hub = MarketStreamHub()
    ws = _FakeWS()
    hub._clients[ws] = {"AAPL"}  # type: ignore[assignment]
    hub._quotes["AAPL"] = QuoteState(price=200.0, change_pct=0.0)

    # 10 rapid ticks (no awaits between them after the first send completes).
    for i in range(10):
        hub._quotes["AAPL"].price = 200.0 + i  # latest = 209.0
        await hub._broadcast_quote("AAPL")

    # Let any trailing task fire. Sleep > one throttle window.
    await asyncio.sleep(BROADCAST_THROTTLE_MS / 1000 * 2)

    # Exactly 2 frames: the immediate one + one trailing coalesced send.
    assert len(ws.frames) == 2, ws.frames
    # The trailing send carries the LATEST state (price=209), not a stale one.
    assert ws.frames[-1]["price"] == 209.0


@pytest.mark.asyncio
async def test_broadcast_throttle_idle_ticks_pass_through(monkeypatch) -> None:
    """Ticks spaced > throttle window apart all go straight through (no coalescing)."""
    import asyncio

    from brokers.market_stream import BROADCAST_THROTTLE_MS, QuoteState

    hub = MarketStreamHub()
    ws = _FakeWS()
    hub._clients[ws] = {"AAPL"}  # type: ignore[assignment]
    hub._quotes["AAPL"] = QuoteState(price=100.0, change_pct=0.0)

    for i in range(3):
        hub._quotes["AAPL"].price = 100.0 + i
        await hub._broadcast_quote("AAPL")
        await asyncio.sleep(BROADCAST_THROTTLE_MS / 1000 * 1.5)

    assert len(ws.frames) == 3
    assert [f["price"] for f in ws.frames] == [100.0, 101.0, 102.0]


@pytest.mark.asyncio
async def test_apply_extended_updates_state_and_payload() -> None:
    """REST poller writes ext fields → next WS broadcast carries them."""
    from brokers.market_stream import _quote_payload

    hub = MarketStreamHub()
    await hub.apply_extended(
        "TSLA",
        ext_price=382.50,
        ext_change_pct=0.42,
        market_session="overnight",
        broadcast=False,
    )
    state = hub._quotes["TSLA"]
    assert state.ext_price == pytest.approx(382.50)
    assert state.ext_change_pct == pytest.approx(0.42)
    assert state.market_session == "overnight"

    payload = _quote_payload(state)
    assert payload["ext_price"] == pytest.approx(382.50)
    assert payload["ext_change_pct"] == pytest.approx(0.42)
    assert payload["market_session"] == "overnight"
