"""Schwab level-one stream handler — labeled-key contract.

Regression lock for the bug where `_on_equity`/`_on_futures` read content by the
numeric field `.value` (e.g. 3) while schwab-py relabels each content dict to the
field enum's MEMBER NAME ("LAST_PRICE") before invoking the handler. Reading
`.value` returned None for every field → every tick was silently dropped → the WS
never streamed and the phone fell back to the ~6-12s REST crawl.

These tests feed the SAME labeled shape schwab-py produces (member-name keys) and
assert the hub's QuoteState is populated — no network.
"""

from __future__ import annotations

import asyncio

import pytest

from brokers.market_stream import QuoteState
from brokers.schwab_market_stream import SchwabMarketStreamHub


@pytest.fixture(autouse=True)
def _regular_session(monkeypatch):
    """Pin the session gate to RTH so handler tests are deterministic regardless of
    wall clock; off-hours tests re-patch `market_session` explicitly."""
    monkeypatch.setattr(
        "brokers.schwab_market_stream.market_session", lambda: "regular"
    )


async def _noop_async(*args, **kwargs):
    return None


async def _drain(hub: SchwabMarketStreamHub) -> None:
    """Await the fire-and-forget broadcast/alert tasks _on_equity scheduled."""
    if hub._inflight_ticks:
        await asyncio.gather(*list(hub._inflight_ticks))


def test_account_activity_queues_accelerated_refresh(monkeypatch) -> None:
    hub = SchwabMarketStreamHub()
    hub._activity_account_hashes = {"12345678": "hash-1"}
    calls: list[set[str] | None] = []
    monkeypatch.setattr(
        "trading.position_trigger.observe_account_activity",
        lambda hashes=None: calls.append(hashes),
    )

    hub._on_account_activity(
        {
            "service": "ACCT_ACTIVITY",
            "content": [{"ACCOUNT": "12345678", "MESSAGE_TYPE": "OrderFill"}],
        }
    )
    assert calls == [{"hash-1"}]


def test_unmapped_account_activity_falls_back_to_all_accounts(monkeypatch) -> None:
    hub = SchwabMarketStreamHub()
    calls: list[set[str] | None] = []
    monkeypatch.setattr(
        "trading.position_trigger.observe_account_activity",
        lambda hashes=None: calls.append(hashes),
    )

    hub._on_account_activity(
        {"service": "ACCT_ACTIVITY", "content": [{"ACCOUNT": "unknown"}]}
    )
    assert calls == [None]


def test_empty_account_activity_does_not_queue_refresh(monkeypatch) -> None:
    hub = SchwabMarketStreamHub()
    calls: list[bool] = []
    monkeypatch.setattr(
        "trading.position_trigger.observe_account_activity",
        lambda hashes=None: calls.append(True),
    )

    hub._on_account_activity({"service": "ACCT_ACTIVITY", "content": []})
    assert calls == []


@pytest.mark.asyncio
async def test_account_activity_subscription_is_fail_soft(caplog) -> None:
    class BrokenStream:
        async def account_activity_sub(self):
            raise RuntimeError("not entitled")

    hub = SchwabMarketStreamHub()
    assert await hub._subscribe_account_activity(BrokenStream()) is False
    assert "position/hourly ledger refresh remains active" in caplog.text


@pytest.mark.asyncio
async def test_stream_client_builds_authoritative_account_routing(monkeypatch) -> None:
    from brokers import schwab_market_stream as stream_mod

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> list[dict[str, str]]:
            return [
                {"accountNumber": "12345678", "hashValue": "hash-1"},
                {"accountNumber": "87654321", "hashValue": "hash-2"},
            ]

    class Client:
        def get_account_numbers(self) -> Response:
            return Response()

    client = Client()
    monkeypatch.setattr("brokers.schwab_auth.create_client", lambda **kwargs: client)
    monkeypatch.setattr(stream_mod, "StreamClient", lambda got: ("stream", got))

    stream, mapping = await stream_mod._build_stream_client()
    assert stream == ("stream", client)
    assert mapping == {"12345678": "hash-1", "87654321": "hash-2"}


