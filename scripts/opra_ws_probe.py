"""Phase-3 probe: Alpaca OPRA *websocket* (msgpack) — read-only, no writes.

Answers the design doc's P-1 + P-3 (research/fable/2026-07-09_options-flow-screener-design.md §8):
  P-1  connect wss://stream.data.alpaca.markets/v1beta1/opra (msgpack), subscribe a
       handful of active contracts' trades+quotes; dump real message schemas; probe the
       practical subscription cap (bulk-subscribe ack/error); check whether a second
       OPRA connection is allowed (contention with a future prod stream / probes).
  P-3  live lag stats (receive time minus print timestamp) + condition-code frequency
       counts to sanity-check F-1/F-2 assumptions.

Usage:  uv run python scripts/opra_ws_probe.py [--secs 120] [--cap-test]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
import msgpack
import websockets
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

WS_URL = "wss://stream.data.alpaca.markets/v1beta1/opra"
DATA_BASE = "https://data.alpaca.markets"
KEY = os.environ["APCA_API_KEY_ID"]
SECRET = os.environ["APCA_API_SECRET_KEY"]
H = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SECRET}
UNDERLYINGS = ["NVDA", "TSLA", "SPY"]


def _ts_to_epoch(t) -> float | None:
    """unpackb(timestamp=3) yields datetime; raw Timestamp/str handled too."""
    if isinstance(t, datetime):
        return t.timestamp()
    if isinstance(t, msgpack.ext.Timestamp):
        return t.to_unix()
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def pick_active_contracts(per_underlying: int = 3) -> list[str]:
    """Most-recently-traded contracts via REST snapshots (activity proxy)."""
    out: list[str] = []
    for u in UNDERLYINGS:
        r = httpx.get(
            f"{DATA_BASE}/v1beta1/options/snapshots/{u}",
            headers=H, params={"feed": "opra", "limit": 200}, timeout=30,
        )
        snaps = r.json().get("snapshots", {}) if r.status_code == 200 else {}

        def recency(kv):
            lt = kv[1].get("latestTrade") or {}
            return lt.get("t") or ""

        top = sorted(snaps.items(), key=recency, reverse=True)[:per_underlying]
        out.extend(occ for occ, _ in top)
    return out


def spy_chain_occ(limit: int = 1500) -> list[str]:
    """A big OCC list for the subscription-cap probe (trades-only, short-lived)."""
    r = httpx.get(
        f"{DATA_BASE}/v1beta1/options/snapshots/SPY",
        headers=H, params={"feed": "opra", "limit": min(limit, 1000)}, timeout=30,
    )
    occ = list(r.json().get("snapshots", {}).keys()) if r.status_code == 200 else []
    if len(occ) < limit and r.status_code == 200:
        token = r.json().get("next_page_token")
        if token:
            r2 = httpx.get(
                f"{DATA_BASE}/v1beta1/options/snapshots/SPY",
                headers=H,
                params={"feed": "opra", "limit": 1000, "page_token": token},
                timeout=30,
            )
            if r2.status_code == 200:
                occ.extend(r2.json().get("snapshots", {}).keys())
    return occ[:limit]


async def recv_batch(ws) -> list[dict]:
    raw = await ws.recv()
    msgs = msgpack.unpackb(raw, timestamp=3) if isinstance(raw, bytes) else []
    return msgs if isinstance(msgs, list) else [msgs]


async def authed_connect():
    ws = await websockets.connect(WS_URL, max_size=2**23)
    print(f"  connected; server hello: {await recv_batch(ws)}")
    await ws.send(msgpack.packb({"action": "auth", "key": KEY, "secret": SECRET}))
    print(f"  auth reply: {await recv_batch(ws)}")
    return ws


async def main(secs: int, cap_test: bool) -> None:
    print(f"OPRA websocket probe  (now={datetime.now(timezone.utc).isoformat(timespec='seconds')})")

    contracts = pick_active_contracts()
    print(f"\n[P-1] subscribing {len(contracts)} active contracts: {contracts}")
    ws = await authed_connect()
    await ws.send(msgpack.packb({"action": "subscribe", "trades": contracts, "quotes": contracts}))

    # P-1c: is a second concurrent OPRA connection tolerated?
    print("\n[P-1c] second concurrent connection attempt:")
    try:
        ws2 = await websockets.connect(WS_URL, max_size=2**23)
        hello2 = await asyncio.wait_for(recv_batch(ws2), timeout=5)
        print(f"  second connect hello: {hello2}")
        await ws2.send(msgpack.packb({"action": "auth", "key": KEY, "secret": SECRET}))
        print(f"  second auth reply: {await asyncio.wait_for(recv_batch(ws2), timeout=5)}")
        await ws2.close()
    except Exception as e:  # noqa: BLE001 — probe: report, don't crash
        print(f"  second connection refused/errored: {type(e).__name__}: {e}")

    print(f"\n[P-3] collecting for {secs}s ...")
    n_by_type: Counter[str] = Counter()
    cond_counts: Counter[str] = Counter()
    lags: list[float] = []
    schema_sample: dict[str, dict] = {}
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        try:
            msgs = await asyncio.wait_for(recv_batch(ws), timeout=max(1, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            continue
        now = time.time()
        for m in msgs:
            mt = m.get("T", "?")
            n_by_type[mt] += 1
            if mt not in schema_sample:
                schema_sample[mt] = m
            if mt == "t":
                # OPRA trades carry ONE condition code as a plain string (unlike equities' list)
                cond = m.get("c")
                if cond:
                    cond_counts[str(cond)] += 1
                ep = _ts_to_epoch(m.get("t"))
                if ep is not None:
                    lags.append(now - ep)

    print(f"  message counts by type: {dict(n_by_type)}")
    for mt, m in schema_sample.items():
        print(f"  schema sample T={mt!r}: {m}")
    if lags:
        lags.sort()
        p = lambda q: lags[int(q * (len(lags) - 1))]  # noqa: E731
        print(f"  trade lag secs: n={len(lags)} p50={p(.5):.3f} p90={p(.9):.3f} max={lags[-1]:.3f}")
    else:
        print("  no trade prints received (quiet contracts or market closed)")
    print(f"  condition-code counts on stream trades: {dict(cond_counts) or '(none)'}")

    if cap_test:
        print("\n[P-1 cap] bulk-subscribe probe (trades-only, immediately closed):")
        big = spy_chain_occ(1500)
        print(f"  attempting {len(big)} contract trade subscriptions ...")
        await ws.send(msgpack.packb({"action": "subscribe", "trades": big}))
        t_end = time.monotonic() + 8
        while time.monotonic() < t_end:
            try:
                msgs = await asyncio.wait_for(recv_batch(ws), timeout=t_end - time.monotonic())
            except asyncio.TimeoutError:
                break
            for m in msgs:
                if m.get("T") in ("subscription", "error"):
                    tr = m.get("trades")
                    if isinstance(tr, list):
                        m = {**m, "trades": f"[{len(tr)} symbols]"}
                    print(f"  ack: {m}")
                    if m.get("T") == "error":
                        t_end = 0
    await ws.close()
    print("\ndone (connection closed).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=int, default=120)
    ap.add_argument("--cap-test", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.secs, args.cap_test))
