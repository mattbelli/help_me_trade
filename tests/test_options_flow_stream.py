"""Hermetic tests for OPRA options-flow websocket stream (fake ws, no sockets)."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any

import msgpack
import pytest

from signals.options_flow.intraday import normalize_print
from signals.options_flow.stream import (
    CONNECTION_LIMIT_BACKOFF_SEC,
    QUOTE_STALE_FOR_DIR1_SECONDS,
    RECONNECT_BASE_SEC,
    OptionsFlowStream,
)

TODAY = "2026-07-09"
EXPIRY = "2026-08-21"
OCC = "NVDA260821C00190000"
OCC2 = "NVDA260821C00200000"
OCC3 = "NVDA260821C00210000"
UNKNOWN = "ZZZZ260821C00001000"

_META = {
    "occ_symbol": OCC,
    "underlying": "NVDA",
    "right": "call",
    "strike": 190.0,
    "expiry": EXPIRY,
    "dte": 43,
}
_META2 = {**_META, "occ_symbol": OCC2, "strike": 200.0}
_META3 = {**_META, "occ_symbol": OCC3, "strike": 210.0}


def _pack(msgs: list[dict]) -> bytes:
    return msgpack.packb(msgs, use_bin_type=True)


def _trade_frame(
    occ: str,
    *,
    price: float = 4.5,
    size: int = 100,
    condition: str = "I",
    ts_sec: int = 1_752_000_000,
) -> bytes:
    ts = msgpack.ext.Timestamp(seconds=ts_sec, nanoseconds=0)
    return _pack(
        [
            {
                "T": "t",
                "S": occ,
                "t": ts,
                "p": price,
                "s": size,
                "x": "C",
                "c": condition,
            }
        ]
    )


def _quote_frame(
    occ: str,
    *,
    bid: float = 4.4,
    ask: float = 4.6,
    ts_sec: int = 1_752_000_000,
) -> bytes:
    ts = msgpack.ext.Timestamp(seconds=ts_sec, nanoseconds=0)
    return _pack(
        [
            {
                "T": "q",
                "S": occ,
                "t": ts,
                "bx": "C",
                "bp": bid,
                "bs": 10,
                "ax": "C",
                "ap": ask,
                "as": 10,
                "c": "A",
            }
        ]
    )


class ScriptWS:
    """Fake websocket driven by outbound actions; no real sockets."""

    def __init__(
        self,
        *,
        fail_on_recv: int | None = None,
        auth_error_406: bool = False,
        block_after: int | None = None,
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self._inbound: asyncio.Queue[bytes] = asyncio.Queue()
        self._recv_count = 0
        self._fail_on_recv = fail_on_recv
        self._auth_error_406 = auth_error_406
        self._block_after = block_after
        self.closed = False
        self._closed_evt = asyncio.Event()
        self._server_trades: set[str] = set()
        self._server_quotes: set[str] = set()
        self._inbound.put_nowait(_pack([{"T": "success", "msg": "connected"}]))

    async def recv(self) -> bytes:
        self._recv_count += 1
        if self._fail_on_recv is not None and self._recv_count >= self._fail_on_recv:
            raise ConnectionError("simulated disconnect")
        if self._block_after is not None and self._recv_count > self._block_after:
            # Like the real library: a blocked recv raises once the socket closes.
            await self._closed_evt.wait()
            raise ConnectionError("closed while blocked")
        # Race the queue against close — the real library raises into a pending
        # recv when the socket closes (the stream's recv loop has no timeout).
        get_task = asyncio.ensure_future(self._inbound.get())
        closed_task = asyncio.ensure_future(self._closed_evt.wait())
        done, pending = await asyncio.wait(
            {get_task, closed_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if get_task in done:
            return get_task.result()
        raise ConnectionError("closed")

    async def send(self, data: bytes) -> None:
        payload = msgpack.unpackb(data, timestamp=3)
        self.sent.append(payload)
        action = payload.get("action")
        if action == "auth":
            if self._auth_error_406:
                await self._inbound.put(
                    _pack(
                        [
                            {
                                "T": "error",
                                "code": 406,
                                "msg": "connection limit exceeded",
                            }
                        ]
                    )
                )
            else:
                await self._inbound.put(
                    _pack([{"T": "success", "msg": "authenticated"}])
                )
        elif action in ("subscribe", "unsubscribe"):
            # Like the real server: apply the change, echo FULL current state.
            op = set.update if action == "subscribe" else set.difference_update
            op(self._server_trades, payload.get("trades") or [])
            op(self._server_quotes, payload.get("quotes") or [])
            await self._inbound.put(
                _pack(
                    [
                        {
                            "T": "subscription",
                            "trades": sorted(self._server_trades),
                            "quotes": sorted(self._server_quotes),
                        }
                    ]
                )
            )

    async def close(self) -> None:
        self.closed = True
        self._closed_evt.set()
        while not self._inbound.empty():
            try:
                self._inbound.get_nowait()
            except asyncio.QueueEmpty:
                break


def _meta_for(occ: str) -> dict | None:
    lookup = {OCC: _META, OCC2: _META2, OCC3: _META3}
    return lookup.get(occ.strip().upper())


def _connector(ws: ScriptWS):
    """connect_factory is an ASYNC factory (matches the real websockets.connect)."""

    async def connect() -> ScriptWS:
        return ws

    return connect


async def _run_until(
    stream: OptionsFlowStream,
    *,
    timeout: float = 2.0,
    predicate: Any = None,
) -> None:
    task = asyncio.create_task(stream.run())

    async def _wait() -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate is not None and predicate():
                return
            await asyncio.sleep(0.02)
        raise TimeoutError("condition not met")

    try:
        await _wait()
    finally:
        stream.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_handshake_subscribe_sends_expected_frames() -> None:
    ws = ScriptWS()
    subs = [OCC, OCC2]
    received: list[list[dict]] = []

    def on_prints(batch: list[dict]) -> None:
        received.extend(batch)

    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=on_prints,
        get_subscription_set=lambda: subs,
        meta_for=_meta_for,
        connect_factory=_connector(ws),
    )

    await _run_until(stream, predicate=lambda: any(m.get("action") == "subscribe" for m in ws.sent))

    auth = next(m for m in ws.sent if m.get("action") == "auth")
    assert auth == {"action": "auth", "key": "k", "secret": "s"}
    sub = next(m for m in ws.sent if m.get("action") == "subscribe")
    assert sub["trades"] == sorted(subs)
    assert "quotes" not in sub or sub.get("quotes") == []


@pytest.mark.asyncio
async def test_trade_normalized_and_unknown_occ_skipped() -> None:
    ws = ScriptWS()
    batches: list[list[dict]] = []

    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda b: batches.append(list(b)),
        get_subscription_set=lambda: [OCC],
        meta_for=_meta_for,
        connect_factory=_connector(ws),
    )

    task = asyncio.create_task(stream.run())
    try:
        await asyncio.sleep(0.05)
        await ws._inbound.put(_trade_frame(OCC, condition="S"))
        await ws._inbound.put(_trade_frame(UNKNOWN))
        await asyncio.sleep(1.2)
    finally:
        stream.stop()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert stream.skipped_no_meta == 1
    assert len(batches) == 1
    got = batches[0][0]
    expected_raw = {
        "t": datetime.fromtimestamp(1_752_000_000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        + ".000Z",
        "p": 4.5,
        "s": 100,
        "x": "C",
        "c": ["S"],
    }
    assert got == normalize_print(expected_raw, meta=_META)


@pytest.mark.asyncio
async def test_quote_updates_cache_and_stale_pruned(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = ScriptWS()
    now_ms = 1_800_000_000_000
    monkeypatch.setattr(
        "signals.options_flow.stream.time.time", lambda: now_ms / 1000.0
    )

    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda _b: None,
        get_subscription_set=lambda: [OCC],
        meta_for=_meta_for,
        connect_factory=_connector(ws),
    )

    task = asyncio.create_task(stream.run())
    try:
        await asyncio.sleep(0.05)
        await ws._inbound.put(_quote_frame(OCC, bid=4.1, ask=4.9, ts_sec=1_800_000_000))
        await asyncio.sleep(0.05)
        q = stream.latest_quote(OCC)
        assert q is not None
        assert q[0] == pytest.approx(4.1)
        assert q[1] == pytest.approx(4.9)

        stream._quote_cache[OCC2] = (1.0, 2.0, now_ms - 3_700_000)
        stream._last_sub_refresh_mono = 0.0
        await stream._refresh_subscriptions(ws, force=True)
        assert OCC2 not in stream._quote_cache
    finally:
        stream.stop()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_hot_quote_subscribe_debounced_and_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = ScriptWS()
    monkeypatch.setattr(
        "signals.options_flow.stream.HOT_QUOTE_MAX", 2, raising=False
    )
    monkeypatch.setattr(
        "signals.options_flow.stream.QUOTE_SUB_DEBOUNCE_SECONDS", 0.05, raising=False
    )

    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda _b: None,
        get_subscription_set=lambda: [],
        meta_for=_meta_for,
        connect_factory=_connector(ws),
    )

    task = asyncio.create_task(stream.run())
    try:
        await asyncio.sleep(0.05)
        for occ, ts in ((OCC, 100), (OCC2, 200), (OCC3, 300)):
            stream._hot_print_ms[occ] = ts
            stream._pending_quote_adds.add(occ)
        # Wake the read loop (it blocks up to 1s in recv on an empty queue) so
        # the debounced quote-sub pass runs before we assert.
        await ws._inbound.put(_quote_frame(OCC))
        await asyncio.sleep(0.15)
        quote_subs = [
            m for m in ws.sent if m.get("action") == "subscribe" and "quotes" in m
        ]
        assert quote_subs
        last_quotes = set(quote_subs[-1]["quotes"])
        assert len(last_quotes) == 2
        assert OCC3 in last_quotes
        assert OCC2 in last_quotes
        assert OCC not in last_quotes
    finally:
        stream.stop()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_subscription_diff_incremental(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = ScriptWS()
    current = [OCC, OCC2]

    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda _b: None,
        get_subscription_set=lambda: list(current),
        meta_for=_meta_for,
        connect_factory=_connector(ws),
    )
    monkeypatch.setattr(
        "signals.options_flow.stream.SUB_REFRESH_SECONDS", 0.05, raising=False
    )

    task = asyncio.create_task(stream.run())
    try:
        await asyncio.sleep(0.08)
        initial_subs = [
            m for m in ws.sent if m.get("action") == "subscribe" and "trades" in m
        ]
        assert initial_subs[-1]["trades"] == sorted([OCC, OCC2])

        current[:] = [OCC2, OCC3]
        # Wake the read loop so the refresh tick runs before we assert.
        await ws._inbound.put(_quote_frame(OCC2))
        await asyncio.sleep(0.12)
        unsubs = [m for m in ws.sent if m.get("action") == "unsubscribe"]
        subs = [m for m in ws.sent if m.get("action") == "subscribe"]
        assert {"action": "unsubscribe", "trades": [OCC]} in unsubs
        assert {"action": "subscribe", "trades": [OCC3]} in subs
        assert not any(
            m.get("action") == "subscribe"
            and set(m.get("trades") or []) == {OCC, OCC2, OCC3}
            for m in subs
        )
    finally:
        stream.stop()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_subscription_ack_tracked_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server's subscription echo becomes the confirmed (acked) state."""
    ws = ScriptWS()
    current = [OCC, OCC2]

    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda _b: None,
        get_subscription_set=lambda: list(current),
        meta_for=_meta_for,
        connect_factory=_connector(ws),
    )
    monkeypatch.setattr(
        "signals.options_flow.stream.SUB_REFRESH_SECONDS", 0.05, raising=False
    )

    task = asyncio.create_task(stream.run())
    try:
        await asyncio.sleep(0.08)
        assert stream._acked_trades == {OCC, OCC2}

        current[:] = [OCC2, OCC3]
        await ws._inbound.put(_quote_frame(OCC2))
        await asyncio.sleep(0.12)
        assert stream._acked_trades == {OCC2, OCC3}
    finally:
        stream.stop()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def _bare_stream() -> OptionsFlowStream:
    return OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda _b: None,
        get_subscription_set=lambda: [],
        meta_for=_meta_for,
    )