@pytest.mark.asyncio
async def test_futures_only_pending_add_still_subscribes() -> None:
    class RecordingStream:
        def __init__(self) -> None:
            self.futures: list[str] = []

        async def level_one_futures_add(self, symbols, *, fields=None):
            self.futures.extend(symbols)

        async def chart_futures_add(self, symbols):
            self.charts = list(symbols)

    hub = SchwabMarketStreamHub()
    hub._pending_add.add("/MNQ")
    stream = RecordingStream()
    await hub._apply_stream_changes(stream)
    assert stream.futures == ["/MNQ"]
    assert stream.charts == ["/MNQ"]


def _labeled_equity_msg() -> dict:
    # Keys are enum MEMBER NAMES — exactly what schwab-py's relabel_message emits.
    return {
        "service": "LEVELONE_EQUITIES",
        "content": [
            {
                "key": "AAPL",
                "LAST_PRICE": 200.5,
                "BID_PRICE": 200.45,
                "ASK_PRICE": 200.55,
                "TOTAL_VOLUME": 1_234_000,
                "NET_CHANGE_PERCENT": 1.25,
                "CLOSE_PRICE": 198.0,
            }
        ],
    }


@pytest.mark.asyncio
async def test_on_equity_reads_labeled_keys() -> None:
    hub = SchwabMarketStreamHub()
    hub._on_equity(_labeled_equity_msg())

    state = hub._quotes.get("AAPL")
    assert state is not None, "tick was dropped — handler read the wrong keys"
    assert state.price == pytest.approx(200.5)
    assert state.bid == pytest.approx(200.45)
    assert state.ask == pytest.approx(200.55)
    assert state.volume == pytest.approx(1_234_000)
    assert state.change_pct == pytest.approx(1.25)
    assert state.prev_close == pytest.approx(198.0)


@pytest.mark.asyncio
async def test_on_equity_falls_back_to_mark_when_no_last() -> None:
    hub = SchwabMarketStreamHub()
    hub._on_equity(
        {"content": [{"key": "MSFT", "MARK": 410.25, "CLOSE_PRICE": 409.0}]}
    )
    state = hub._quotes.get("MSFT")
    assert state is not None
    assert state.price == pytest.approx(410.25)


@pytest.mark.asyncio
async def test_on_equity_drops_implausible_tick() -> None:
    hub = SchwabMarketStreamHub()
    # A good tick establishes the prior close (198) + last price (200.5).
    hub._on_equity(_labeled_equity_msg())
    assert hub._quotes["AAPL"].price == pytest.approx(200.5)

    # A bad print far above the prior close (a ~$198 stock "printing" $750) is
    # dropped — the last good price stays, so it can't spike a chart candle.
    hub._on_equity({"content": [{"key": "AAPL", "LAST_PRICE": 750.0}]})
    assert hub._quotes["AAPL"].price == pytest.approx(200.5)

    # The reference (prior close) isn't poisoned, so the next good tick updates.
    hub._on_equity({"content": [{"key": "AAPL", "LAST_PRICE": 201.25}]})
    assert hub._quotes["AAPL"].price == pytest.approx(201.25)


@pytest.mark.asyncio
async def test_on_equity_skips_tick_with_no_price() -> None:
    """A partial tick (bid/ask only, no last/mark) is skipped — not a zero quote."""
    hub = SchwabMarketStreamHub()
    hub._on_equity({"content": [{"key": "TSLA", "BID_PRICE": 250.0}]})
    assert "TSLA" not in hub._quotes


@pytest.mark.asyncio
async def test_index_tick_moves_the_live_candle_equity_does_not() -> None:
    """$SPX has no SIP trade stream — its level-one last must feed the minute-bar
    builder (it sat frozen at the last REST bar). Equities keep their SIP prints."""
    hub = SchwabMarketStreamHub()
    hub._on_equity({"content": [{"key": "$SPX", "LAST_PRICE": 7713.66, "CLOSE_PRICE": 7764.64}]})
    hub._on_equity(_labeled_equity_msg())
    assert hub.bars.forming("$SPX").close == pytest.approx(7713.66)
    assert hub.bars.forming("AAPL") is None
    await _drain(hub)


