"""Server-side minute-bar authority (card #325): print folding, vendor bar
adoption, aggregation, and the /api/bars tail splice."""

from __future__ import annotations

from brokers.bar_builder import (
    Bar,
    MinuteBarBuilder,
    aggregate,
    bar_payload,
    bar_session,
    parse_ts_ms,
    splice_vendor_tail,
)

# 2026-08-17 15:46:00Z = 11:46 ET, regular session
T0 = parse_ts_ms("2026-08-17T15:46:00Z")
MIN = 60_000


def test_parse_ts_ms_handles_alpaca_precisions():
    assert parse_ts_ms("2026-08-17T15:46:00Z") == T0
    assert parse_ts_ms("2026-08-17T15:46:00.123456789Z") == T0 + 123
    assert parse_ts_ms("2026-08-17T15:46:00.5+00:00") == T0 + 500
    assert parse_ts_ms("2026-08-17T11:46:00-04:00") == T0
    assert parse_ts_ms(None) is None
    assert parse_ts_ms("garbage") is None


def test_bar_session_by_et_clock():
    assert bar_session(T0) == "regular"
    assert bar_session(parse_ts_ms("2026-08-17T12:00:00Z")) == "pre"  # 08:00 ET
    assert bar_session(parse_ts_ms("2026-08-17T21:00:00Z")) == "post"  # 17:00 ET
    assert bar_session(parse_ts_ms("2026-08-18T02:00:00Z")) == "overnight"
    assert bar_session(parse_ts_ms("2026-08-15T15:00:00Z")) == "overnight"  # Sat


def test_prints_bucket_by_their_own_timestamp_and_roll():
    b = MinuteBarBuilder()
    forming, done = b.on_print("SPY", 100.0, 10, T0 + 5_000)
    assert done is None and forming.time == T0
    assert (forming.open, forming.high, forming.low, forming.close, forming.volume) == (
        100.0, 100.0, 100.0, 100.0, 10,
    )
    forming, done = b.on_print("SPY", 101.0, 5, T0 + 30_000)
    assert done is None and forming.high == 101.0 and forming.volume == 15
    forming, done = b.on_print("SPY", 99.0, 1, T0 + 59_999)
    assert forming.low == 99.0 and forming.close == 99.0
    # Next minute: the closed bar comes back as `completed`; the new forming
    # bar opens at the FIRST PRINT of its own minute (not at prev.close).
    forming, done = b.on_print("SPY", 99.5, 2, T0 + MIN + 100)
    assert done is not None and done.time == T0 and done.close == 99.0
    assert forming.time == T0 + MIN and forming.open == 99.5
    assert [x.time for x in b.recent("SPY")] == [T0, T0 + MIN]


def test_late_print_updates_kept_minute_but_not_a_vendor_sealed_one():
    b = MinuteBarBuilder()
    b.on_print("SPY", 100.0, 1, T0)
    b.on_print("SPY", 100.0, 1, T0 + MIN)
    forming, done = b.on_print("SPY", 102.0, 3, T0 + 10)  # late, for T0
    assert forming is None and done is not None and done.time == T0
    assert done.high == 102.0 and done.volume == 4
    b.on_vendor_bar("SPY", Bar(T0, 100.0, 100.5, 99.5, 100.2, 50))
    forming, done = b.on_print("SPY", 200.0, 3, T0 + 20)
    assert forming is None and done is None  # sealed: vendor owns it now
    assert b.recent("SPY")[0].high == 100.5


def test_vendor_bar_replaces_forming_ahead_or_in_ring():
    b = MinuteBarBuilder()
    b.on_print("SPY", 100.0, 1, T0 + 1000)
    # Vendor minute for the current bucket lands (no new-minute print yet).
    v = b.on_vendor_bar("SPY", Bar(T0, 99.9, 100.4, 99.8, 100.1, 500))
    assert v.final and b.forming("SPY") is v
    # A print for the NEXT minute rolls past it normally.
    forming, done = b.on_print("SPY", 100.2, 1, T0 + MIN)
    assert done is v and forming.time == T0 + MIN
    # Vendor ahead of any print (fresh subscription): becomes the tail.
    b2 = MinuteBarBuilder()
    v2 = b2.on_vendor_bar("QQQ", Bar(T0, 1, 2, 0.5, 1.5, 9))
    assert b2.recent("QQQ") == [v2]
    # Vendor for an older kept minute replaces in place.
    b.on_vendor_bar("SPY", Bar(T0, 1, 1, 1, 1, 1))
    assert b.recent("SPY")[0].close == 1


