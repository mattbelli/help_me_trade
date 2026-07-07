"""Silently-degraded-stream detector (see brokers/market_stream.py).

A recovered Schwab stream can keep DELIVERING ticks with FROZEN prices — no
connection error, no message-timeout, so the two existing reconnect triggers stay
blind. The fresh REST poll (apply_extended) is ground truth: when the stream's
last-tick price sits still while the REST price diverges from it across several
polls on several symbols, the stream is degraded and we force ONE paced reconnect.

These tests inject a fake monotonic clock (never the wall clock) and touch no
network — they drive _record_stream_tick / _note_stream_staleness directly and
assert the force-reconnect event + counter state.
"""

from __future__ import annotations

import asyncio

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from brokers.market_stream import (
    FORCE_RECONNECT_COOLDOWN_S,
    RECONNECT_BASE_SEC,
    STALE_STREAM_LIVE_WINDOW_S,
)
from brokers.schwab_market_stream import SchwabMarketStreamHub


class _FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _hub() -> tuple[SchwabMarketStreamHub, _FakeClock]:
    hub = SchwabMarketStreamHub()
    clock = _FakeClock()
    hub._clock = clock
    return hub, clock


def _poll(hub: SchwabMarketStreamHub, sym: str, stream_price: float, rest_price: float):
    """Simulate one REST poll: the stream's last tick, then the REST comparison."""
    hub._record_stream_tick(sym, stream_price)
    hub._note_stream_staleness(sym, rest_price)


_SYMS = ("AAA", "BBB", "CCC")


def test_frozen_stream_vs_moving_rest_forces_reconnect() -> None:
    """3 symbols frozen at 100 while REST sits at 100.5 (>0.3%) across 2+ polls
    (after the priming poll) → the force-reconnect event trips."""
    hub, clock = _hub()

    # Priming poll: prev-stream-price unset, so nothing is "frozen" yet.
    for sym in _SYMS:
        _poll(hub, sym, 100.0, 100.5)
    assert not hub._force_reconnect_event.is_set()

    # Poll 1: frozen + diverged → count 1 (still below STALE_POLLS_REQUIRED=2).
    clock.advance(6)
    for sym in _SYMS:
        _poll(hub, sym, 100.0, 100.5)
    assert not hub._force_reconnect_event.is_set()

    # Poll 2: each symbol hits count 2; the 3rd distinct symbol meets the gate.
    clock.advance(6)
    for sym in _SYMS:
        _poll(hub, sym, 100.0, 100.5)
    assert hub._force_reconnect_event.is_set()


def test_quiet_market_does_not_trigger() -> None:
    """Stream frozen AND REST unchanged/near it (no divergence) → never trips —
    the divergence clause is the quiet-market guard."""
    hub, clock = _hub()
    for _ in range(6):
        for sym in _SYMS:
            _poll(hub, sym, 100.0, 100.0)  # rest == frozen stream, 0% divergence
        clock.advance(6)
    assert not hub._force_reconnect_event.is_set()
    assert all(hub._stale_poll_count.get(s, 0) == 0 for s in _SYMS)


def test_single_frozen_symbol_does_not_trigger() -> None:
    """One symbol frozen+diverged for many polls, the rest healthy → below the
    multi-symbol gate → no reconnect."""
    hub, clock = _hub()
    for _ in range(6):
        _poll(hub, "AAA", 100.0, 100.5)  # frozen + diverged
        _poll(hub, "BBB", 50.0, 50.02)   # healthy: stream tracks REST
        _poll(hub, "CCC", 20.0, 20.01)   # healthy
        clock.advance(6)
    assert not hub._force_reconnect_event.is_set()
    assert hub._stale_poll_count.get("AAA", 0) >= 2  # it WAS counted, just alone


def test_stale_stream_timestamp_is_not_judged() -> None:
    """A symbol whose last stream tick is older than the live window is the
    message-timeout trigger's job, not this one — it is skipped and its history
    dropped (guards the closed-market false positive)."""
    hub, clock = _hub()
    for sym in _SYMS:
        _poll(hub, sym, 100.0, 100.5)
        _poll(hub, sym, 100.0, 100.5)  # would be counting toward the gate
    # No fresh stream tick; time passes beyond the live window, then REST polls.
    clock.advance(STALE_STREAM_LIVE_WINDOW_S + 10)
    for sym in _SYMS:
        hub._note_stream_staleness(sym, 100.5)
    assert not hub._force_reconnect_event.is_set()
    assert all(s not in hub._stale_poll_count for s in _SYMS)


