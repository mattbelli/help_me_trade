"""Price-alert dispatch via unified notify() — persist, WS, and APNs push."""

from __future__ import annotations

import pytest

from brokers.market_stream import MarketStreamHub


@pytest.mark.asyncio
async def test_price_alert_calls_notify_with_kind_and_push(monkeypatch) -> None:
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

    notify_calls: list[dict] = []

    async def _fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "notif-test", **kwargs}

    monkeypatch.setattr("signals.notify.notify", _fake_notify)

    await hub._check_price_alerts("AAPL", prev=199.0, current=200.5)

    assert len(notify_calls) == 1
    call = notify_calls[0]
    assert call["kind"] == "price_alert"
    assert call.get("push", True) is True
    assert call["ref_type"] == "alert"
    assert call["ref_id"] == "42"
    assert call["data"]["symbol"] == "AAPL"
    assert call["data"]["alert_id"] == 42
    assert call["data"]["target_price"] == pytest.approx(200.0)
    assert call["data"]["trigger_price"] == pytest.approx(200.5)
    assert call["data"]["direction"] == "above"
    assert call["data"]["note"] == "breakout"
    assert "breakout" in call["body"]


@pytest.mark.asyncio
async def test_price_alert_notify_persists_and_broadcasts(monkeypatch) -> None:
    hub = MarketStreamHub()

    triggered_alert = {
        "id": 42,
        "symbol": "AAPL",
        "price": 200.0,
        "direction": "above",
        "note": "",
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
    monkeypatch.setattr("brokers.market_stream.market_hub", hub)

    await hub._check_price_alerts("AAPL", prev=199.0, current=200.5)

    recent = hub.recent_alerts()
    assert len(recent) == 1
    enriched = recent[0]
    assert enriched["type"] == "notification"
    assert enriched["kind"] == "price_alert"
    assert enriched["data"]["symbol"] == "AAPL"
    assert enriched["data"]["alert_id"] == 42
    assert enriched["id"]
    assert enriched["created_at"]

    assert len(pushes) == 1
    title, body, data = pushes[0]
    assert "Price alert" in title
    assert "AAPL" in body and "above" in body
    assert data["type"] == "notification"
    assert data["kind"] == "price_alert"
    assert data["symbol"] == "AAPL"
    assert data["alert_id"] == 42


@pytest.mark.asyncio
async def test_price_alert_no_triggers_no_notify(monkeypatch) -> None:
    hub = MarketStreamHub()
    monkeypatch.setattr(
        "api.alert_checker.check_alerts",
        lambda quotes, prev_quotes: [],
    )
    notify_calls = 0

    async def _fake_notify(**kwargs):
        nonlocal notify_calls
        notify_calls += 1
        return {}

    monkeypatch.setattr("signals.notify.notify", _fake_notify)

    await hub._check_price_alerts("AAPL", prev=199.0, current=200.5)
    assert notify_calls == 0
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
async def test_apply_extended_drives_price_alerts(monkeypatch) -> None:
    """Overnight: the ext-price path must evaluate price alerts (stream is silent.

    Two polls — the first only seeds prev_ext (no prior value → no check), the
    second moves the price → check_alerts runs against (prev, current) and a
    triggered alert fans out through notify().
    """
    hub = MarketStreamHub()

    calls: list[tuple[dict, dict]] = []

    def _fake_check(quotes, *, prev_quotes):
        calls.append((quotes, prev_quotes))
        return [{"id": 7, "symbol": "TSLA", "price": 383.0, "direction": "above"}]

    monkeypatch.setattr("api.alert_checker.check_alerts", _fake_check)

    async def _fake_push(title, body, **kw):
        return 1

    monkeypatch.setattr("signals.push.send_alert_push", _fake_push)
    monkeypatch.setattr("brokers.market_stream.market_hub", hub)

    # First poll: no prior ext value, so cross-detection can't run yet.
    await hub.apply_extended(
        "TSLA", ext_price=382.5, ext_change_pct=0.1,
        market_session="overnight", broadcast=False,
    )
    assert calls == []

    # Second poll: price moved → alert check runs with the right prev/current.
    await hub.apply_extended(
        "TSLA", ext_price=383.5, ext_change_pct=0.4,
        market_session="overnight", broadcast=False,
    )
    assert len(calls) == 1
    quotes, prev_quotes = calls[0]
    assert quotes == {"TSLA": pytest.approx(383.5)}
    assert prev_quotes == {"TSLA": pytest.approx(382.5)}

    recent = hub.recent_alerts()
    assert len(recent) == 1
    assert recent[0]["type"] == "notification"
    assert recent[0]["kind"] == "price_alert"
    assert recent[0]["data"]["symbol"] == "TSLA"


@pytest.mark.asyncio
async def test_apply_extended_no_alert_on_flat_or_first_price(monkeypatch) -> None:
    """No prior value, or an unchanged price, must NOT trigger an alert check."""
    hub = MarketStreamHub()

    calls = 0

    def _fake_check(quotes, *, prev_quotes):
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr("api.alert_checker.check_alerts", _fake_check)

    # First poll seeds prev_ext (no prior → skip); second is identical (no move → skip).
    await hub.apply_extended(
        "TSLA", ext_price=382.5, ext_change_pct=0.1,
        market_session="overnight", broadcast=False,
    )
    await hub.apply_extended(
        "TSLA", ext_price=382.5, ext_change_pct=0.1,
        market_session="overnight", broadcast=False,
    )
    assert calls == 0


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