def test_ack_divergence_warns_in_stats(caplog: pytest.LogCaptureFixture) -> None:
    """A persistent sent-vs-acked trade-sub gap must warn, not stay silent."""
    import logging
    import time

    stream = _bare_stream()
    stream._subscribed_trades = {OCC, OCC2}
    stream._handle_frame(_pack([{"T": "subscription", "trades": [OCC], "quotes": []}]))

    with caplog.at_level(logging.INFO, logger="signals.options_flow.stream"):
        stream._maybe_log_stats(time.monotonic())
    assert "ack_trades=1" in caplog.text
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and "diverge" in warnings[0].getMessage()

    # Once the ack matches, a later stats pass must not warn.
    caplog.clear()
    stream._handle_frame(
        _pack([{"T": "subscription", "trades": [OCC, OCC2], "quotes": []}])
    )
    stream._last_stats_log_mono = 0.0
    with caplog.at_level(logging.INFO, logger="signals.options_flow.stream"):
        stream._maybe_log_stats(time.monotonic())
    assert "ack_trades=2" in caplog.text
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_error_frame_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Post-handshake server error frames must surface, not vanish."""
    import logging

    stream = _bare_stream()
    with caplog.at_level(logging.WARNING, logger="signals.options_flow.stream"):
        stream._handle_frame(
            _pack([{"T": "error", "code": 405, "msg": "symbol limit exceeded"}])
        )
    assert "code=405" in caplog.text and "symbol limit exceeded" in caplog.text


@pytest.mark.asyncio
async def test_subscribes_as_soon_as_set_fills_mid_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connect pre-open with an empty set, then the bell fills it mid-window.

    SUB_REFRESH_SECONDS is anchored to connect time, not the market clock, so waiting
    for the next tick left the stream unsubscribed through the open burst.
    """
    ws = ScriptWS()
    current: list[str] = []

    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda _b: None,
        get_subscription_set=lambda: list(current),
        meta_for=_meta_for,
        connect_factory=_connector(ws),
    )
    # Long cadence: without the fix nothing would resubscribe for another 5 minutes.
    monkeypatch.setattr(
        "signals.options_flow.stream.SUB_REFRESH_SECONDS", 300.0, raising=False
    )

    task = asyncio.create_task(stream.run())
    try:
        await asyncio.sleep(0.08)
        assert not [
            m for m in ws.sent if m.get("action") == "subscribe" and "trades" in m
        ]

        current[:] = [OCC]  # the bell: active set appears
        await asyncio.sleep(0.12)
        subs = [m for m in ws.sent if m.get("action") == "subscribe" and "trades" in m]
        assert subs, "stream stayed unsubscribed after the active set filled"
        assert subs[-1]["trades"] == [OCC]
    finally:
        stream.stop()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_reconnect_backoff_and_resubscribe(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    ws_instances: list[ScriptWS] = []

    async def factory() -> ScriptWS:
        if not ws_instances:
            ws = ScriptWS(fail_on_recv=5)
        else:
            ws = ScriptWS()
        ws_instances.append(ws)
        return ws

    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda _b: None,
        get_subscription_set=lambda: [OCC],
        meta_for=_meta_for,
        connect_factory=factory,
    )
    monkeypatch.setattr(stream, "_sleep", fake_sleep)

    task = asyncio.create_task(stream.run())
    try:
        await asyncio.sleep(0.05)
        # Feed frames so recv() calls return immediately and the 5th call's
        # simulated disconnect fires without waiting out 1s recv timeouts.
        ws_instances[0]._inbound.put_nowait(_trade_frame(OCC))
        ws_instances[0]._inbound.put_nowait(_trade_frame(OCC))
        await asyncio.sleep(0.3)
    finally:
        stream.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert len(ws_instances) >= 2
    assert stream.reconnects >= 1
    assert RECONNECT_BASE_SEC in sleeps
    assert any(m.get("action") == "subscribe" for m in ws_instances[-1].sent)


