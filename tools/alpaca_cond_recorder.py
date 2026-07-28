"""Standalone whole-market Alpaca OPRA trades recorder — the codes sidecar, probed.

LIVE_FLOW_PAIRING_SPEC_2026-07-28 Inc 2's live unknowns, measured without
touching the production collector: since the provider flip to Databento the
collector never opens the Alpaca OPRA socket, so this process can hold it
alone (one ws per account — a 406 here means something else grabbed it, stop
and find out what). It subscribes ``trades: ["*"]`` and appends every print
as a msgpack tuple::

    (t_ns, arrival_ns, price_cents, size, symbol, cond, exchange)

t_ns = participant timestamp (the matcher's clock), arrival_ns = our wall
clock at receipt — the pair IS the wire-arrival lag measurement. Offline the
file replays against the day's tcbbo DBN for shadow-fidelity pairing.

Self-contained on purpose (no signals.* imports): a probe with a bell
deadline must not inherit the pipeline's import graph.

    .venv/bin/python tools/alpaca_cond_recorder.py \
        --env-file .env --out data/alpaca_conditions/live_2026-07-28.mpk \
        --until 21:00
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

import msgpack
import websockets

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cond_recorder")

OPRA_WS_URL = "wss://stream.data.alpaca.markets/v1beta1/opra"
RECONNECT_BASE_SEC, RECONNECT_MAX_SEC = 5.0, 120.0
STATS_EVERY_SEC = 60.0
FLUSH_EVERY_SEC = 1.0
LAG_SAMPLE_MAX = 4096
INDEX_ROOTS = ("SPX", "SPXW", "XSP", "VIX", "VIXW", "NDX", "NDXP",
               "SPY", "QQQ", "IWM")


def _root(sym: str) -> str:
    # OCC compact = root + YYMMDD + C/P + 8-digit strike: a fixed 15-char tail.
    return sym[:-15] if len(sym) > 15 else sym


class Recorder:
    def __init__(self, out: Path) -> None:
        self.f = open(out, "ab", buffering=1 << 20)
        self.packer = msgpack.Packer()
        self.n = 0
        self.reconnects = 0
        self.acked_wide = False
        self.roots: dict[str, int] = {}
        self.syms: set[str] = set()
        self.lags_ms: deque[float] = deque(maxlen=LAG_SAMPLE_MAX)
        self._last_stats = time.monotonic()
        self._last_flush = time.monotonic()
        self._last_n = 0

    def on_trade(self, item: dict) -> None:
        sym = str(item.get("S") or "")
        t = item.get("t")
        if not sym or not isinstance(t, int):  # timestamp=2 → epoch ns
            return
        arrival = time.time_ns()
        try:
            cents = int(round(float(item.get("p")) * 100))
            size = int(item.get("s"))
        except (TypeError, ValueError):
            return
        cond = str(item.get("c") or "")
        exch = str(item.get("x") or "")
        self.f.write(self.packer.pack((t, arrival, cents, size, sym, cond, exch)))
        self.n += 1
        self.lags_ms.append((arrival - t) / 1e6)
        root = _root(sym)
        self.roots[root] = self.roots.get(root, 0) + 1
        self.syms.add(sym)

    def tick(self) -> None:
        now = time.monotonic()
        if now - self._last_flush >= FLUSH_EVERY_SEC:
            self.f.flush()
            self._last_flush = now
        if now - self._last_stats >= STATS_EVERY_SEC:
            rate = (self.n - self._last_n) / (now - self._last_stats)
            self._last_stats, self._last_n = now, self.n
            lags = sorted(self.lags_ms)
            p50 = lags[len(lags) // 2] if lags else 0.0
            p95 = lags[int(len(lags) * 0.95)] if lags else 0.0
            idx = " ".join(f"{r}={self.roots.get(r, 0):,}" for r in INDEX_ROOTS)
            logger.info(
                "trades=%s (%.0f/s) syms=%s roots=%s acked_wide=%s "
                "reconnects=%s lag_ms p50=%.0f p95=%.0f | %s",
                f"{self.n:,}", rate, f"{len(self.syms):,}",
                f"{len(self.roots):,}", self.acked_wide, self.reconnects,
                p50, p95, idx,
            )

    def close(self) -> None:
        self.f.flush()
        self.f.close()
        top = sorted(self.roots.items(), key=lambda kv: -kv[1])[:15]
        logger.info("FINAL: trades=%s syms=%s roots=%s top=%s",
                    f"{self.n:,}", f"{len(self.syms):,}",
                    f"{len(self.roots):,}",
                    " ".join(f"{r}={n:,}" for r, n in top))


def _frames(raw: bytes | str) -> list[dict]:
    if isinstance(raw, str):
        return []
    data = msgpack.unpackb(raw, timestamp=2)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return [data] if isinstance(data, dict) else []


async def _handshake(ws, key: str, secret: str) -> None:
    auth = msgpack.packb({"action": "auth", "key": key, "secret": secret})
    deadline = time.monotonic() + 12.0
    sent = False
    while time.monotonic() < deadline:
        for item in _frames(await asyncio.wait_for(ws.recv(), timeout=10.0)):
            t, msg = item.get("T"), str(item.get("msg", ""))
            if t == "error":
                raise RuntimeError(f"code={item.get('code')} {msg}")
            if t == "success" and msg == "authenticated":
                return
            if t == "success" and msg == "connected" and not sent:
                await ws.send(auth)
                sent = True
    raise RuntimeError("no authenticated ack")


async def run(rec: Recorder, key: str, secret: str, url: str,
              stop: asyncio.Event) -> None:
    backoff = RECONNECT_BASE_SEC
    while not stop.is_set():
        try:
            async with websockets.connect(url, max_size=2**23) as ws:
                await _handshake(ws, key, secret)
                await ws.send(msgpack.packb(
                    {"action": "subscribe", "trades": ["*"]}))
                logger.info("connected + subscribed trades=['*']")
                backoff = RECONNECT_BASE_SEC
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except TimeoutError:
                        rec.tick()
                        continue
                    for item in _frames(raw):
                        t = item.get("T")
                        if t == "t":
                            rec.on_trade(item)
                        elif t == "subscription":
                            trades = item.get("trades") or []
                            rec.acked_wide = "*" in [str(s) for s in trades]
                            logger.info("subscription ack: trades=%s", trades)
                        elif t == "error":
                            logger.error("server error: %s", item)
                    rec.tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if stop.is_set():
                break
            rec.reconnects += 1
            logger.warning("stream error (%s); reconnecting in %.0fs",
                           exc, backoff)
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except TimeoutError:
                pass
            backoff = min(backoff * 2, RECONNECT_MAX_SEC)


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--env-file", default=None)
    p.add_argument("--until", default="21:00",
                   help="UTC HH:MM self-stop (default 21:00 = 17:00 ET)")
    p.add_argument("--url", default=OPRA_WS_URL)
    a = p.parse_args()
    if a.env_file:
        for line in Path(a.env_file).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    key = os.environ.get("APCA_API_KEY_ID", "")
    secret = os.environ.get("APCA_API_SECRET_KEY", "")
    if not key or not secret:
        raise SystemExit("APCA_API_KEY_ID / APCA_API_SECRET_KEY not set")

    hh, _, mm = a.until.partition(":")
    now = datetime.now(UTC)
    stop_at = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    seconds_left = (stop_at - now).total_seconds()
    if seconds_left <= 0:
        raise SystemExit(f"--until {a.until} is in the past (UTC now {now:%H:%M})")

    rec = Recorder(Path(a.out).expanduser())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    loop.call_later(seconds_left, stop.set)
    logger.info("recording to %s until %s UTC (%.0f min)",
                a.out, a.until, seconds_left / 60)
    try:
        await run(rec, key, secret, a.url, stop)
    finally:
        rec.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
