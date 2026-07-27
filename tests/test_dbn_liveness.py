"""Roadmap 0.1 — a dead feed must not look like a quiet market.

The whole point is that `is_connected()` cannot be trusted: the failure that
matters is a socket that still reports healthy while nothing arrives. So these
drive `FeedLiveness` on an injected clock and assert on the three faults it
separates — never acked, dead transport, dead pipeline — plus the false alarms
that would get the alarm ignored.
"""

from __future__ import annotations

import pytest

from signals.options_flow.dbn_stream import (
    PIPELINE_STALL_S,
    RATE_LOG_S,
    SILENCE_S,
    SUBSCRIPTION_ACK_S,
    FeedDead,
    FeedLiveness,
)


def _live(t0: float = 1000.0, *, acked: bool = True) -> FeedLiveness:
    live = FeedLiveness(t0)
    if acked:
        live.observe(data_msgs=1, prints=1, acks=1, now=t0)
    return live


# --- dead transport: the roadmap's done-condition ---------------------------

def test_a_killed_socket_raises_well_inside_two_minutes():
    """Roadmap 0.1: 'a deliberately killed socket raises inside 2 minutes'.
    Records simply stop; nothing else about the client changes."""
    live = _live()
    with pytest.raises(FeedDead, match="transport is dead"):
        live.check(now=1000.0 + SILENCE_S + 1, market_open=True)
    assert SILENCE_S + 1 < 120, "detection budget must fit inside 2 minutes"


def test_a_quiet_stretch_shorter_than_the_budget_does_not_raise():
    live = _live()
    assert live.check(now=1000.0 + SILENCE_S - 1, market_open=True) is None


def test_arriving_records_reset_the_silence_clock():
    live = _live()
    for i in range(1, 6):
        t = 1000.0 + i * (SILENCE_S - 5)
        live.observe(data_msgs=1 + i, prints=1 + i, acks=1, now=t)
        assert live.check(now=t, market_open=True) is None


# --- the false alarms that would get the check ignored ----------------------

def test_silence_outside_rth_is_not_a_fault():
    """The market being closed is the normal reason for no prints. Alarming on
    it would train everyone to ignore the alarm."""
    live = _live()
    assert live.check(now=1000.0 + SILENCE_S * 10, market_open=False) is None


def test_the_clock_restarts_at_the_open_after_a_closed_stretch():
    """The subtle one. After hours of legitimate silence, the first RTH tick
    would compare against a stale timestamp and report a dead feed on a feed
    that is working perfectly — so a closed-market tick must reset the clock."""
    live = _live()
    live.check(now=1000.0 + 40_000, market_open=False)      # overnight
    assert live.check(now=1000.0 + 40_001, market_open=True) is None


# --- subscription echo: the only check valid outside RTH --------------------

def test_a_subscription_never_acked_raises_during_rth():
    """Asking for 40 parents and having the server ack nothing looks exactly like
    a quiet market, so it is fatal."""
    live = FeedLiveness(1000.0)
    with pytest.raises(FeedDead, match="never acked"):
        live.check(now=1000.0 + SUBSCRIPTION_ACK_S + 1, market_open=True)


def test_a_missing_ack_outside_rth_ALSO_raises():
    """Reversed on 2026-07-27 by probing the real gateway, and the reversal is
    the point. This used to log instead of raising, because the ack was inferred
    from `len(symbology_map)` and nobody knew whether mappings arrive off-hours.
    They do NOT — measured at 00:07 ET, `symbology_map` stayed empty for 75s
    while the gateway acked in ~17ms. So the old check read a signal that is
    absent by design overnight and fired a FALSE 'never acked' on a healthy
    subscription; the RTH-only hedge was the only thing standing between that
    and a collector restart every 60s all weekend. Keyed on the real ack the
    condition is honest at every hour, and a rejected subscription is now caught
    at 03:00 rather than surviving until the opening bell."""
    live = FeedLiveness(1000.0)
    with pytest.raises(FeedDead, match="never acked"):
        live.check(now=1000.0 + SUBSCRIPTION_ACK_S + 1, market_open=False)


def test_the_gateway_ack_satisfies_it_with_no_symbols_mapped():
    """The exact off-hours shape the probe recorded: one ack, zero mappings,
    zero data. Must be silent — this is a healthy idle subscription."""
    live = FeedLiveness(1000.0)
    live.observe(data_msgs=0, prints=0, acks=1, now=1000.0 + 5)
    assert live.check(now=1000.0 + SUBSCRIPTION_ACK_S + 1, market_open=False) is None
    for i in range(1, 200):                       # a whole weekend of ticks
        assert live.check(now=1000.0 + SUBSCRIPTION_ACK_S + i * 60,
                          market_open=False) is None


def test_grace_period_before_the_ack_is_expected():
    live = FeedLiveness(1000.0)
    assert live.check(now=1000.0 + SUBSCRIPTION_ACK_S - 1, market_open=False) is None


# --- dead pipeline: alive socket, nothing coming out ------------------------

def test_records_without_prints_is_reported_but_not_fatal():
    """A mapping or meta failure is a different bug with a different fix than a
    dead socket, so tearing the healthy socket down would be the wrong response."""
    live = _live()
    t = 1000.0
    for i in range(1, 40):                      # msgs climb, prints never do
        t = 1000.0 + i * 10
        live.observe(data_msgs=1 + i, prints=1, acks=1, now=t)
        stalled = live.check(now=t, market_open=True)
        if stalled is not None:
            assert stalled > PIPELINE_STALL_S
            break
    else:
        pytest.fail("pipeline stall was never reported")