@pytest.mark.asyncio
async def test_auth_406_long_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    ws = ScriptWS(auth_error_406=True)
    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda _b: None,
        get_subscription_set=lambda: [],
        meta_for=_meta_for,
        connect_factory=_connector(ws),
    )
    monkeypatch.setattr(stream, "_sleep", fake_sleep)

    task = asyncio.create_task(stream.run())
    try:
        await asyncio.sleep(0.1)
    finally:
        stream.stop()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert CONNECTION_LIMIT_BACKOFF_SEC in sleeps


@pytest.mark.asyncio
async def test_stop_ends_run_promptly() -> None:
    ws = ScriptWS(block_after=3)
    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda _b: None,
        get_subscription_set=lambda: [],
        meta_for=_meta_for,
        connect_factory=_connector(ws),
    )

    task = asyncio.create_task(stream.run())
    await asyncio.sleep(0.08)
    stream.stop()
    await asyncio.wait_for(task, timeout=1.0)


def test_alpaca_prints_never_carry_a_book_so_quote_at_stays_the_path() -> None:
    """sweep_loop now reads `p["nbbo"] or stream.quote_at(...)`, because the
    Databento lane stamps the book on the print. This lane has no book to stamp
    — quotes arrive on a separate channel — so its prints must stay bare or the
    `or` would short-circuit past the time-matching that IS this lane's
    classification."""
    p = normalize_print(
        {"t": "2026-07-09T14:30:00Z", "p": "4.20", "s": "10", "x": "C", "c": []},
        meta=_meta_for(OCC),
    )
    assert p is not None and "nbbo" not in p


