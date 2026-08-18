"""Equity tape record → replay → collector A/B harness.

Pins: the recorder round-trips raw frames + arrival ns; the replay server
speaks the Alpaca handshake and injects clock frames; the Python collector in
replay mode produces events off the tape's virtual clock; and two replays of
the same recording are byte-identical (the property that makes the Rust
collector's diff meaningful).
"""

from __future__ import annotations

import asyncio
import socket
import sqlite3
from datetime import datetime
from pathlib import Path

import msgpack
import pytest
import websockets

from signals.equity_stream.recorder import RawFrameRecorder, read_frames
from signals.equity_stream.runner import EquityTapeRunner
from tools import equity_tape_ab
from tools.equity_tape_replay import Replay, _handle
from trading.market_hours import _ET


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ts(ms: int) -> msgpack.Timestamp:
    return msgpack.Timestamp(ms // 1000, (ms % 1000) * 1_000_000)


def _t0_ms() -> int:
    return int(datetime(2026, 8, 18, 10, 0, tzinfo=_ET).timestamp() * 1000)


def _synthetic_tape(path: Path) -> int:
    """~4s of tape: quotes then a $20M block (two prints for the tick test) and
    a 3-venue ISO sweep. Returns the number of frames written."""
    t0 = _t0_ms()
    frames: list[tuple[int, bytes]] = []

    def frame(at_ms: int, msgs: list[dict]) -> None:
        frames.append((int((t0 + at_ms) * 1_000_000), msgpack.packb(msgs, use_bin_type=True)))

    frame(0, [{"T": "subscription", "trades": ["*"], "quotes": ["*"]}])
    frame(10, [{"T": "q", "S": "AAPL", "bp": 99.9, "ap": 100.0, "t": _ts(t0 + 10)}])
    frame(20, [{"T": "t", "S": "AAPL", "p": 100.0, "s": 100, "x": "V", "c": ["@"], "i": 1, "t": _ts(t0 + 20)}])
    frame(30, [{"T": "t", "S": "AAPL", "p": 100.0, "s": 200_000, "x": "D", "c": ["@"], "i": 2, "t": _ts(t0 + 30)}])
    frame(40, [
        {"T": "q", "S": "MSFT", "bp": 49.9, "ap": 50.0, "t": _ts(t0 + 40)},
        {"T": "t", "S": "MSFT", "p": 50.0, "s": 5000, "x": "V", "c": ["F"], "i": 3, "t": _ts(t0 + 41)},
        {"T": "t", "S": "MSFT", "p": 50.0, "s": 5000, "x": "K", "c": ["F"], "i": 4, "t": _ts(t0 + 42)},
        {"T": "t", "S": "MSFT", "p": 50.0, "s": 5000, "x": "P", "c": ["F"], "i": 5, "t": _ts(t0 + 43)},
    ])
    frame(3500, [{"T": "t", "S": "AAPL", "p": 100.1, "s": 100, "x": "V", "c": ["@"], "i": 6, "t": _ts(t0 + 3500)}])
    frame(4000, [{"T": "q", "S": "AAPL", "bp": 100.0, "ap": 100.1, "t": _ts(t0 + 4000)}])

    rec = RawFrameRecorder(path)
    rec.start()
    for ns, raw in frames:
        rec.record(raw, ns)
    rec.stop()
    return len(frames)


def test_recorder_round_trips_frames_and_arrival_ns(tmp_path: Path):
    n = _synthetic_tape(tmp_path)
    files = sorted(tmp_path.glob("*.mpk.zst"))
    assert [f.name for f in files] == ["2026-08-18.mpk.zst"]
    got = list(read_frames(files[0]))
    assert len(got) == n
    assert got[0][0] == _t0_ms() * 1_000_000
    assert msgpack.unpackb(got[1][1], raw=False)[0]["S"] == "AAPL"
    # arrival ns strictly ordered as recorded
    assert [ns for ns, _ in got] == sorted(ns for ns, _ in got)


def test_recorder_appends_across_restarts(tmp_path: Path):
    _synthetic_tape(tmp_path)
    rec = RawFrameRecorder(tmp_path)
    rec.start()
    rec.record(b"\x90", int((_t0_ms() + 5000) * 1_000_000))
    rec.stop()
    got = list(read_frames(tmp_path / "2026-08-18.mpk.zst"))
    assert got[-1][1] == b"\x90"


async def _serve(replay: Replay, port: int):
    return await websockets.serve(lambda ws: _handle(ws, replay), "127.0.0.1", port, max_size=None)


@pytest.mark.asyncio
async def test_replay_server_speaks_alpaca_handshake_and_clock_frames(tmp_path: Path):
    _synthetic_tape(tmp_path)
    port = _free_port()
    replay = Replay(tmp_path / "2026-08-18.mpk.zst", speed=0, clock_every_ms=100, start=None, end=None, limit=None)
    server = await _serve(replay, port)
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}", max_size=None) as ws:
            hello = msgpack.unpackb(await ws.recv(), raw=False)
            assert hello == [{"T": "success", "msg": "connected"}]
            await ws.send(msgpack.packb({"action": "auth", "key": "k", "secret": "s"}))
            assert msgpack.unpackb(await ws.recv(), raw=False)[0]["msg"] == "authenticated"
            await ws.send(msgpack.packb({"action": "subscribe", "trades": ["*"], "quotes": ["*"]}))
            ack = msgpack.unpackb(await ws.recv(), raw=False)[0]
            assert ack["T"] == "subscription" and ack["trades"] == ["*"]
            types = []
            async for raw in ws:
                for m in msgpack.unpackb(raw, raw=False):
                    types.append(m["T"])
            # First frame is a clock, clocks are interleaved, and one trailing
            # end-grace clock lets collectors flush.
            assert types[0] == "clock"
            assert types.count("t") == 6 and types.count("q") == 3
            assert types[-1] == "clock"
    finally:
        server.close()
        await server.wait_closed()


