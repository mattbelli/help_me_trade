"""Equity tape collector — detectors, proxy registry, and the socket handoff.

Pins the 2026-07-15 measurements this build rests on: the stocks stream allows
one connection per account (so the hub must dial the local proxy when the
collector owns the socket), wildcards are ACKed on both channels, and 2s-stale
NBBOs misclassify often enough that direction past that age is "undetermined".
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import msgpack

from signals.equity_stream.constants import (
    NBBO_FRESH_MS,
    SWEEP_COOLDOWN_SECONDS,
    SWEEP_WINDOW_MS,
    VELOCITY_BUCKET_SECONDS,
)
from signals.equity_stream.detectors import (
    SpreadStats,
    SweepAggregator,
    VelocityDetector,
    side_vs_nbbo,
)
from signals.equity_stream.proxy import LocalMarketProxy, _Client
from signals.equity_stream.stream import EquityStream, to_json_msg, ts_to_iso, ts_to_ms
from signals.equity_tape import TapeDetector

_BUCKET_MS = VELOCITY_BUCKET_SECONDS * 1000.0


# --- direction vs NBBO ----------------------------------------------------------------


def test_side_vs_nbbo_at_and_through_the_quote():
    now = 1_000_000.0
    nbbo = (99.90, 100.00, now)
    assert side_vs_nbbo(100.00, nbbo, now) == ("buy_side", "nbbo_at_print")
    assert side_vs_nbbo(100.10, nbbo, now) == ("buy_side", "nbbo_at_print")
    assert side_vs_nbbo(99.90, nbbo, now) == ("sell_side", "nbbo_at_print")
    # inside the spread: undetermined, never leaned
    assert side_vs_nbbo(99.95, nbbo, now) == ("undetermined", "nbbo_at_print")


def test_side_vs_nbbo_refuses_stale_quotes():
    now = 1_000_000.0
    stale = (99.90, 100.00, now - NBBO_FRESH_MS - 1)
    assert side_vs_nbbo(100.00, stale, now) == ("undetermined", "stale_quote")
    assert side_vs_nbbo(100.00, None, now) == ("undetermined", "stale_quote")
    crossed = (100.00, 99.90, now)  # bid >= ask: junk book
    assert side_vs_nbbo(100.00, crossed, now) == ("undetermined", "stale_quote")


# --- sweeps ---------------------------------------------------------------------------


def _feed_sweep(agg, symbol, t, *, venues, price=100.0, size=2000):
    nbbo = (99.90, 100.00, t)
    for i, ven in enumerate(venues):
        closed = agg.on_iso_print(
            symbol=symbol,
            price=price,
            size=size,
            exchange=ven,
            ts_ms=t + i,
            nbbo=nbbo,
        )
        assert closed is None
    return agg.flush(t + SWEEP_WINDOW_MS + 1)


def test_sweep_clusters_child_prints_into_one_event():
    agg = SweepAggregator()
    events = _feed_sweep(agg, "ABC", 1_000_000.0, venues=["N", "P", "Z", "K", "N"])
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "equity_sweep"
    assert ev["notional_usd"] == 1_000_000.0  # 5 x 2000sh x $100
    assert ev["size"] == 10_000
    assert ev["direction"] == "buy_side"  # every print at the ask
    assert ev["direction_basis"] == "nbbo_at_print"
    assert ev["detail"]["venues"] == ["K", "N", "P", "Z"]
    assert ev["detail"]["prints"] == 5


def test_sweep_below_venue_or_notional_threshold_is_silent():
    agg = SweepAggregator()
    # 2 venues — a big print bouncing between two exchanges is not a sweep
    assert _feed_sweep(agg, "ABC", 1_000_000.0, venues=["N", "P"]) == []
    # 3 venues but only $30k total
    assert (
        _feed_sweep(agg, "DEF", 1_000_000.0, venues=["N", "P", "Z"], size=100) == []
    )


def test_sweep_cooldown_suppresses_repeats():
    agg = SweepAggregator()
    t = 1_000_000.0
    assert len(_feed_sweep(agg, "ABC", t, venues=["N", "P", "Z", "K"])) == 1
    # same working order re-sweeping 5s later: inside the 30s cooldown
    t2 = t + 5_000.0
    assert _feed_sweep(agg, "ABC", t2, venues=["N", "P", "Z", "K"]) == []
    assert agg.suppressed == 1
    t3 = t + SWEEP_COOLDOWN_SECONDS * 1000.0 + 1_000.0
    assert len(_feed_sweep(agg, "ABC", t3, venues=["N", "P", "Z", "K"])) == 1


# --- velocity -------------------------------------------------------------------------


def _warm(v: VelocityDetector, symbol: str, base: float, buckets: int = 20):
    for i in range(buckets):
        assert (
            v.on_print(
                symbol=symbol,
                price=10.0,
                size=100,
                ts_ms=base + i * _BUCKET_MS,
                signed_notional=1000.0,
            )
            is None
        )
    return base + buckets * _BUCKET_MS


def test_velocity_spike_fires_after_warmup():
    v = VelocityDetector()
    base = 1_000_000_000.0
    t0 = _warm(v, "X", base)
    for j in range(40):  # spike bucket: 40 prints vs ~0.64 baseline
        assert (
            v.on_print(
                symbol="X", price=10.0, size=100, ts_ms=t0 + j * 10.0,
                signed_notional=1000.0,
            )
            is None
        )
    ev = v.on_print(  # rollover closes + judges the spike bucket
        symbol="X", price=10.0, size=100, ts_ms=t0 + _BUCKET_MS, signed_notional=0.0
    )
    assert ev is not None
    assert ev["event_type"] == "tape_velocity"
    assert ev["detail"]["prints"] == 40
    assert ev["direction"] == "buy_side"  # every print classified as a buy
    assert "40 prints" in ev["reason"]


def test_velocity_never_fires_during_warmup():
    v = VelocityDetector()
    base = 1_000_000_000.0
    for j in range(50):  # huge burst but only 2 buckets of history
        v.on_print(
            symbol="X", price=10.0, size=100, ts_ms=base + j * 10.0,
            signed_notional=1000.0,
        )
    assert (
        v.on_print(
            symbol="X", price=10.0, size=100, ts_ms=base + _BUCKET_MS,
            signed_notional=0.0,
        )
        is None
    )


def test_velocity_two_sided_battle_is_mixed():
    v = VelocityDetector()
    base = 1_000_000_000.0
    t0 = _warm(v, "X", base)
    for j in range(40):
        v.on_print(
            symbol="X", price=10.0, size=100, ts_ms=t0 + j * 10.0,
            signed_notional=1000.0 if j % 2 else -1000.0,
        )
    ev = v.on_print(
        symbol="X", price=10.0, size=100, ts_ms=t0 + _BUCKET_MS, signed_notional=0.0
    )
    assert ev is not None
    assert ev["direction"] == "mixed"


def test_velocity_cooldown_blocks_the_next_bucket():
    v = VelocityDetector()
    base = 1_000_000_000.0
    t0 = _warm(v, "X", base)
    for j in range(40):
        v.on_print(
            symbol="X", price=10.0, size=100, ts_ms=t0 + j * 10.0,
            signed_notional=0.0,
        )
    assert (
        v.on_print(
            symbol="X", price=10.0, size=100, ts_ms=t0 + _BUCKET_MS,
            signed_notional=0.0,
        )
        is not None
    )
    # immediately spike again — cooldown (300s = 30 buckets) suppresses
    t1 = t0 + _BUCKET_MS
    for j in range(1, 40):
        v.on_print(
            symbol="X", price=10.0, size=100, ts_ms=t1 + j * 10.0,
            signed_notional=0.0,
        )
    assert (
        v.on_print(
            symbol="X", price=10.0, size=100, ts_ms=t1 + _BUCKET_MS,
            signed_notional=0.0,
        )
        is None
    )


# --- spread stats ---------------------------------------------------------------------


def test_spread_stats_tracks_wideners():
    s = SpreadStats()
    # sampled 1-in-16: feed enough quotes to build a baseline then widen
    for _ in range(16 * 20):
        s.on_quote("X", 99.99, 100.01)  # ~2bps
    for _ in range(16 * 2):
        s.on_quote("X", 99.80, 100.20)  # ~40bps: 20x the median
    top = s.top_wideners(3)
    assert top and top[0][0] == "X"
    assert top[0][1] > 5.0
    assert s.top_wideners(3) == []  # report resets the window


# --- tape detector early-out ----------------------------------------------------------


def test_sub_million_print_never_touches_the_profile_loader():
    calls: list[str] = []

    def loader(symbol: str) -> float:
        calls.append(symbol)
        return 1_000_000.0

    det = TapeDetector(median_shares_loader=loader)
    ev = det.on_print(
        symbol="X", price=10.0, size=50_000, exchange="N", conditions=[],
        ts="2026-07-15T14:00:00Z", trade_id=1, prev_price=10.0,
    )  # $500k
    assert ev is None and calls == []
    ev = det.on_print(
        symbol="X", price=10.0, size=1_500_000, exchange="D", conditions=[],
        ts="2026-07-15T14:00:01Z", trade_id=2, prev_price=10.0,
    )  # $15M dark-pool print
    assert ev is not None and ev["event_type"] == "dark_pool"
    assert calls == ["X"]


# --- timestamp conversion (msgpack ext type broke the first probe) ---------------------


def test_msgpack_timestamps_become_rfc3339():
    epoch = int(datetime(2026, 7, 15, 13, 30, tzinfo=timezone.utc).timestamp())
    ts = msgpack.Timestamp(epoch)
    assert ts_to_iso(ts).startswith("2026-07-15T13:30:00")
    assert ts_to_ms(ts) == epoch * 1000.0
    converted = to_json_msg({"T": "t", "S": "SPY", "p": 630.0, "t": ts})
    assert converted["t"].startswith("2026-07-15T13:30:00")
    assert converted["p"] == 630.0


# --- proxy registry -------------------------------------------------------------------


def test_proxy_subscriptions_fanout_and_daily_bar_callback():
    changes: list[set[str]] = []
    p = LocalMarketProxy(on_daily_bar_symbols_change=changes.append)
    ws = object()
    client = _Client()
    p._clients[ws] = client

    p._apply_subscription(
        client, "subscribe", {"trades": ["spy", "QQQ"], "dailyBars": ["SPY"]}
    )
    assert p.wants("trades", "SPY") and p.wants("trades", "QQQ")
    assert not p.wants("quotes", "SPY")
    assert changes[-1] == {"SPY"}

    p.publish("trades", "SPY", {"T": "t", "S": "SPY"})
    p.publish("trades", "TSLA", {"T": "t", "S": "TSLA"})  # nobody asked
    assert client.queue.qsize() == 1

    ack = p._ack(client)
    assert ack["T"] == "subscription"
    assert ack["trades"] == ["QQQ", "SPY"]

    p._apply_subscription(client, "unsubscribe", {"trades": ["SPY"]})
    assert not p.wants("trades", "SPY")
    assert p.wants("trades", "QQQ")


def test_proxy_drops_overflow_instead_of_blocking():
    p = LocalMarketProxy()
    ws = object()
    client = _Client()
    p._clients[ws] = client
    p._apply_subscription(client, "subscribe", {"trades": ["SPY"]})
    for _ in range(3000):
        p.publish("trades", "SPY", {"T": "t", "S": "SPY"})
    assert client.queue.full()
    assert client.drops == 3000 - client.queue.maxsize
    assert p.total_drops == client.drops


# --- the hub dials the proxy when the collector owns the socket ------------------------


def test_market_ws_url_points_at_proxy_when_flag_set(monkeypatch):
    from brokers.market_stream import alpaca_market_ws_url

    monkeypatch.setenv("EQUITY_TAPE_EXTERNAL_COLLECTOR", "1")
    assert alpaca_market_ws_url() == "ws://127.0.0.1:8767"
    monkeypatch.setenv("EQUITY_TAPE_PROXY_URL", "ws://127.0.0.1:9999")
    assert alpaca_market_ws_url() == "ws://127.0.0.1:9999"

    monkeypatch.delenv("EQUITY_TAPE_PROXY_URL")
    monkeypatch.setenv("EQUITY_TAPE_EXTERNAL_COLLECTOR", "0")
    assert alpaca_market_ws_url().startswith("wss://stream.data.alpaca.markets/v2/")


# --- review findings 2026-07-15 (adversarial pass on PR #81) ----------------------------


_TRADE = {
    "S": "SPY", "p": 630.0, "s": 500_000, "x": "D", "c": [],
    "t": "2026-07-15T14:00:00Z", "i": 1,
}


def test_hub_tape_tap_stands_down_under_external_collector(monkeypatch):
    """Under the flag, the hub's in-process detector tap must not run: its
    deque is never drained and the profile loader does blocking file I/O on
    the API event loop (HIGH review finding)."""
    from brokers.market_stream import AlpacaMarketStreamHub

    calls: list[dict] = []

    monkeypatch.setenv("EQUITY_TAPE_EXTERNAL_COLLECTOR", "1")
    hub = AlpacaMarketStreamHub()
    hub.tape_detector.on_print = lambda **kw: calls.append(kw)
    asyncio.run(hub._on_trade(dict(_TRADE)))
    assert calls == []

    monkeypatch.setenv("EQUITY_TAPE_EXTERNAL_COLLECTOR", "0")
    hub2 = AlpacaMarketStreamHub()
    hub2.tape_detector.on_print = lambda **kw: calls.append(kw)
    asyncio.run(hub2._on_trade(dict(_TRADE)))
    assert len(calls) == 1


def test_proxy_refuses_and_drops_clients_while_upstream_down():
    """A collector whose upstream auth fails must not serve a silent
    'connected' feed — the hub has to SEE the outage (MEDIUM-HIGH finding)."""

    sent: list[str] = []
    closed: list[bool] = []

    class FakeWS:
        async def send(self, data):
            sent.append(data)

        async def close(self):
            closed.append(True)

    async def scenario():
        p = LocalMarketProxy()
        assert not p.upstream_healthy  # boots unhealthy until first connect
        await p._handle(FakeWS())  # refused with an error frame, then closed
        assert "upstream not connected" in sent[0]
        assert closed == [True]

        p.set_upstream_healthy(True)
        ws2 = FakeWS()
        p._clients[ws2] = _Client()
        p.set_upstream_healthy(False)  # upstream died: drop everyone
        await asyncio.sleep(0)
        assert closed == [True, True]

    asyncio.run(scenario())


def test_dispatch_isolates_callback_errors():
    """One malformed message must never tear down the wildcard connection
    (MEDIUM finding) — the error is counted, the next message still flows."""
    quotes_seen: list[dict] = []

    def bad_trade(m, now_ms):
        raise ValueError("malformed print")

    s = EquityStream(
        key="k", secret="s",
        on_trade=bad_trade,
        on_quote=lambda m, now_ms: quotes_seen.append(m),
    )
    frame = msgpack.packb(
        [
            {"T": "t", "S": "SPY", "p": 630.0},
            {"T": "q", "S": "SPY", "bp": 629.9, "ap": 630.0},
        ]
    )
    s._dispatch(frame)  # must not raise
    assert s.callback_errors == 1
    assert len(quotes_seen) == 1


def test_runner_on_quote_guards_malformed_fields():
    from signals.equity_stream.runner import EquityTapeRunner

    r = EquityTapeRunner(key="k", secret="s")
    r._on_quote({"S": "SPY", "bp": "garbage", "ap": 630.0}, 1000.0)
    assert "SPY" not in r._nbbo
    r._on_quote({"S": "SPY", "bp": 629.9, "ap": 630.0, "t": None}, 1000.0)
    assert r._nbbo["SPY"] == (629.9, 630.0, 1000.0)