@pytest.mark.asyncio
async def test_on_futures_reads_labeled_keys() -> None:
    hub = SchwabMarketStreamHub()
    hub._on_futures(
        {
            "content": [
                {
                    "key": "/ES",
                    "LAST_PRICE": 5500.25,
                    "FUTURE_CHANGE_PERCENT": -0.42,
                    "CLOSE_PRICE": 5523.5,
                    "TOTAL_VOLUME": 88_000,
                }
            ]
        }
    )
    state = hub._quotes.get("/ES")
    assert state is not None
    assert state.price == pytest.approx(5500.25)
    assert state.change_pct == pytest.approx(-0.42)
    assert state.prev_close == pytest.approx(5523.5)


@pytest.mark.asyncio
async def test_post_tick_gates_equity_alerts_to_rth(monkeypatch) -> None:
    """Equity stream alerts fire only during RTH — pre/post the stream's LAST_PRICE is
    the frozen regular-session last, which phantom-crosses against the live extended
    price. Off-hours equity alerts come from the REST ext poll instead. Futures trade
    ~23h with a single live last, so their stream alerts always apply."""
    hub = SchwabMarketStreamHub()
    calls: list[tuple[str, float, float]] = []

    async def _fake_check(sym, prev, current):
        calls.append((sym, prev, current))

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(hub, "_check_price_alerts", _fake_check)
    monkeypatch.setattr(hub, "_broadcast_quote", _noop)

    # Off-hours: equity alert suppressed; futures alert still fires.
    monkeypatch.setattr("brokers.schwab_market_stream.is_market_open", lambda: False)
    await hub._post_tick("SPY", 733.0, 728.99)
    await hub._post_tick("/ES", 7400.0, 7380.0)
    assert calls == [("/ES", 7400.0, 7380.0)]

    # RTH: equity alert fires.
    calls.clear()
    monkeypatch.setattr("brokers.schwab_market_stream.is_market_open", lambda: True)
    await hub._post_tick("SPY", 733.0, 728.99)
    assert calls == [("SPY", 733.0, 728.99)]


# ---------------------------------------------------------------------------
# Session gate: outside RTH the equity stream must freeze the regular price and
# route ticks to the extended fields (bug: price + ext_price moved in lockstep
# post-4pm). Patch the session function — never the wall clock.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regular_tick_drives_price_leaves_ext_untouched() -> None:
    """RTH behavior pinned: tick updates price/change_pct/sparkline, not ext."""
    hub = SchwabMarketStreamHub()
    hub._on_equity(_labeled_equity_msg())
    state = hub._quotes["AAPL"]
    assert state.price == pytest.approx(200.5)
    assert state.change_pct == pytest.approx(1.25)
    assert state.sparkline == [pytest.approx(200.5)]
    assert state.ext_price is None
    assert state.market_session is None


@pytest.mark.asyncio
async def test_post_session_freezes_price_routes_to_ext(monkeypatch) -> None:
    """Post-market tick leaves price/change_pct/sparkline frozen and fills the ext
    fields (ext_change_pct measured vs prev_close — the day reference, ToS
    "Mark % change" style — so the day move + AH move read as one number)."""
    monkeypatch.setattr(
        "brokers.schwab_market_stream.market_session", lambda: "post"
    )
    hub = SchwabMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)
    # Frozen regular close from the last RTH tick.
    hub._quotes["AAPL"] = QuoteState(
        price=198.0, change_pct=0.5, sparkline=[197.0, 198.0], prev_close=196.0
    )
    hub._on_equity({"content": [{"key": "AAPL", "LAST_PRICE": 200.5}]})
    await _drain(hub)
    state = hub._quotes["AAPL"]
    # Regular fields frozen.
    assert state.price == pytest.approx(198.0)
    assert state.change_pct == pytest.approx(0.5)
    assert state.sparkline == [197.0, 198.0]
    # Ext fields updated: (200.5 - 196.0) / 196.0 * 100 = 2.30% (vs prev_close,
    # NOT vs the 198 regular close — that would read "since the bell").
    assert state.ext_price == pytest.approx(200.5)
    assert state.ext_change_pct == pytest.approx(2.30)
    assert state.market_session == "post"