def test_rapid_retrip_within_cooldown_reconnects_once() -> None:
    """A persistently degraded stream must reconnect at most once per cooldown."""
    hub, clock = _hub()

    def drive_until_trip() -> None:
        for _ in range(3):  # prime + 2 counting polls
            for sym in _SYMS:
                _poll(hub, sym, 100.0, 100.5)
            clock.advance(6)

    drive_until_trip()
    assert hub._force_reconnect_event.is_set()

    # The upstream loop consumes (clears) the event on reconnect.
    hub._force_reconnect_event.clear()

    # Keep feeding a bad stream while still inside the cooldown → no second trip.
    clock.advance(10)
    drive_until_trip()
    assert not hub._force_reconnect_event.is_set()

    # Past the cooldown, the same bad stream is allowed to reconnect again.
    clock.advance(FORCE_RECONNECT_COOLDOWN_S + 1)
    drive_until_trip()
    assert hub._force_reconnect_event.is_set()


def test_counters_reset_after_forced_reconnect() -> None:
    """The stale counters + prev-price memory clear on a trip so the next window
    re-evaluates from scratch."""
    hub, clock = _hub()
    for _ in range(3):
        for sym in _SYMS:
            _poll(hub, sym, 100.0, 100.5)
        clock.advance(6)
    assert hub._force_reconnect_event.is_set()
    assert hub._stale_poll_count == {}
    assert hub._stale_prev_stream_price == {}


def _drive_upstream_loop(monkeypatch, hub, build_side_effects):
    """Run `_upstream_loop` with a mocked stream builder that raises each of
    *build_side_effects* in turn, then stops the loop. Returns the list of backoff
    sleep durations the loop performed. No network, no real sleeps."""
    monkeypatch.setattr(
        "brokers.schwab_market_stream.broker_is_configured", lambda *a: True
    )
    monkeypatch.setattr(
        "brokers.schwab_market_stream._token_exists", lambda: True
    )
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    pending = list(build_side_effects)

    async def fake_build():
        if pending:
            raise pending.pop(0)
        hub._running = False
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "brokers.schwab_market_stream._build_stream_client", fake_build
    )
    hub._running = True
    return sleeps


@pytest.mark.asyncio
async def test_clean_close_reconnects_without_backoff(monkeypatch) -> None:
    """A clean server-initiated close (ConnectionClosedOK, code 1000) reconnects
    immediately with no backoff sleep and does NOT grow the backoff counter — the
    genuine error that follows still backs off at the BASE interval."""
    hub = SchwabMarketStreamHub()
    sleeps = _drive_upstream_loop(
        monkeypatch,
        hub,
        [ConnectionClosedOK(None, None), ConnectionClosedError(None, None)],
    )
    with pytest.raises(asyncio.CancelledError):
        await hub._upstream_loop()
    # Only the genuine error slept, and at BASE — proving the clean close neither
    # slept nor advanced the backoff.
    assert sleeps == [RECONNECT_BASE_SEC]


@pytest.mark.asyncio
async def test_error_close_backs_off_exponentially(monkeypatch) -> None:
    """A genuine error (ConnectionClosedError) keeps the existing exponential
    backoff: two consecutive errors sleep BASE then 2×BASE."""
    hub = SchwabMarketStreamHub()
    sleeps = _drive_upstream_loop(
        monkeypatch,
        hub,
        [ConnectionClosedError(None, None), ConnectionClosedError(None, None)],
    )
    with pytest.raises(asyncio.CancelledError):
        await hub._upstream_loop()
    assert sleeps == [RECONNECT_BASE_SEC, RECONNECT_BASE_SEC * 2]


@pytest.mark.asyncio
async def test_apply_extended_feeds_detector_with_rest_price() -> None:
    """RTH wiring: apply_extended(ext_price=None, rest_price=…) routes the regular
    REST price into the detector, so the RTH frozen-stream case is caught."""
    hub, clock = _hub()

    # Prime + two counting polls, driving the REST side through apply_extended.
    for poll in range(3):
        clock.advance(6)
        for sym in _SYMS:
            hub._record_stream_tick(sym, 100.0)  # frozen stream tick
            await hub.apply_extended(
                sym,
                ext_price=None,
                ext_change_pct=None,
                market_session="regular",
                broadcast=False,
                rest_price=100.5,
            )
    assert hub._force_reconnect_event.is_set()
