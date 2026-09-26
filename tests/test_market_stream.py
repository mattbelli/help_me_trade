"""Alpaca SIP trade handler — condition filter + suspect-band guard.

Hermetic: no network; assert QuoteState / guard state / alert dispatch directly.
Pins the 2026-07-09 NVDA incident: away-from-market SIP prints must not touch
state.price, broadcasts, or phone alerts.
"""

from __future__ import annotations

import asyncio
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
async def test_off_market_odd_lot_dropped(hub: AlpacaMarketStreamHub) -> None:
    """AMAT 2026-09-23: a 9-share TRF odd lot 2% through the market sat inside
    the 3% round-lot band, fired a $479 alert and drew a phantom wick. Odd lots
    get the 1% band and never hold/corroborate; near-market odd lots still flow."""
    hub._quotes["AMAT"] = QuoteState(price=470.5, prev_close=470.0)
    hub._prev_prices["AMAT"] = 470.5
    alert = AsyncMock()
    hub._check_price_alerts = alert  # type: ignore[method-assign]

    await hub._on_trade(_trade_item("AMAT", 480.48, conditions=["@", "I"]))
    assert hub._quotes["AMAT"].price == pytest.approx(470.5)
    assert hub.bars.forming("AMAT") is None
    assert "AMAT" not in hub._pending_trade
    alert.assert_not_called()

    await hub._on_trade(_trade_item("AMAT", 470.6, conditions=["@", "I"]))
    assert hub._quotes["AMAT"].price == pytest.approx(470.6)


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


# ---------------------------------------------------------------------------
# Baseline seeding — prev_close must be the last COMPLETED session's close
# (the 07-23 NTSK −13.65% incident: overnight seed pinned prev_close two
# sessions back and every tick's change_pct inherited it)
# ---------------------------------------------------------------------------


def _daily_bar(session_date, close: float):
    from datetime import datetime as _dt

    from brokers.alpaca import StockBar
    from trading.market_hours import _ET

    t = _dt(session_date.year, session_date.month, session_date.day, tzinfo=_ET)
    return StockBar(
        time=t.isoformat(),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000.0,
    )


def _stub_bars_broker(monkeypatch, bars_by_symbol: dict) -> None:
    import brokers.market_stream as ms
    from brokers.alpaca import AlpacaAdapter

    broker = AsyncMock(spec=AlpacaAdapter)
    broker.aget_stock_bars_batch = AsyncMock(return_value=bars_by_symbol)
    monkeypatch.setattr(ms, "get_broker", lambda name: broker)
    monkeypatch.setattr(ms, "broker_is_configured", lambda name: True)


@pytest.mark.asyncio
async def test_baseline_overnight_series_prev_is_last_close(
    hub: AlpacaMarketStreamHub, monkeypatch
) -> None:
    """Series ending YESTERDAY: its last close IS the day-change baseline."""
    from datetime import datetime as _dt
    from datetime import timedelta

    import brokers.market_clock
    from trading.market_hours import _ET

    # Pre-open: a weekend run reads "closed", which keeps Friday's change (closes[-2]).
    monkeypatch.setattr(brokers.market_clock, "market_session", lambda: "pre")
    today = _dt.now(_ET).date()
    _stub_bars_broker(
        monkeypatch,
        {
            "NTSK": [
                _daily_bar(today - timedelta(days=2), 13.33),
                _daily_bar(today - timedelta(days=1), 11.955),
            ]
        },
    )
    assert await hub._ensure_baselines({"NTSK"}) is True
    state = hub._quotes["NTSK"]
    assert state.prev_close == 11.955
    assert state.change_pct == 0.0


@pytest.mark.asyncio
async def test_baseline_intra_session_series_prev_is_prior_close(
    hub: AlpacaMarketStreamHub, monkeypatch
) -> None:
    """Series ending TODAY (developing bar): prev = the close before it."""
    from datetime import datetime as _dt
    from datetime import timedelta

    from trading.market_hours import _ET

    today = _dt.now(_ET).date()
    _stub_bars_broker(
        monkeypatch,
        {
            "NTSK": [
                _daily_bar(today - timedelta(days=1), 11.955),
                _daily_bar(today, 11.58),
            ]
        },
    )
    assert await hub._ensure_baselines({"NTSK"}) is True
    state = hub._quotes["NTSK"]
    assert state.prev_close == 11.955
    assert state.change_pct == pytest.approx(-3.14, abs=0.01)


