"""Monday-open probe: does OPRA trades:['*'] actually FLOW, and at what rate?

The Sat 2026-07-11 probe confirmed the wildcard subscription ACKs; this measures
the part an ACK can't promise (References/FLOW_CAPACITY_RESEARCH_2026-07-11.md):
  - msg/s (avg + peak 1s) over the sample window
  - lag: p50/p95/max of (receive wall clock - print ts)
  - client cost: fraction of wall time spent receiving+parsing (msgpack)
Branch decision (FLOW_INTELLIGENCE_DESIGN §build): sustained p95 lag < 2s and
tolerable CPU → branch W (wildcard). Otherwise branch C (capped active set).

Run at ~09:31 ET: uv run python scripts/opra_rate_probe.py [--secs 90]
    [--capture research/options_flow/tape_YYYY-MM-DD_open.mpk]
--capture dumps every raw frame + receive timestamp to a file so perf work can
replay real market-hours tape offline (scripts/opra_replay_bench.py) instead of
waiting for the next open. ~90s of full tape ≈ tens of MB; keep out of git.
NOTE: 1 conn/account — stop the prod stream first (kill hmt-backend MainPID and
start this within its ~5s boot window, or use the retry: this script retries 406
for 45s just like opra_wildcard_probe.py).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import msgpack
import websockets
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

WS_URL = "wss://stream.data.alpaca.markets/v1beta1/opra"
KEY = os.environ["APCA_API_KEY_ID"]
SECRET = os.environ["APCA_API_SECRET_KEY"]


def _unpack(raw: bytes):
    data = msgpack.unpackb(raw, timestamp=3)
    return data if isinstance(data, list) else [data]


async def _auth_with_retry(deadline_s: float = 45.0):
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    attempt = 0
    while True:
        attempt += 1
        ws = await websockets.connect(WS_URL, max_size=None)
        _unpack(await ws.recv())
        await ws.send(msgpack.packb({"action": "auth", "key": KEY, "secret": SECRET}))
        resp = _unpack(await ws.recv())
        if any(m.get("msg") == "authenticated" for m in resp):
            print(f"auth: OK (attempt {attempt})")
            return ws
        await ws.close()
        if loop.time() - t0 > deadline_s:
            raise SystemExit(f"gave up: {resp}")
        await asyncio.sleep(0.5)


async def main(secs: int, capture: str | None) -> None:
    cap_file = None
    cap_packer = None
    if capture:
        cap_path = Path(capture)
        cap_path.parent.mkdir(parents=True, exist_ok=True)
        cap_file = cap_path.open("wb")
        cap_packer = msgpack.Packer(use_bin_type=True)

    ws = await _auth_with_retry()
    async with ws:
        await ws.send(msgpack.packb({"action": "subscribe", "trades": ["*"]}))
        print("subscribed trades:['*'] — sampling", secs, "s ...")

        n_msgs = 0
        n_trades = 0
        lags_ms: list[float] = []
        per_sec: dict[int, int] = {}
        busy_s = 0.0
        t_start = time.time()
        deadline = t_start + secs

        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                print("  (5s with no messages)")
                continue
            t0 = time.perf_counter()
            recv_wall = time.time()
            if not isinstance(raw, bytes):
                continue
            if cap_file is not None:
                cap_file.write(cap_packer.pack((int(recv_wall * 1e9), raw)))
            msgs = _unpack(raw)
            for m in msgs:
                n_msgs += 1
                if m.get("T") != "t":
                    continue
                n_trades += 1
                per_sec[int(recv_wall)] = per_sec.get(int(recv_wall), 0) + 1
                t = m.get("t")
                if isinstance(t, datetime):
                    ts = t if t.tzinfo else t.replace(tzinfo=UTC)
                    lags_ms.append((recv_wall - ts.timestamp()) * 1000)
            busy_s += time.perf_counter() - t0

        wall = time.time() - t_start
        lags_ms.sort()
        n = len(lags_ms)

        def pct(p: float) -> float:
            return lags_ms[min(n - 1, int(n * p))] if n else float("nan")

        print(f"\n=== RESULTS ({wall:.0f}s sample) ===")
        print(f"trades: {n_trades:,}  ({n_trades / wall:,.0f}/s avg, "
              f"{max(per_sec.values() or [0]):,}/s peak-1s)")
        print(f"lag ms: p50={pct(0.5):,.0f}  p95={pct(0.95):,.0f}  "
              f"max={lags_ms[-1] if n else float('nan'):,.0f}")
        print(f"client busy: {busy_s:.1f}s of {wall:.0f}s wall "
              f"({busy_s / wall * 100:.0f}% of one core for recv+parse)")
        verdict = (
            "branch W viable (extrapolate open-burst headroom before committing)"
            if n and pct(0.95) < 2_000 and busy_s / wall < 0.5
            else "branch C — wildcard too hot for this client as-is"
        )
        print("verdict:", verdict)

    if cap_file is not None:
        cap_file.close()
        size_mb = Path(capture).stat().st_size / 1e6
        print(f"capture: {capture} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=int, default=90)
    ap.add_argument("--capture", default=None, help="dump raw frames for offline replay")
    args = ap.parse_args()
    asyncio.run(main(args.secs, args.capture))
