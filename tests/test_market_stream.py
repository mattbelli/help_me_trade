"""Alpaca SIP trade handler — condition filter + suspect-band guard.

Hermetic: no network; assert QuoteState / guard state / alert dispatch directly.
Pins the 2026-07-09 NVDA incident: away-from-market SIP prints must not touch
state.price, broadcasts, or phone alerts.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from brokers.market_stream import AlpacaMarketStreamHub, QuoteState


async def _noop_async(*args, **kwargs):
    return None


def _trade_item(symbol: str, price: float, *, conditions: list[str] | None = None) -> dict:
    item: dict = {"S": symbol, "p": price}
    if conditions is not None:
        item["c"] = conditions
    return item


@pytest.fixture
def hub(monkeypatch) -> AlpacaMarketStreamHub:
    h = AlpacaMarketStreamHub()
    monkeypatch.setattr(h, "_broadcast_quote", _noop_async)
    return h


# ---------------------------------------------------------------------------
# Condition filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_excluded_condition_leaves_state_untouched(hub: AlpacaMarketStreamHub) -> None:
    hub._quotes["NVDA"] = QuoteState(price=201.8, prev_close=200.0, sparkline=[201.8])
    hub._prev_prices["NVDA"] = 201.8
    alert = AsyncMock()
    hub._check_price_alerts = alert  # type: ignore[method-assign]
    broadcast = AsyncMock()
    hub._broadcast_quote = broadcast  # type: ignore[method-assign]

    await hub._on_trade(_trade_item("NVDA", 192.21, conditions=["B"]))

    assert hub._quotes["NVDA"].price == pytest.approx(201.8)
    assert hub._quotes["NVDA"].sparkline == [201.8]
    assert hub._prev_prices["NVDA"] == pytest.approx(201.8)
    alert.assert_not_called()
    broadcast.assert_not_called()


@pytest.mark.asyncio
async def test_missing_conditions_admits(hub: AlpacaMarketStreamHub) -> None:
    hub._quotes["AAPL"] = QuoteState(price=200.0, prev_close=200.0)
    hub._prev_prices["AAPL"] = 200.0

    await hub._on_trade(_trade_item("AAPL", 201.0))

    assert hub._quotes["AAPL"].price == pytest.approx(201.0)


@pytest.mark.asyncio
async def test_none_conditions_admits(hub: AlpacaMarketStreamHub) -> None:
    hub._quotes["AAPL"] = QuoteState(price=200.0, prev_close=200.0)
    hub._prev_prices["AAPL"] = 200.0

    await hub._on_trade({"S": "AAPL", "p": 201.0, "c": None})

    assert hub._quotes["AAPL"].price == pytest.approx(201.0)


@pytest.mark.asyncio
async def test_odd_lot_condition_admits(hub: AlpacaMarketStreamHub) -> None:
    hub._quotes["MSFT"] = QuoteState(price=400.0, prev_close=400.0)
    hub._prev_prices["MSFT"] = 400.0

    await hub._on_trade(_trade_item("MSFT", 401.0, conditions=["I"]))

    assert hub._quotes["MSFT"].price == pytest.approx(401.0)


# ---------------------------------------------------------------------------
# Suspect band + corroboration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_band_print_admits_and_fires_alert(monkeypatch) -> None:
    hub = AlpacaMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    hub._quotes["AAPL"] = QuoteState(price=200.0, prev_close=195.0)
    hub._prev_prices["AAPL"] = 200.0

    alert_calls: list[tuple[str, float, float]] = []

    async def _capture_alert(symbol: str, prev: float, current: float) -> None:
        alert_calls.append((symbol, prev, current))

    monkeypatch.setattr(hub, "_check_price_alerts", _capture_alert)

    await hub._on_trade(_trade_item("AAPL", 201.0))

    assert hub._quotes["AAPL"].price == pytest.approx(201.0)
    assert alert_calls == [("AAPL", 200.0, 201.0)]


@pytest.mark.asyncio
async def test_nvda_incident_print_is_held(caplog) -> None:
    """4.8% below market — the live-incident print must not touch state or alerts."""
    hub = AlpacaMarketStreamHub()
    hub._quotes["NVDA"] = QuoteState(price=201.8, prev_close=200.0, sparkline=[201.8])
    hub._prev_prices["NVDA"] = 201.8
    hub._check_price_alerts = AsyncMock()  # type: ignore[method-assign]
    hub._broadcast_quote = AsyncMock()  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING):
        await hub._on_trade(_trade_item("NVDA", 192.21))

    assert hub._quotes["NVDA"].price == pytest.approx(201.8)
    assert hub._prev_prices["NVDA"] == pytest.approx(201.8)
    assert hub._pending_trade["NVDA"] == pytest.approx(192.21)
    hub._check_price_alerts.assert_not_called()  # type: ignore[attr-defined]
    hub._broadcast_quote.assert_not_called()  # type: ignore[attr-defined]
    assert any(
        "suspect" in r.message.lower() and "NVDA" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_held_print_corroborated_on_second_print_admits(monkeypatch) -> None:
    hub = AlpacaMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    hub._quotes["NVDA"] = QuoteState(price=201.8, prev_close=200.0)
    hub._prev_prices["NVDA"] = 201.8

    alert_calls: list[tuple[str, float, float]] = []

    async def _capture_alert(symbol: str, prev: float, current: float) -> None:
        alert_calls.append((symbol, prev, current))

    monkeypatch.setattr(hub, "_check_price_alerts", _capture_alert)

    await hub._on_trade(_trade_item("NVDA", 192.21))  # held
    await hub._on_trade(_trade_item("NVDA", 192.30))  # corroborated → admit

    assert hub._quotes["NVDA"].price == pytest.approx(192.30)
    assert hub._prev_prices["NVDA"] == pytest.approx(192.30)
    assert "NVDA" not in hub._pending_trade
    # Swept-path evaluation (adversarial-review MAJOR #1): the corroborated held
    # print is real, so alerts see prev→held then held→current.
    assert alert_calls == [("NVDA", 201.8, 192.21), ("NVDA", 192.21, 192.30)]


@pytest.mark.asyncio
async def test_held_print_non_corroborating_next_discards_junk(monkeypatch) -> None:
    hub = AlpacaMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    hub._quotes["NVDA"] = QuoteState(price=201.8, prev_close=200.0)
    hub._prev_prices["NVDA"] = 201.8
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)

    await hub._on_trade(_trade_item("NVDA", 192.21))  # held junk
    await hub._on_trade(_trade_item("NVDA", 201.9))  # in-band vs ref → admit

    assert hub._quotes["NVDA"].price == pytest.approx(201.9)
    assert hub._prev_prices["NVDA"] == pytest.approx(201.9)
    assert "NVDA" not in hub._pending_trade


@pytest.mark.asyncio
async def test_abs_ceiling_blocks_corroborated_absurd_print(monkeypatch) -> None:
    hub = AlpacaMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)
    hub._quotes["WALK"] = QuoteState(price=100.0, prev_close=100.0)
    hub._prev_prices["WALK"] = 100.0

    await hub._on_trade(_trade_item("WALK", 1500.0))  # held (absurd)
    await hub._on_trade(_trade_item("WALK", 1510.0))  # corroborates — still held

    assert hub._quotes["WALK"].price == pytest.approx(100.0)
    assert hub._prev_prices["WALK"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_band_references_last_admitted_not_prev_close(monkeypatch) -> None:
    """After a re-center to 160, a 2.5% swing vs last-admitted admits even though it
    is 64% off prev_close."""
    hub = AlpacaMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)
    hub._quotes["GAP"] = QuoteState(price=160.0, prev_close=100.0)
    hub._prev_prices["GAP"] = 160.0

    await hub._on_trade(_trade_item("GAP", 164.0))

    assert hub._quotes["GAP"].price == pytest.approx(164.0)


@pytest.mark.asyncio
async def test_held_print_does_not_fire_alert(monkeypatch) -> None:
    """An alert must NOT fire from a held print's value."""
    hub = AlpacaMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    hub._quotes["NVDA"] = QuoteState(price=201.8, prev_close=200.0)
    hub._prev_prices["NVDA"] = 201.8

    triggered_alert = {
        "id": 7,
        "symbol": "NVDA",
        "price": 193.95,
        "direction": "cross",
        "note": "",
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

    await hub._on_trade(_trade_item("NVDA", 192.21))  # would cross $193.95 — held

    assert hub._quotes["NVDA"].price == pytest.approx(201.8)
    assert notify_calls == []


# ---------------------------------------------------------------------------
# Adversarial-review regressions (2026-07-09 FIX-FIRST findings)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corroborated_spike_alerts_over_swept_path(hub: AlpacaMarketStreamHub) -> None:
    """MAJOR #1: a real spike held one print then corroborated must still fire an
    alert whose level lies between prev and the held print."""
    hub._quotes["XYZ"] = QuoteState(price=203.0, prev_close=203.0)
    hub._prev_prices["XYZ"] = 203.0
    alert = AsyncMock()
    hub._check_price_alerts = alert  # type: ignore[method-assign]

    await hub._on_trade(_trade_item("XYZ", 210.0))  # >3% off ref → held
    assert hub._quotes["XYZ"].price == pytest.approx(203.0)
    alert.assert_not_called()

    await hub._on_trade(_trade_item("XYZ", 209.5))  # corroborates the held 210
    assert hub._quotes["XYZ"].price == pytest.approx(209.5)
    intervals = [(c.args[1], c.args[2]) for c in alert.await_args_list]
    # Swept path: prev→held then held→current — a $205 'above' alert sits inside
    # the first interval and would have been silently skipped before this fix.
    assert intervals == [(203.0, 210.0), (210.0, 209.5)]


@pytest.mark.asyncio
async def test_uncorroborated_spike_revert_stays_silent(hub: AlpacaMarketStreamHub) -> None:
    """The deliberate trade-off: a held print that is never corroborated (junk, or
    a one-print spike that fully reverts) must NOT reach alerts — that ambiguity
    is exactly the NVDA phantom-alert incident."""
    hub._quotes["XYZ"] = QuoteState(price=203.0, prev_close=203.0)
    hub._prev_prices["XYZ"] = 203.0
    alert = AsyncMock()
    hub._check_price_alerts = alert  # type: ignore[method-assign]

    await hub._on_trade(_trade_item("XYZ", 210.0))  # held
    await hub._on_trade(_trade_item("XYZ", 203.4))  # back in-band → junk discarded
    intervals = [(c.args[1], c.args[2]) for c in alert.await_args_list]
    assert intervals == [(203.0, 203.4)]  # the 210 never reaches alerts


@pytest.mark.asyncio
async def test_hold_streak_escape_hatch_admits(hub: AlpacaMarketStreamHub) -> None:
    """MAJOR #2: a genuine staircase whose consecutive prints never corroborate
    must not freeze the price forever — the Nth consecutive hold admits."""
    from brokers.market_stream import TRADE_HOLD_STREAK_MAX

    hub._quotes["THIN"] = QuoteState(price=100.0, prev_close=100.0)
    hub._prev_prices["THIN"] = 100.0

    # Consecutive prints each >3% off the stale ref and >5% apart from each other.
    stair = [110.0, 122.0, 136.0, 152.0, 170.0, 190.0]
    for px in stair[: TRADE_HOLD_STREAK_MAX - 1]:
        await hub._on_trade(_trade_item("THIN", px))
        assert hub._quotes["THIN"].price == pytest.approx(100.0)  # still held

    await hub._on_trade(_trade_item("THIN", stair[TRADE_HOLD_STREAK_MAX - 1]))
    # 5th consecutive hold → escape hatch admits and re-centers the band.
    assert hub._quotes["THIN"].price == pytest.approx(stair[TRADE_HOLD_STREAK_MAX - 1])
    assert hub._trade_hold_streak.get("THIN") is None
    # Follow-up in-band print flows normally against the re-centered ref.
    await hub._on_trade(_trade_item("THIN", 171.0))
    assert hub._quotes["THIN"].price == pytest.approx(171.0)


@pytest.mark.asyncio
async def test_absurd_print_never_escapes_via_streak(hub: AlpacaMarketStreamHub) -> None:
    """The 10x absolute ceiling holds unconditionally — streak never admits it."""
    from brokers.market_stream import TRADE_HOLD_STREAK_MAX

    hub._quotes["GLITCH"] = QuoteState(price=50.0, prev_close=50.0)
    hub._prev_prices["GLITCH"] = 50.0

    for _ in range(TRADE_HOLD_STREAK_MAX + 2):
        await hub._on_trade(_trade_item("GLITCH", 750.0))  # 15x prev_close
        assert hub._quotes["GLITCH"].price == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_absurd_burst_does_not_arm_escape_hatch(hub: AlpacaMarketStreamHub) -> None:
    """Re-review MAJOR: 4 absurd glitch prints must NOT arm the streak hatch so
    that a following first-appearance junk print (the NVDA incident class) walks
    in as hold #5. Absurd holds reset the streak."""
    from brokers.market_stream import TRADE_HOLD_STREAK_MAX

    hub._quotes["NVDA"] = QuoteState(price=201.8, prev_close=200.0)
    hub._prev_prices["NVDA"] = 201.8
    alert = AsyncMock()
    hub._check_price_alerts = alert  # type: ignore[method-assign]

    for _ in range(TRADE_HOLD_STREAK_MAX - 1):
        await hub._on_trade(_trade_item("NVDA", 5000.0))  # >10x prev_close
        assert hub._quotes["NVDA"].price == pytest.approx(201.8)
        assert hub._trade_hold_streak.get("NVDA") is None  # never armed

    await hub._on_trade(_trade_item("NVDA", 192.21))  # incident print, first appearance
    assert hub._quotes["NVDA"].price == pytest.approx(201.8)  # still held
    alert.assert_not_called()