@pytest.mark.asyncio
async def test_baseline_force_reseeds_and_preserves_ext_fields(
    hub: AlpacaMarketStreamHub, monkeypatch
) -> None:
    from datetime import datetime as _dt
    from datetime import timedelta

    from trading.market_hours import _ET

    today = _dt.now(_ET).date()
    stale = QuoteState(price=11.955, prev_close=13.33, change_pct=-10.32)
    stale.ext_price = 11.6
    stale.market_session = "pre"
    hub._quotes["NTSK"] = stale
    _stub_bars_broker(
        monkeypatch, {"NTSK": [_daily_bar(today - timedelta(days=1), 11.955)]}
    )

    # Without force: existing state untouched.
    assert await hub._ensure_baselines({"NTSK"}) is True
    assert hub._quotes["NTSK"].prev_close == 13.33

    assert await hub._ensure_baselines({"NTSK"}, force=True) is True
    state = hub._quotes["NTSK"]
    assert state.prev_close == 11.955
    assert state.ext_price == 11.6
    assert state.market_session == "pre"


@pytest.mark.asyncio
async def test_baseline_roll_stamps_date_only_on_success(
    hub: AlpacaMarketStreamHub, monkeypatch
) -> None:
    from datetime import datetime as _dt
    from datetime import timedelta

    from trading.market_hours import _ET

    today = _dt.now(_ET).date()
    hub._quotes["NTSK"] = QuoteState(price=11.955, prev_close=13.33)
    hub._baseline_date = today - timedelta(days=1)

    calls: list[bool] = []

    async def failing(symbols, force=False):
        calls.append(force)
        return False

    monkeypatch.setattr(hub, "_ensure_baselines", failing)
    await hub._roll_baselines_if_new_day()
    assert calls == [True]
    assert hub._baseline_date == today - timedelta(days=1)  # failed → retry later

    async def succeeding(symbols, force=False):
        return True

    monkeypatch.setattr(hub, "_ensure_baselines", succeeding)
    await hub._roll_baselines_if_new_day()
    assert hub._baseline_date == today


# ---------------------------------------------------------------------------
# Minute-bar authority (card #325): admitted prints build the forming minute,
# Form-T prints feed it too, vendor b/u bars replace ours, frames go out.
# ---------------------------------------------------------------------------


class _FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_admitted_prints_form_minute_and_ride_quote_frame() -> None:
    hub = AlpacaMarketStreamHub()
    ws = _FakeWs()
    hub._clients[ws] = {"AAPL"}  # type: ignore[index]
    hub._quotes["AAPL"] = QuoteState(price=200.0, prev_close=200.0)
    t = "2026-08-17T15:46:00.100Z"
    await hub._on_trade({"S": "AAPL", "p": 201.0, "s": 100, "t": t})
    await hub._on_trade({"S": "AAPL", "p": 201.5, "s": 50, "t": "2026-08-17T15:46:30Z"})
    await asyncio.sleep(0.15)  # the 100ms trailing broadcast re-reads state
    quote_frames = [p for p in ws.sent if p["type"] == "quote"]
    assert quote_frames, "quote frame expected"
    bar = quote_frames[-1]["bar"]
    assert bar["timeframe"] == "1Min" and bar["final"] is False
    assert bar["open"] == 201.0 and bar["high"] == 201.5 and bar["close"] == 201.5
    assert bar["volume"] == 150 and bar["session"] == "regular"
    assert bar["time"] == 1_786_981_560_000 or bar["time"] % 60_000 == 0
    # Next minute's print seals 15:46 → a `bar` frame, opens 15:47 at 201.4
    await hub._on_trade({"S": "AAPL", "p": 201.4, "s": 10, "t": "2026-08-17T15:47:00.010Z"})
    sealed = [p for p in ws.sent if p["type"] == "bar"]
    assert len(sealed) == 1 and sealed[0]["close"] == 201.5 and sealed[0]["final"] is False
    forming = hub.bars.forming("AAPL")
    assert forming is not None and forming.open == 201.4


