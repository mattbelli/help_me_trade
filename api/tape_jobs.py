"""Equity tape flush loop (+ the equity-proxy probe). Extracted verbatim from
``api/main`` (strangler round 3); ``api/main``'s lifespan starts the loop,
``api/background_jobs`` registers its heartbeat. Ownership of the tape itself
flips to the standalone collector via EQUITY_TAPE_EXTERNAL_COLLECTOR.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime

from api.hub import market_hub
from api.jobs import mark_alive
from brokers.factory import get_broker
from trading.market_hours import _ET, _SESSION_OPEN

logger = logging.getLogger(__name__)


# Same ownership pattern for the STOCKS socket: "1" = tools/equity_collector.py
# owns the wildcard tape (and runs market-wide tape detection + the confluence
# watch itself); the hub dials the collector's local proxy and this process's
# tape flush loop stands down.
_EQUITY_TAPE_EXTERNAL_COLLECTOR = os.environ.get(
    "EQUITY_TAPE_EXTERNAL_COLLECTOR", "0"
).strip() not in ("0", "false", "no")


_EQUITY_TAPE_FLUSH_SECONDS = 2.0


async def _probe_equity_proxy() -> bool:
    """True iff the equity collector's local proxy accepts a TCP connection —
    i.e. the collector PROCESS is up (upstream health is the proxy's own job)."""
    from urllib.parse import urlparse

    parsed = urlparse(os.environ.get("EQUITY_TAPE_PROXY_URL", "ws://127.0.0.1:8767"))
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(parsed.hostname or "127.0.0.1", parsed.port or 8767),
            timeout=1.5,
        )
    except Exception:
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True


_EQUITY_PROXY_PROBE_SECONDS = 15.0


async def _equity_tape_flush_loop() -> None:
    """Persist large-print tape events queued by the stream hub's detector,
    then run them through the confluence watch (any 2 watched things)."""
    from db.equity_tape import insert_event as _insert_tape_event
    from signals.options_flow.convergence import process_events

    if _EQUITY_TAPE_EXTERNAL_COLLECTOR:
        logger.info(
            "equity tape: external collector owns detection "
            "(EQUITY_TAPE_EXTERNAL_COLLECTOR=1); flush loop standing down"
        )

    collector_reachable = True
    next_probe_mono = 0.0
    while True:
        await asyncio.sleep(_EQUITY_TAPE_FLUSH_SECONDS)
        if _EQUITY_TAPE_EXTERNAL_COLLECTOR:
            # The collector persists + converges market-wide; draining the
            # hub's (bounded, maxlen=256) detector here would double-process.
            # The heartbeat now means "the collector's proxy is reachable" —
            # a dead collector must surface as a stale beat on /health, not a
            # green board over a frozen watchlist (2026-07-15 Opus review).
            now_mono = time.monotonic()
            if now_mono >= next_probe_mono:
                next_probe_mono = now_mono + _EQUITY_PROXY_PROBE_SECONDS
                reachable = await _probe_equity_proxy()
                if reachable != collector_reachable:
                    (logger.info if reachable else logger.error)(
                        "equity tape: collector proxy %s",
                        "reachable again"
                        if reachable
                        else "UNREACHABLE — watchlist stream is down until "
                        "hmt-equity-collector returns (or flip the flag to 0)",
                    )
                collector_reachable = reachable
            if collector_reachable:
                mark_alive("equity_tape_flush")
            continue
        try:
            detector = getattr(market_hub, "tape_detector", None)
            events: list[dict] = []
            if detector is not None:
                while detector.pending:
                    events.append(detector.pending.popleft())
            for ev in events:
                await asyncio.to_thread(_insert_tape_event, ev)

            if events:
                try:
                    broker = get_broker("alpaca")
                except Exception:
                    broker = None
                if broker is not None:
                    now_et = datetime.now(_ET)
                    open_min = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
                    now_min = now_et.hour * 60 + now_et.minute
                    conv = await process_events(
                        broker,
                        events,
                        today_et=now_et.date().isoformat(),
                        # Real minutes (negative pre-market): rvol_from_profile returns None
                        # for <0 — clamping to 0 compared pre-market volume against the
                        # first 5-min bucket and fabricated 16-22x RVOL (2026-07-10 4AM).
                        minutes_since_open=now_min - open_min,
                    )
                    if conv.get("convergence") or conv.get("pushes"):
                        logger.info(
                            "equity tape: events=%s convergence=%s pushes=%s",
                            len(events),
                            conv.get("convergence", 0),
                            conv.get("pushes", 0),
                        )
        except Exception:
            logger.exception("equity tape: flush failed")
        else:
            mark_alive("equity_tape_flush")