async def _run_collector(url: str, db: Path) -> EquityTapeRunner:
    runner = EquityTapeRunner(
        key="k", secret="s", replay=True, url=url, proxy_port=_free_port(), db_path=db
    )
    await asyncio.wait_for(runner.stream.run(), timeout=20)
    return runner


@pytest.mark.asyncio
async def test_replay_collector_emits_events_and_is_deterministic(tmp_path: Path):
    _synthetic_tape(tmp_path)
    port = _free_port()
    replay = Replay(tmp_path / "2026-08-18.mpk.zst", speed=0, clock_every_ms=100, start=None, end=None, limit=None)
    server = await _serve(replay, port)
    try:
        a, b = tmp_path / "a.db", tmp_path / "b.db"
        ra = await _run_collector(f"ws://127.0.0.1:{port}", a)
        rb = await _run_collector(f"ws://127.0.0.1:{port}", b)
    finally:
        server.close()
        await server.wait_closed()

    assert ra.stream.callback_errors == 0
    con = sqlite3.connect(a)
    rows = {r[0]: r[1] for r in con.execute("SELECT event_type, symbol FROM equity_tape_events")}
    assert rows == {"dark_pool": "AAPL", "equity_sweep": "MSFT"}
    spots = dict(con.execute("SELECT symbol, price FROM fusion_spot_cache").fetchall())
    # spot flush ran (end-grace clock) with the last admitted consolidated print
    assert spots == {"AAPL": "100.1", "MSFT": "50.0"}
    con.close()

    assert rb.stream.callback_errors == 0
    result = equity_tape_ab.compare(a, b)
    assert result["events"]["a"] == 2
    assert not result["events"]["only_a"] and not result["events"]["only_b"]
    assert not result["events"]["changed"]
    assert not result["spots"]["changed"] and not result["spots"]["only_a"] and not result["spots"]["only_b"]
    assert equity_tape_ab.main([str(a), str(b)]) == 0