def test_recent_since_and_ring_bound():
    b = MinuteBarBuilder(keep=3)
    for i in range(6):
        b.on_print("SPY", 100 + i, 1, T0 + i * MIN)
    times = [x.time for x in b.recent("SPY")]
    assert times == [T0 + 2 * MIN, T0 + 3 * MIN, T0 + 4 * MIN, T0 + 5 * MIN]
    assert [x.time for x in b.recent("SPY", since_ms=T0 + 4 * MIN)] == [
        T0 + 4 * MIN,
        T0 + 5 * MIN,
    ]
    assert b.recent("NOPE") == []


def test_aggregate_5min_utc_floor():
    t5 = T0 - T0 % 300_000  # 15:45Z
    bars = [
        Bar(t5 + MIN, 10, 12, 9, 11, 1),  # 15:46
        Bar(t5 + 2 * MIN, 11, 15, 11, 14, 2, final=True),  # 15:47
        Bar(t5 + 5 * MIN, 14, 14, 13, 13, 3),  # 15:50 -> next bucket
    ]
    agg = aggregate(bars, 300_000)
    assert [a.time for a in agg] == [t5, t5 + 300_000]
    a = agg[0]
    assert (a.open, a.high, a.low, a.close, a.volume, a.final) == (10, 15, 9, 14, 3, False)
    assert agg[1].open == 14 and agg[1].volume == 3


def _vendor(iso: str, o=1.0, h=1.0, lo=1.0, c=1.0, v=0.0):
    return {"time": iso, "open": o, "high": h, "low": lo, "close": c, "volume": v}


def test_splice_appends_past_tail_and_merges_equal_bucket():
    vendor = [
        _vendor("2026-08-17T15:40:00Z", 1, 2, 0.5, 1.5, 100),
        _vendor("2026-08-17T15:45:00Z", 1.5, 1.6, 1.4, 1.55, 40),  # partial
    ]
    t45 = parse_ts_ms("2026-08-17T15:45:00Z")
    ours = [
        Bar(t45 + MIN, 1.55, 1.9, 1.5, 1.8, 30),  # inside vendor's last bucket
        Bar(t45 + 2 * MIN, 1.8, 1.85, 1.7, 1.75, 20),
        Bar(t45 + 5 * MIN, 1.75, 1.8, 1.7, 1.78, 5),  # 15:50 -> new bucket
    ]
    out = splice_vendor_tail(vendor, ours, 300_000)
    assert vendor[-1]["close"] == 1.55  # input untouched
    assert len(out) == 3
    merged = out[1]
    assert merged["open"] == 1.5 and merged["high"] == 1.9 and merged["low"] == 1.4
    assert merged["close"] == 1.75 and merged["volume"] == 50  # max(40, 30+20)
    assert out[2]["time"] == "2026-08-17T15:50:00Z" and out[2]["open"] == 1.75


def test_splice_noops_when_nothing_newer():
    vendor = [_vendor("2026-08-17T15:46:00Z")]
    older = [Bar(T0 - MIN, 1, 1, 1, 1, 1)]
    assert splice_vendor_tail(vendor, older, MIN) is vendor
    assert splice_vendor_tail(vendor, [], MIN) is vendor
    empty = splice_vendor_tail([], [Bar(T0, 1, 2, 0.5, 1.5, 3)], MIN)
    assert empty[0]["time"] == "2026-08-17T15:46:00Z"


def test_bar_payload_shape():
    p = bar_payload("SPY", Bar(T0, 1, 2, 0.5, 1.5, 3))
    assert p == {
        "symbol": "SPY",
        "timeframe": "1Min",
        "time": T0,
        "open": 1,
        "high": 2,
        "low": 0.5,
        "close": 1.5,
        "volume": 3,
        "final": False,
        "session": "regular",
    }