def test_quote_at_prevailing_before_print_wins() -> None:
    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda _b: None,
        get_subscription_set=lambda: [],
        meta_for=_meta_for,
    )
    print_ts = 1_800_000_000_000
    stream._quote_ring[OCC] = deque(
        [
            (print_ts - 2000, 4.0, 4.2),
            (print_ts - 500, 4.1, 4.3),
            (print_ts + 300, 4.5, 4.7),
        ],
        maxlen=64,
    )
    q = stream.quote_at(OCC, print_ts)
    assert q is not None
    assert q[0] == pytest.approx(4.1)
    assert q[1] == pytest.approx(4.3)
    assert q[2] == print_ts - 500


def test_quote_at_fallback_to_quote_after_print() -> None:
    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda _b: None,
        get_subscription_set=lambda: [],
        meta_for=_meta_for,
    )
    print_ts = 1_800_000_000_000
    stream._quote_ring[OCC] = deque(
        [
            (print_ts + 400, 4.5, 4.7),
        ],
        maxlen=64,
    )
    q = stream.quote_at(OCC, print_ts)
    assert q is not None
    assert q[0] == pytest.approx(4.5)
    assert q[2] == print_ts + 400


def test_quote_at_stale_before_print_returns_none() -> None:
    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda _b: None,
        get_subscription_set=lambda: [],
        meta_for=_meta_for,
    )
    print_ts = 1_800_000_000_000
    stale_ms = QUOTE_STALE_FOR_DIR1_SECONDS * 1000 + 1
    stream._quote_ring[OCC] = deque(
        [(print_ts - stale_ms, 4.0, 4.2)],
        maxlen=64,
    )
    assert stream.quote_at(OCC, print_ts) is None