@pytest.mark.asyncio
async def test_form_t_print_feeds_bars_but_not_last_price() -> None:
    hub = AlpacaMarketStreamHub()
    hub._quotes["AAPL"] = QuoteState(price=200.0, prev_close=200.0)
    await hub._on_trade(
        {"S": "AAPL", "p": 202.0, "s": 5, "t": "2026-08-17T12:00:00Z", "c": ["T"]}
    )
    assert hub._quotes["AAPL"].price == 200.0  # last price untouched (pre-market)
    forming = hub.bars.forming("AAPL")
    assert forming is not None and forming.close == 202.0
    # Off-band Form-T junk is dropped; other excluded conditions never build.
    await hub._on_trade(
        {"S": "AAPL", "p": 300.0, "s": 5, "t": "2026-08-17T12:00:05Z", "c": ["T"]}
    )
    assert hub.bars.forming("AAPL").high == 202.0
    await hub._on_trade(
        {"S": "AAPL", "p": 203.0, "s": 5, "t": "2026-08-17T12:00:06Z", "c": ["T", "W"]}
    )
    assert hub.bars.forming("AAPL").high == 202.0


@pytest.mark.asyncio
async def test_vendor_minute_and_daily_bars_are_forwarded_final() -> None:
    hub = AlpacaMarketStreamHub()
    ws = _FakeWs()
    hub._clients[ws] = {"AAPL"}  # type: ignore[index]
    await hub._handle_alpaca_message(
        '[{"T":"b","S":"AAPL","o":1,"h":2,"l":0.5,"c":1.5,"v":9,"t":"2026-08-17T15:46:00Z"}]'
    )
    frame = ws.sent[-1]
    assert frame["type"] == "bar" and frame["final"] is True and frame["timeframe"] == "1Min"
    assert hub.bars.forming("AAPL").final is True
    await hub._handle_alpaca_message(
        '[{"T":"u","S":"AAPL","o":1,"h":2.5,"l":0.5,"c":1.6,"v":12,"t":"2026-08-17T15:46:00Z"}]'
    )
    assert hub.bars.forming("AAPL").high == 2.5 and ws.sent[-1]["high"] == 2.5
    await hub._handle_alpaca_message(
        '[{"T":"d","S":"AAPL","o":1,"h":3,"l":0.5,"c":2,"v":100,"t":"2026-08-17T04:00:00Z"}]'
    )
    daily = [p for p in ws.sent if p["type"] == "bar" and p["timeframe"] == "1Day"]
    assert len(daily) == 1 and daily[0]["close"] == 2 and daily[0]["final"] is True
    # Daily bars are NOT folded into the minute ring.
    assert [b.time for b in hub.bars.recent("AAPL")] == [hub.bars.forming("AAPL").time]


# ---------------------------------------------------------------------------
# Crypto hub: 24/7 UTC day reference + its own socket (never the equity proxy)
# ---------------------------------------------------------------------------


def _utc_daily_bar(day, close: float):
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    from brokers.alpaca import StockBar

    t = _dt(day.year, day.month, day.day, tzinfo=_tz.utc)
    return StockBar(
        time=t.isoformat().replace("+00:00", "Z"),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
    )


def test_crypto_prev_close_last_bar_is_today_utc_uses_prior_close() -> None:
    """The 00:00Z bar is still forming — yesterday's UTC close is the reference."""
    from datetime import datetime as _dt
    from datetime import timedelta
    from datetime import timezone as _tz

    from brokers.market_stream import _crypto_prev_close

    today = _dt.now(_tz.utc).date()
    bars = [
        _utc_daily_bar(today - timedelta(days=1), 60_000.0),
        _utc_daily_bar(today, 61_500.0),
    ]
    assert _crypto_prev_close(bars) == 60_000.0


def test_crypto_prev_close_series_ending_yesterday_uses_last_close() -> None:
    from datetime import datetime as _dt
    from datetime import timedelta
    from datetime import timezone as _tz

    from brokers.market_stream import _crypto_prev_close

    today = _dt.now(_tz.utc).date()
    bars = [
        _utc_daily_bar(today - timedelta(days=2), 59_000.0),
        _utc_daily_bar(today - timedelta(days=1), 60_000.0),
    ]
    assert _crypto_prev_close(bars) == 60_000.0


def test_crypto_prev_close_single_bar_is_its_own_reference() -> None:
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    from brokers.market_stream import _crypto_prev_close

    bars = [_utc_daily_bar(_dt.now(_tz.utc).date(), 61_500.0)]
    assert _crypto_prev_close(bars) == 61_500.0


def test_crypto_prev_close_is_not_the_et_session_rule(monkeypatch) -> None:
    """Weekend: `_daily_prev_close` holds Friday (closes[-2]); crypto trades
    through it, so the last completed UTC day is the reference."""
    from datetime import datetime as _dt
    from datetime import timedelta
    from datetime import timezone as _tz

    from brokers.market_stream import _crypto_prev_close, _daily_prev_close

    today = _dt.now(_tz.utc).date()
    bars = [
        _utc_daily_bar(today - timedelta(days=2), 59_000.0),
        _utc_daily_bar(today - timedelta(days=1), 60_000.0),
    ]
    assert _daily_prev_close(bars, session="closed") == 59_000.0
    assert _crypto_prev_close(bars) == 60_000.0


