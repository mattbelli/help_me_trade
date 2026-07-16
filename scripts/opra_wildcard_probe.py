"""Probe: does Alpaca's OPRA websocket accept a WILDCARD (*) on the TRADES channel?

Docs forbid * for option QUOTES only ("too many of them") and are silent on trades;
Polygon's options trades channel supports * explicitly. If * ACKs here, a market-wide
real-time flow tape exists on the current plan (References/FLOW_CAPACITY_RESEARCH_2026-07-11.md).

Read-only. Safe on a closed market: the subscription ACK/error IS the answer — no
prints needed. NOTE: 1 connection per account — the prod stream must not be connected
(it is idle outside regular hours, but a 406 here means it's holding the slot).

Usage:  uv run python scripts/opra_wildcard_probe.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import msgpack
import websockets
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

WS_URL = "wss://stream.data.alpaca.markets/v1beta1/opra"
KEY = os.environ["APCA_API_KEY_ID"]
SECRET = os.environ["APCA_API_SECRET_KEY"]


def _unpack(raw: bytes) -> list[dict]:
    data = msgpack.unpackb(raw, timestamp=3)
    return data if isinstance(data, list) else [data]


async def _recv_all(ws, timeout: float = 6.0) -> list[dict]:
    """Drain frames until timeout — collect every control/data message."""
    out: list[dict] = []
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            if isinstance(raw, bytes):
                out.extend(_unpack(raw))
                timeout = 2.0  # after first frame, short follow-up drain
    except asyncio.TimeoutError:
        return out


async def _auth_with_retry(deadline_s: float = 45.0):
    """Retry tightly on 406 — used to win the connection slot from a restarting
    prod stream (kill backend AFTER starting this; its boot takes seconds)."""
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    attempt = 0
    while True:
        attempt += 1
        ws = await websockets.connect(WS_URL, max_size=None)
        _unpack(await ws.recv())  # connected hello
        await ws.send(msgpack.packb({"action": "auth", "key": KEY, "secret": SECRET}))
        resp = _unpack(await ws.recv())
        if any(m.get("msg") == "authenticated" for m in resp):
            print(f"auth: OK (attempt {attempt})")
            return ws
        await ws.close()
        if loop.time() - t0 > deadline_s:
            raise SystemExit(f"gave up after {attempt} attempts: {resp}")
        await asyncio.sleep(0.5)


async def main() -> None:
    ws = await _auth_with_retry()
    async with ws:

        # THE question: wildcard on the trades channel.
        await ws.send(msgpack.packb({"action": "subscribe", "trades": ["*"]}))
        msgs = await _recv_all(ws)
        print("\n=== trades:['*'] response ===")
        for m in msgs:
            # subscription confirmations echo the full sub list; truncate if huge
            if isinstance(m.get("trades"), list) and len(m["trades"]) > 10:
                m = {**m, "trades": m["trades"][:10] + [f"...(+{len(m['trades']) - 10} more)"]}
            print(" ", m)

        # Control: a normal explicit sub on the same conn (proves the conn is sane
        # even if * was rejected).
        await ws.send(
            msgpack.packb({"action": "subscribe", "trades": ["NVDA260717C00210000"]})
        )
        msgs = await _recv_all(ws)
        print("\n=== control explicit sub response ===")
        for m in msgs:
            if isinstance(m.get("trades"), list) and len(m["trades"]) > 10:
                m = {**m, "trades": m["trades"][:10] + [f"...(+{len(m['trades']) - 10} more)"]}
            print(" ", m)


if __name__ == "__main__":
    asyncio.run(main())
