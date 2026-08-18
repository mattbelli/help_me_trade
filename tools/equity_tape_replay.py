#!/usr/bin/env python
"""Replay a recorded equity tape over the Alpaca stocks-v2 msgpack protocol.

Serves a `signals/equity_stream/recorder.py` file (`<date>.mpk.zst`, msgpack
``[recv_ns, frame]`` pairs) to any collector that dials it — the Python
collector via tools/equity_collector_replay.py, the Rust collector via its
``--url`` — exactly as Alpaca would: hello → auth → subscription ack → frames.
The recorded frames go out byte-for-byte; the only additions are
``[{"T":"clock","ns":<recv_ns>}]`` frames every ``--clock-every-ms`` of
recorded time so a collector can run on the tape's own clock (deterministic
sweep windows / velocity buckets / cooldowns at any replay speed).

Pacing: ``--speed 1`` = real time, ``--speed 10`` = 10x, ``--speed 0`` = as
fast as the client can take it (the CPU benchmark). ``--start/--end`` (ET
HH:MM) slice the session. Each client gets its own independent replay, so a
Python and a Rust collector can be A/B'd side by side.

Run: .venv/bin/python tools/equity_tape_replay.py data/equity_tape_raw/2026-08-18.mpk.zst --speed 0
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any, Iterator

import msgpack
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signals.equity_stream.recorder import read_frames  # noqa: E402
from signals.equity_stream.stream import parse_client_frame  # noqa: E402
from trading.market_hours import _ET  # noqa: E402

logger = logging.getLogger("equity_tape_replay")

_CHANNELS = ("trades", "quotes", "dailyBars", "bars", "updatedBars")
# After the last recorded frame the server advances the clock this far so
# every collector runs its final flushes (2s events, 30s spots) before close.
END_GRACE_MS = 60_000


def _pack(obj: Any) -> bytes:
    return msgpack.packb(obj, use_bin_type=True)


def _clock_frame(ns: int) -> bytes:
    return _pack([{"T": "clock", "ns": int(ns)}])


def _et_bound(day: datetime, hhmm: str | None) -> int | None:
    if not hhmm:
        return None
    hh, mm = (int(x) for x in hhmm.split(":"))
    return int(datetime.combine(day.date(), dtime(hh, mm), tzinfo=_ET).timestamp() * 1e9)


class Replay:
    def __init__(
        self,
        path: Path,
        *,
        speed: float,
        clock_every_ms: int,
        start: str | None,
        end: str | None,
        limit: int | None,
    ) -> None:
        self.path = path
        self.speed = speed
        self.clock_every_ns = int(clock_every_ms * 1e6)
        self.start = start
        self.end = end
        self.limit = limit

    def frames(self) -> Iterator[tuple[int, bytes]]:
        lo = hi = None
        n = 0
        for recv_ns, raw in read_frames(self.path):
            if lo is None and (self.start or self.end):
                day = datetime.fromtimestamp(recv_ns / 1e9, _ET)
                lo, hi = _et_bound(day, self.start), _et_bound(day, self.end)
            if lo is not None and recv_ns < lo:
                continue
            if hi is not None and recv_ns >= hi:
                return
            yield recv_ns, raw
            n += 1
            if self.limit is not None and n >= self.limit:
                return

    async def stream(self, ws: Any) -> None:
        t0_ns: int | None = None
        wall0 = time.monotonic()
        last_clock_ns: int | None = None
        last_ns = 0
        sent = 0
        for recv_ns, raw in self.frames():
            if t0_ns is None:
                t0_ns = recv_ns
            if self.speed > 0:
                target = wall0 + (recv_ns - t0_ns) / 1e9 / self.speed
                delay = target - time.monotonic()
                if delay > 0.001:
                    await asyncio.sleep(delay)
            if last_clock_ns is None or recv_ns - last_clock_ns >= self.clock_every_ns:
                await ws.send(_clock_frame(recv_ns))
                last_clock_ns = recv_ns
            await ws.send(raw)
            last_ns = recv_ns
            sent += 1
            if sent % 20000 == 0:
                # Yield even at speed 0 so subscribe/unsubscribe frames get acked.
                await asyncio.sleep(0)
        if last_ns:
            await ws.send(_clock_frame(last_ns + END_GRACE_MS * 1_000_000))
        logger.info("replay done: %d frames sent", sent)
        # Server-side close ends the client's recv loop (and our reader loop).
        await ws.close()


async def _handle(ws: Any, replay: Replay) -> None:
    subs: dict[str, set[str]] = {ch: set() for ch in _CHANNELS}
    await ws.send(_pack([{"T": "success", "msg": "connected"}]))
    streamer: asyncio.Task | None = None
    try:
        async for raw in ws:
            msgs = (
                [msgpack.unpackb(raw, raw=False)]
                if isinstance(raw, (bytes, bytearray))
                else parse_client_frame(raw)
            )
            for msg in msgs:
                if not isinstance(msg, dict):
                    continue
                action = str(msg.get("action", "")).lower()
                if action == "auth":
                    await ws.send(_pack([{"T": "success", "msg": "authenticated"}]))
                elif action in ("subscribe", "unsubscribe"):
                    for ch in _CHANNELS:
                        syms = msg.get(ch)
                        if isinstance(syms, list):
                            names = {str(s).upper() for s in syms}
                            subs[ch] = subs[ch] | names if action == "subscribe" else subs[ch] - names
                    await ws.send(
                        _pack([{"T": "subscription", **{ch: sorted(subs[ch]) for ch in _CHANNELS}}])
                    )
                    if streamer is None and action == "subscribe":
                        streamer = asyncio.create_task(replay.stream(ws))
    except websockets.ConnectionClosed:
        pass
    finally:
        if streamer is not None:
            if not streamer.done():
                streamer.cancel()
            else:
                exc = streamer.exception() if not streamer.cancelled() else None
                if exc is not None:
                    logger.warning("replay stream ended with %r", exc)
        await ws.close()


async def serve(replay: Replay, host: str, port: int) -> None:
    async with websockets.serve(lambda ws: _handle(ws, replay), host, port, max_size=None):
        logger.info("replay server on ws://%s:%d serving %s (speed=%s)", host, port, replay.path, replay.speed)
        await asyncio.Future()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--speed", type=float, default=1.0, help="1=real time, 0=as fast as possible")
    ap.add_argument("--clock-every-ms", type=int, default=100)
    ap.add_argument("--start", help="ET HH:MM — skip frames before")
    ap.add_argument("--end", help="ET HH:MM — stop at")
    ap.add_argument("--limit", type=int, help="max frames (tests)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    replay = Replay(
        args.path,
        speed=args.speed,
        clock_every_ms=args.clock_every_ms,
        start=args.start,
        end=args.end,
        limit=args.limit,
    )
    try:
        asyncio.run(serve(replay, args.host, args.port))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