def test_crypto_hub_dials_the_crypto_socket_never_the_proxy(monkeypatch) -> None:
    from brokers.market_stream import CRYPTO_MARKET_WS_URL, AlpacaCryptoStreamHub

    monkeypatch.setenv("EQUITY_TAPE_EXTERNAL_COLLECTOR", "1")
    monkeypatch.setenv("EQUITY_TAPE_PROXY_URL", "ws://127.0.0.1:8767")
    hub = AlpacaCryptoStreamHub()
    assert hub._ws_url({"data_feed": "sip"}) == CRYPTO_MARKET_WS_URL
    assert CRYPTO_MARKET_WS_URL == "wss://stream.data.alpaca.markets/v1beta3/crypto/us"


def test_equity_hub_ws_url_still_honours_the_external_collector(monkeypatch) -> None:
    monkeypatch.setenv("EQUITY_TAPE_EXTERNAL_COLLECTOR", "1")
    monkeypatch.setenv("EQUITY_TAPE_PROXY_URL", "ws://127.0.0.1:9999")
    assert AlpacaMarketStreamHub()._ws_url({"data_feed": "sip"}) == "ws://127.0.0.1:9999"

    monkeypatch.setenv("EQUITY_TAPE_EXTERNAL_COLLECTOR", "0")
    assert (
        AlpacaMarketStreamHub()._ws_url({"data_feed": "sip"})
        == "wss://stream.data.alpaca.markets/v2/sip"
    )


def test_crypto_hub_skips_the_equity_tape_detector() -> None:
    """Crypto prints carry no condition codes or exchange, and the detector's
    thresholds are equity-shaped — `_external_tape` gates exactly that tap."""
    from brokers.market_stream import AlpacaCryptoStreamHub

    assert AlpacaCryptoStreamHub()._external_tape is True


@pytest.mark.asyncio
async def test_crypto_baseline_uses_crypto_bars_and_utc_prev_close(monkeypatch) -> None:
    from datetime import datetime as _dt
    from datetime import timedelta
    from datetime import timezone as _tz

    import brokers.market_stream as ms
    from brokers.alpaca import AlpacaAdapter
    from brokers.market_stream import AlpacaCryptoStreamHub

    today = _dt.now(_tz.utc).date()
    bars = [
        _utc_daily_bar(today - timedelta(days=1), 60_000.0),
        _utc_daily_bar(today, 61_200.0),
    ]
    broker = AsyncMock(spec=AlpacaAdapter)
    broker.aget_crypto_bars_batch = AsyncMock(return_value={"BTC/USD": bars})
    broker.aget_stock_bars_batch = AsyncMock(return_value={})
    monkeypatch.setattr(ms, "get_broker", lambda name: broker)
    monkeypatch.setattr(ms, "broker_is_configured", lambda name: True)

    hub = AlpacaCryptoStreamHub()
    assert await hub._ensure_baselines({"BTC/USD"}) is True
    broker.aget_stock_bars_batch.assert_not_awaited()
    assert broker.aget_crypto_bars_batch.await_args.kwargs == {
        "timeframe": "1Day",
        "days": 12,
    }
    state = hub._quotes["BTC/USD"]
    assert state.price == 61_200.0
    assert state.prev_close == 60_000.0
    assert state.change_pct == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_crypto_baseline_roll_uses_the_utc_date(monkeypatch) -> None:
    from datetime import datetime as _dt
    from datetime import timedelta
    from datetime import timezone as _tz

    from brokers.market_stream import AlpacaCryptoStreamHub

    hub = AlpacaCryptoStreamHub()
    assert hub._baseline_date == _dt.now(_tz.utc).date()

    seeded: list[bool] = []

    async def _seed(symbols, force=False):
        seeded.append(force)
        return True

    hub._ensure_baselines = _seed  # type: ignore[method-assign]
    hub._subscribed = {"BTC/USD"}

    await hub._roll_baselines_if_new_day()
    assert seeded == []  # same UTC day — no roll

    hub._baseline_date = _dt.now(_tz.utc).date() - timedelta(days=1)
    await hub._roll_baselines_if_new_day()
    assert seeded == [True]
    assert hub._baseline_date == _dt.now(_tz.utc).date()