@pytest.mark.asyncio
async def test_ext_change_pct_falls_back_to_frozen_price(monkeypatch) -> None:
    """With no prev_close seeded yet, ext_change_pct measures vs the frozen
    regular price rather than going blank."""
    monkeypatch.setattr(
        "brokers.schwab_market_stream.market_session", lambda: "overnight"
    )
    hub = SchwabMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)
    hub._quotes["MSFT"] = QuoteState(price=400.0, prev_close=0.0)
    hub._on_equity({"content": [{"key": "MSFT", "LAST_PRICE": 404.0}]})
    await _drain(hub)
    state = hub._quotes["MSFT"]
    assert state.price == pytest.approx(400.0)  # still frozen
    assert state.ext_price == pytest.approx(404.0)
    assert state.ext_change_pct == pytest.approx(1.0)  # (404-400)/400*100
    assert state.market_session == "overnight"


@pytest.mark.asyncio
async def test_ext_alert_fires_and_dedups_with_rest_poll(monkeypatch) -> None:
    """Post-market ext move fires a price alert; a REST apply_extended poll of the
    SAME price does NOT double-fire (structural dedup via shared state.ext_price)."""
    monkeypatch.setattr(
        "brokers.schwab_market_stream.market_session", lambda: "post"
    )
    hub = SchwabMarketStreamHub()
    calls: list[tuple[str, float, float]] = []

    async def _fake_check(sym, prev, current):
        calls.append((sym, prev, current))

    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    monkeypatch.setattr(hub, "_check_price_alerts", _fake_check)
    hub._quotes["SPY"] = QuoteState(price=730.0, prev_close=728.0, ext_price=730.0)

    # Stream ext tick 730 -> 731.5 fires against the prior ext value.
    hub._on_equity({"content": [{"key": "SPY", "LAST_PRICE": 731.5}]})
    await _drain(hub)
    assert calls == [("SPY", 730.0, 731.5)]

    # REST poll applies the SAME 731.5: prev_ext == ext_price now, so no re-fire.
    calls.clear()
    await hub.apply_extended(
        "SPY", ext_price=731.5, ext_change_pct=0.48, market_session="post"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_futures_volume_only_update_opens_minute_at_unchanged_last(monkeypatch) -> None:
    """Level-one sends changed fields only: a trade at the unchanged last arrives
    as TOTAL_VOLUME alone. The new minute must open THERE, not at the first
    changed price seconds later (measured 2026-10-27, /ES 11:03)."""
    clock = [1_000_000_079.9]
    monkeypatch.setattr("brokers.market_stream.time.time", lambda: clock[0])
    hub = SchwabMarketStreamHub()
    hub._on_futures({"content": [{"key": "/ES", "LAST_PRICE": 7753.75, "TOTAL_VOLUME": 100}]})
    clock[0] = 1_000_000_081.0  # next minute: same price, volume moved
    hub._on_futures({"content": [{"key": "/ES", "TOTAL_VOLUME": 105}]})
    clock[0] = 1_000_000_084.0
    hub._on_futures({"content": [{"key": "/ES", "LAST_PRICE": 7754.25, "TOTAL_VOLUME": 110}]})
    bar = hub.bars.forming("/ES")
    assert bar.time == 1_000_000_080_000  # minute-aligned
    assert bar.open == pytest.approx(7753.75)
    assert bar.close == pytest.approx(7754.25)
    # Volume-only with no live last yet (baseline price only) folds nothing.
    hub._quotes["/NQ"] = QuoteState(price=27000.0)
    hub._on_futures({"content": [{"key": "/NQ", "TOTAL_VOLUME": 5}]})
    assert hub.bars.forming("/NQ") is None
    await _drain(hub)


@pytest.mark.asyncio
async def test_chart_futures_candle_seals_our_minute() -> None:
    """Schwab's CHART_FUTURES minute replaces our level-one copy, final."""
    hub = SchwabMarketStreamHub()
    hub._on_futures({"content": [{"key": "/ES", "LAST_PRICE": 7756.5}]})
    t = hub.bars.forming("/ES").time
    hub._on_chart_futures(
        {
            "content": [
                {
                    "key": "/ES",
                    "CHART_TIME_MILLIS": t,
                    "OPEN_PRICE": 7757,
                    "HIGH_PRICE": 7757.75,
                    "LOW_PRICE": 7754.5,
                    "CLOSE_PRICE": 7755,
                    "VOLUME": 3351,
                }
            ]
        }
    )
    await _drain(hub)
    bar = hub.bars.forming("/ES")
    assert bar.final
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        7757.0, 7757.75, 7754.5, 7755.0, 3351.0
    )
