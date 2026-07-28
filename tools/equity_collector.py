#!/usr/bin/env python
"""Standalone equity-tape collector process (hmt-equity-collector.service).

Owns the Alpaca stocks v2 websocket (1 connection/account) and runs the
market-wide tape pipeline from signals/equity_stream/runner.py — wildcard
trades+quotes, block/dark-pool/sweep/velocity detection, and the local proxy
that serves the API's market hub its watchlist slice. All output lands in
data/trading.db (WAL); the API keeps serving reads untouched.

Interlock: refuses to start unless EQUITY_TAPE_EXTERNAL_COLLECTOR=1 in the
shared .env — the same flag points the API's hub at the local proxy instead of
upstream and stands down its in-process tape flush. One switch, one stocks
socket, never two (Alpaca 406s the second connection).

Run: .venv/bin/python tools/equity_collector.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# config.settings loads .env on import — everything below sees the same
# environment the API does.
from config.settings import alpaca_config  # noqa: E402
from signals.equity_stream.runner import EquityTapeRunner  # noqa: E402

logger = logging.getLogger("equity_collector")


def _flag(name: str) -> bool:
    return os.environ.get(name, "0").strip() not in ("0", "false", "no")


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # The API's 15s health probe is a bare TCP connect; websockets.server logs
    # each one as a full InvalidMessage traceback. Loopback-only listener —
    # silence the handshake noise.
    logging.getLogger("websockets.server").setLevel(logging.CRITICAL)

    if not _flag("EQUITY_TAPE_EXTERNAL_COLLECTOR"):
        logger.error(
            "EQUITY_TAPE_EXTERNAL_COLLECTOR is not 1 — the API hub still owns "
            "the stocks socket; refusing to start a second connection"
        )
        return 2
    cfg = alpaca_config()
    key = str(cfg.get("api_key_id") or "").strip()
    secret = str(cfg.get("api_secret_key") or "").strip()
    if not key or not secret:
        logger.error("Alpaca credentials missing — nothing to stream")
        return 2

    runner = EquityTapeRunner(key=key, secret=secret)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    logger.info("equity collector starting: wildcard tape + local proxy")
    tasks = [
        asyncio.create_task(runner.stream.run(), name="equity-stream"),
        asyncio.create_task(runner.proxy.serve(), name="equity-proxy"),
        asyncio.create_task(runner.flush_loop(), name="equity-flush"),
        asyncio.create_task(runner.spot_flush_loop(), name="equity-spots"),
        asyncio.create_task(runner.stats_loop(), name="equity-stats"),
    ]
    stopper = asyncio.create_task(stop.wait(), name="equity-stop")

    # First exit wins: a stop signal, or any leg dying unexpectedly (systemd
    # Restart=always brings the whole process back — one supervisor, not four).
    done, _ = await asyncio.wait([*tasks, stopper], return_when=asyncio.FIRST_COMPLETED)
    if stopper not in done:
        for t in done:
            exc = t.exception()
            logger.error(
                "equity collector: task %s exited%s — shutting down for systemd restart",
                t.get_name(),
                f" ({exc!r})" if exc else "",
            )

    runner.stop()
    stopper.cancel()
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    logger.info("equity collector stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
