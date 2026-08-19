#!/usr/bin/env python
"""Drive the REAL market-stream hub (brokers/market_stream.py) against a replay
equity_rs proxy and measure whether it keeps up — the hub-side half of the
equity tape A/B (References/EQUITY_COLLECTOR_RS_SPEC.md).

Setup (three processes, scratch DB, private ports — never the live :8767):
  tools/equity_db_init.py /tmp/rs.db
  tools/equity_tape_replay.py data/equity_tape_raw/<date>.mpk.zst --start 15:50 --end 16:00 --speed 1 --port 8770
  equity_rs --replay --url ws://127.0.0.1:8770 --db /tmp/rs.db --bars-db /tmp/bars.db --proxy-port 8768 --profile-dir /tmp/empty
then one harness per mode, all at once (each is its own proxy client with its own queue):
  HARNESS_SYMBOLS=syms.txt tools/hub_replay_harness.py {count|real|noalert}
    count    — same connection, handler replaced by a counter (= what the proxy offered)
    real     — the hub as in prod (alert DB writes + notify stubbed; nothing fires for real)
    noalert  — the hub with _check_price_alerts stubbed (isolates that path)
  msgs(real) < msgs(count) ⇒ the hub dropped the difference at the proxy queue.
HARNESS_SYMBOLS = file of symbols to pin (default: tonight's alerts ∪ watchlists; the
prod set at record time is in the tape's own subscription ack — grep the equity_rs log).
2026-08-18 close, 150 syms: before the alert-cache fix real=635k of 2.12M (70% dropped,
91% busy, alert check 2.04 ms/print); after: 2.12M, 0 dropped, 5% busy, 3 µs/check.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections import Counter
from pathlib import Path

REPO = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, REPO)
os.chdir(REPO)
os.environ["EQUITY_TAPE_EXTERNAL_COLLECTOR"] = "1"
os.environ["EQUITY_TAPE_PROXY_URL"] = os.environ.get("HARNESS_PROXY_URL", "ws://127.0.0.1:8768")

mode = sys.argv[1]
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("brokers.market_stream").setLevel(logging.ERROR)  # hold/absurd warnings are noise here

from brokers.market_stream import AlpacaMarketStreamHub, _parse_alpaca_frame  # noqa: E402


async def _noop_notify(*a, **k):
    return None


import api.alert_checker as _ac  # noqa: E402
import signals.notify as _n  # noqa: E402

_n.notify = _noop_notify
_ac.mark_alert_triggered = lambda *a, **k: None
_ac.rearm_repeat_alert = lambda *a, **k: None


def watch_set() -> set[str]:
    f = os.environ.get("HARNESS_SYMBOLS")
    if f:
        return {ln.strip() for ln in open(f) if ln.strip()}
    from config.settings import get_watchlists_state
    from db.chart_features import list_price_alerts

    watch = {str(a["symbol"]).upper() for a in list_price_alerts(active_only=True)}
    for wl in get_watchlists_state().get("lists") or []:
        watch.update(str(s).upper() for s in (wl.get("symbols") or []))
    return watch


class Meter:
    def __init__(self):
        self.frames = 0
        self.by_type = Counter()
        self.handler_s = 0.0
        self.alert_calls = 0
        self.alert_s = 0.0
        self.trades = 0
        self.first_wall = None
        self.last_wall = None
        self.sec = Counter()  # msgs per wall-second
        self.sec_busy = Counter()


m = Meter()
hub = AlpacaMarketStreamHub()

_orig_handle = hub._handle_alpaca_message
_orig_alert = hub._check_price_alerts


async def _handle(raw):
    now = time.monotonic()
    if m.first_wall is None:
        m.first_wall = now
    m.last_wall = now
    m.frames += 1
    items = _parse_alpaca_frame(raw)
    for it in items:
        m.by_type[it.get("T")] += 1
    m.sec[int(now)] += len(items)
    if mode == "count":
        return
    t0 = time.perf_counter()
    await _orig_handle(raw)
    dt = time.perf_counter() - t0
    m.handler_s += dt
    m.sec_busy[int(now)] += dt


async def _alert(symbol, prev, current):
    m.alert_calls += 1
    t0 = time.perf_counter()
    await _orig_alert(symbol, prev, current)
    m.alert_s += time.perf_counter() - t0


hub._handle_alpaca_message = _handle
if mode == "real":
    hub._check_price_alerts = _alert
else:
    async def _skip(symbol, prev, current):
        m.alert_calls += 1
    hub._check_price_alerts = _skip


async def main():
    syms = watch_set()
    print(f"[{mode}] watch set = {len(syms)} symbols", flush=True)
    await hub.ensure_symbols(syms)  # seeds baselines via Alpaca REST, sets _subscribed
    print(f"[{mode}] baselines seeded for {len(hub._quotes)}; connecting to proxy…", flush=True)
    await hub.start()
    last_report = time.monotonic()
    while True:
        await asyncio.sleep(1)
        now = time.monotonic()
        if m.first_wall is not None:
            if now - m.last_wall > 90:  # tape ended
                break
        if now - last_report >= 30 and m.first_wall is not None:
            last_report = now
            s = int(now) - 1
            print(f"[{mode}] t+{now - m.first_wall:6.0f}s frames={m.frames} msgs={sum(m.by_type.values())} "
                  f"last1s={m.sec.get(s, 0)}/s busy={m.sec_busy.get(s, 0) * 100:.0f}% "
                  f"alerts={m.alert_calls} alert_s={m.alert_s:.1f}", flush=True)
        if m.first_wall is None and now - last_report > 600:
            print(f"[{mode}] still waiting for tape…", flush=True)
            last_report = now
    wall = (m.last_wall - m.first_wall) if m.first_wall else 0
    print(f"[{mode}] DONE wall={wall:.0f}s frames={m.frames} msgs={dict(m.by_type)} "
          f"handler_s={m.handler_s:.1f} busy={m.handler_s / wall * 100 if wall else 0:.0f}% "
          f"alert_calls={m.alert_calls} alert_s={m.alert_s:.1f} "
          f"alert_mean_ms={m.alert_s / m.alert_calls * 1000 if m.alert_calls else 0:.2f}", flush=True)
    peak = max(m.sec.values()) if m.sec else 0
    print(f"[{mode}] peak msgs/s={peak}; top busy seconds=" +
          str(sorted(((round(b, 2), m.sec[s]) for s, b in m.sec_busy.items()), reverse=True)[:5]), flush=True)
    await hub.stop()


asyncio.run(main())
