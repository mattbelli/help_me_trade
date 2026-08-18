#!/usr/bin/env python
"""Run the Python equity collector against a replay server — the A/B baseline.

Dials tools/equity_tape_replay.py instead of Alpaca, writes to a scratch DB,
runs on the tape's virtual clock, and exits at end-of-tape with a stats line.
Only the tape → detectors → equity_tape_events + fusion_spot_cache path runs
(plus the local proxy on an alternate port): no convergence pushes, no
abnormality engine, no broker — the same scope the Rust collector implements,
so `tools/equity_tape_ab.py` can diff the two DBs.

Run:
  .venv/bin/python tools/equity_tape_replay.py data/equity_tape_raw/2026-08-18.mpk.zst --speed 0 &
  .venv/bin/python tools/equity_collector_replay.py --db /tmp/py.db
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="ws://127.0.0.1:8770")
    ap.add_argument("--db", required=True, type=Path, help="scratch trading.db path (created)")
    ap.add_argument("--proxy-port", type=int, default=8768)
    args = ap.parse_args(argv)

    # DEFAULT_DB_PATH is read at import time — set the override before any db import.
    os.environ["TRADING_DB_PATH"] = str(args.db)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    import asyncio
    import logging

    from signals.equity_stream.runner import EquityTapeRunner

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
    log = logging.getLogger("equity_collector_replay")

    runner = EquityTapeRunner(
        key="replay",
        secret="replay",
        replay=True,
        url=args.url,
        proxy_port=args.proxy_port,
        db_path=args.db,
    )

    async def run() -> None:
        proxy = asyncio.create_task(runner.proxy.serve())
        t0 = time.monotonic()
        await runner.stream.run()
        elapsed = time.monotonic() - t0
        proxy.cancel()
        total = sum(runner.stream.msgs.values())
        log.info(
            "replay finished: msgs=%d t=%d q=%d in %.1fs (%.0f msgs/s) | events=%d "
            "sweeps=%d velocity=%d spots=%d callback_errors=%d",
            total,
            runner.stream.msgs.get("t", 0),
            runner.stream.msgs.get("q", 0),
            elapsed,
            total / elapsed if elapsed else 0.0,
            runner._persisted,
            runner.sweeps.emitted,
            runner.velocity.emitted,
            runner._spots_flushed,
            runner.stream.callback_errors,
        )

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