def test_the_pipeline_stall_is_reported_once_not_every_tick():
    """Per-tick logging at a 1s flush would emit thousands of identical lines and
    bury the thing it is warning about."""
    live = _live()
    t = 1000.0 + PIPELINE_STALL_S + 1
    live.observe(data_msgs=99, prints=1, acks=1, now=t)
    assert live.check(now=t, market_open=True) is not None
    live.observe(data_msgs=100, prints=1, acks=1, now=t + 1)
    assert live.check(now=t + 1, market_open=True) is None


def test_a_print_rearms_the_pipeline_warning():
    """Recovery then a second stall must warn again, or a flapping pipeline goes
    silent after its first report."""
    live = _live()
    t = 1000.0 + PIPELINE_STALL_S + 1
    live.observe(data_msgs=99, prints=1, acks=1, now=t)
    assert live.check(now=t, market_open=True) is not None
    live.observe(data_msgs=100, prints=2, acks=1, now=t + 1)     # recovered
    t2 = t + 1 + PIPELINE_STALL_S + 1
    live.observe(data_msgs=200, prints=2, acks=1, now=t2)
    assert live.check(now=t2, market_open=True) is not None


# --- the rate counter -------------------------------------------------------

def test_rate_is_none_between_windows_and_a_number_at_the_window():
    # Built without the shared helper so the arithmetic is visible: 0 -> 120
    # prints across exactly one window is 120/min.
    live = FeedLiveness(1000.0)
    live.observe(data_msgs=1, prints=0, acks=1, now=1000.0)
    assert live.take_rate(1000.0 + RATE_LOG_S - 1) is None
    live.observe(data_msgs=200, prints=120, acks=1, now=1000.0 + RATE_LOG_S)
    assert live.take_rate(1000.0 + RATE_LOG_S) == pytest.approx(120.0)


def test_rate_resets_so_windows_do_not_accumulate():
    live = FeedLiveness(1000.0)
    live.observe(data_msgs=2, prints=60, acks=1, now=1000.0 + RATE_LOG_S)
    live.take_rate(1000.0 + RATE_LOG_S)
    # no new prints in the second window
    assert live.take_rate(1000.0 + 2 * RATE_LOG_S) == pytest.approx(0.0)


def test_a_rate_falling_to_zero_is_visible_before_the_alarm_fires():
    """The observability half of 0.1: the counter should show the feed dying
    before the 90s threshold trips, not only after."""
    live = FeedLiveness(1000.0)
    live.observe(data_msgs=1, prints=0, acks=1, now=1000.0)
    live.observe(data_msgs=2, prints=0, acks=1, now=1000.0 + RATE_LOG_S)
    assert live.take_rate(1000.0 + RATE_LOG_S) == pytest.approx(0.0)


# --- the defect review caught: heartbeats must not read as liveness ----------

def test_records_without_book_levels_do_not_count_as_data():
    """THE regression test for the defect that shipped. databento dispatches
    EVERY record to the callback, including the gateway's 30s heartbeat, so a
    counter incremented before the `levels` guard advances forever through a
    completely dead feed. Driven through `_on_record` on purpose: the first
    version of this feature was tested against FeedLiveness in isolation, which
    is exactly why a miscounted input went unnoticed."""
    import types

    from signals.options_flow.dbn_stream import DatabentoFlowStream

    s = DatabentoFlowStream(key="k", on_prints=lambda b: None,
                            get_subscription_set=lambda: ["TSLA"],
                            meta_for=lambda occ: None)
    heartbeat = types.SimpleNamespace(instrument_id=0, ts_recv=0, price=0, size=0)
    for _ in range(120):                       # an hour of 30s heartbeats
        s._on_record(heartbeat)
    assert s.msgs == 120, "raw counter should still see them"
    assert s.data_msgs == 0, "heartbeats are NOT market data"


def test_a_feed_delivering_only_heartbeats_is_declared_dead():
    """End to end on the fixed signal: heartbeats keep arriving, data does not,
    and the watchdog must still fire. Before the fix this ran forever."""
    live = _live()
    with pytest.raises(FeedDead, match="transport is dead"):
        for i in range(1, 12):                 # 11 heartbeats, no data
            t = 1000.0 + i * 30
            live.observe(data_msgs=1, prints=1, acks=1, now=t)
            live.check(now=t, market_open=True)


def test_a_pipeline_fault_is_reported_again_the_next_session():
    """The latch must clear overnight. A pipeline that is broken and STAYS broken
    never advances prints, so without resetting the flag on idle the fault is
    reported once on Monday and never again."""
    live = _live()
    t = 1000.0 + PIPELINE_STALL_S + 1
    live.observe(data_msgs=99, prints=1, acks=1, now=t)
    assert live.check(now=t, market_open=True) is not None      # day 1
    live.check(now=t + 40_000, market_open=False)               # overnight
    t2 = t + 40_000 + PIPELINE_STALL_S + 1
    live.observe(data_msgs=200, prints=1, acks=1, now=t2)
    assert live.check(now=t2, market_open=True) is not None, "day 2 went silent"