def test_quote_at_after_fallback_beyond_1s_returns_none() -> None:
    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda _b: None,
        get_subscription_set=lambda: [],
        meta_for=_meta_for,
    )
    print_ts = 1_800_000_000_000
    stream._quote_ring[OCC] = deque(
        [(print_ts + 1001, 4.5, 4.7)],
        maxlen=64,
    )
    assert stream.quote_at(OCC, print_ts) is None


@pytest.mark.asyncio
async def test_quote_ring_pruned_with_cache_and_quote_unsub() -> None:
    ws = ScriptWS()
    now_ms = 1_800_000_000_000
    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda _b: None,
        get_subscription_set=lambda: [OCC, OCC2],
        meta_for=_meta_for,
        connect_factory=_connector(ws),
    )
    stream._quote_cache[OCC2] = (1.0, 2.0, now_ms - 3_700_000)
    stream._quote_ring[OCC2] = deque(
        [(now_ms - 3_700_000, 1.0, 2.0)],
        maxlen=64,
    )
    stream._subscribed_quotes = {OCC, OCC2}
    stream._last_sub_refresh_mono = 0.0

    import time as time_mod
    original_time = time_mod.time
    time_mod.time = lambda: now_ms / 1000.0
    try:
        await stream._refresh_subscriptions(ws, force=True)
        assert OCC2 not in stream._quote_cache
        assert OCC2 not in stream._quote_ring

        stream._quote_ring[OCC] = deque(
            [(now_ms, 3.0, 4.0)],
            maxlen=64,
        )
        stream._subscribed_quotes = {OCC, OCC3}
        await stream._diff_and_send(
            ws,
            channel="quotes",
            current={OCC, OCC3},
            desired={OCC},
        )
        assert OCC3 not in stream._quote_ring
        assert OCC in stream._quote_ring
    finally:
        time_mod.time = original_time
