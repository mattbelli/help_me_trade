"""Prove the Databento lane against a real socket WITHOUT touching production.

This runs the real `DatabentoFlowStream` with the real `runner.meta_for`, in its
own process. It does not read `options_flow.provider`, does not touch the
collector, and does not write to any table — so it can be run while the Alpaca
lane is live and owning its own socket.

Two modes:

  * **live** (default) — connect now. Run off-hours this is the cheap test that
    settles the one premise the arming check is still hedging: does the gateway
    ack the subscription and deliver symbol mappings when there is NO market
    data? Everything downstream of that assumption is currently guesswork, and
    `dbn_stream.FeedLiveness` raises on a missing ack only during RTH precisely
    because nobody has ever looked.

  * **--replay-from** — intraday replay. The live gateway resends the last ≤24h
    from a start time, through this same code path. Two things only replay can
    prove: the callback chain on a real session's tape, and the slow-reader path
    (replay arrives as fast as the gateway will push it, which is a harder test
    than real time). ⚠️ The window is the last 24 HOURS, and on weekends the
    gateway reports less — so a Friday session is unreachable by Sunday night.
    The usable slot is a weekday after the close, replaying that day's open.

Usage:
  python tools/dbn_live_probe.py --seconds 90
  python tools/dbn_live_probe.py --replay-from 2026-07-27T13:30:00Z --seconds 300
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

DEFAULT_SYMBOLS = "SPY,QQQ,TSLA,NVDA,AAPL"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seconds", type=float, default=90.0,
                   help="how long to stay connected (default 90)")
    p.add_argument("--symbols", default=DEFAULT_SYMBOLS,
                   help="parent symbols, comma separated")
    p.add_argument("--replay-from", default=None,
                   help="ISO start time for intraday replay (last 24h only)")
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    from signals.options_flow.dbn_stream import DatabentoFlowStream
    from signals.options_flow.runner import OptionsFlowRunner

    key = str(os.environ.get("DATABENTO_API_KEY") or "").strip()
    if not key:
        print("DATABENTO_API_KEY is unset", file=sys.stderr)
        return 2

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    got: list[dict] = []
    # The REAL meta resolver, not a stub: it is the per-record hot path and the
    # thing most likely to reject every print for an uninteresting reason.
    runner = OptionsFlowRunner.__new__(OptionsFlowRunner)
    stream = DatabentoFlowStream(
        key=key,
        on_prints=got.extend,
        get_subscription_set=lambda: symbols,
        meta_for=runner.meta_for,
        replay_from=args.replay_from,
    )

    mode = f"replay from {args.replay_from}" if args.replay_from else "live now"
    print(f"[probe] {mode}; {len(symbols)} parents; {args.seconds:.0f}s\n")

    t0 = time.monotonic()
    task = asyncio.create_task(stream.run())
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=args.seconds)
    except asyncio.TimeoutError:
        stream.stop()
        await asyncio.wait_for(task, timeout=15)
    except Exception as exc:
        print(f"[probe] run() RAISED: {type(exc).__name__}: {exc}")
    elapsed = time.monotonic() - t0

    mapped = 0
    try:
        mapped = len(stream._client.symbology_map)  # type: ignore[union-attr]
    except Exception:
        pass

    print(f"\n[probe] {elapsed:.0f}s elapsed")
    for k in ("msgs", "data_msgs", "prints", "subscription_acks",
              "unresolved_symbols", "skipped_no_meta", "slow_reader_warnings",
              "skipped_by_gateway", "symbol_resolution_failures",
              "callback_errors", "reconnects", "reconnect_gap_s",
              "dropped_prints"):
        print(f"  {k:<28} {getattr(stream, k, '-')}")
    print(f"  {'symbology_map size':<28} {mapped}")
    print(f"  {'prints delivered downstream':<28} {len(got)}")
    if got:
        with_book = sum(1 for p in got if p.get("nbbo"))
        print(f"  {'of which carried an NBBO':<28} {with_book} "
              f"({with_book / len(got):.1%})")

    if args.replay_from:
        print("\n[probe] REPLAY:", end=" ")
        if stream.reconnects:
            print("⚠️ RECONNECTED — databento re-subscribes with start=None, so "
                  "this stopped being a replay and became a live session. The "
                  "numbers above are NOT the replayed window.")
        elif not stream.data_msgs:
            print("no records. Either the window holds no data (a weekend, an "
                  "overnight) or the start was rejected — the gateway names its "
                  "floor in the log above: 'Invalid start time. Must be … or "
                  "later'. Measured 2026-07-27: the floor snaps to a UTC "
                  "midnight, so it runs 24-48h back, and a Friday session is "
                  "already gone by Sunday night.")
        else:
            print(f"{stream.data_msgs:,} records replayed.")
        return 0

    # The question this probe exists to answer. State it as a finding, not a
    # pass/fail: an unacked subscription off-hours is not necessarily a fault,
    # it is the thing we do not know.
    print("\n[probe] ACK PREMISE:", end=" ")
    if stream.subscription_acks or mapped:
        print(f"mappings/acks DO arrive (acks={stream.subscription_acks}, "
              f"mapped={mapped}) — the RTH-only hedge in FeedLiveness.check can "
              f"be dropped and the ack check can raise whenever it fires.")
    else:
        print("NO ack and NO symbol mappings in this window. If this was a "
              "closed market, the RTH-only hedge is CORRECT and must stay — "
              "raising off-hours would restart the collector every 60s.")
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # databento logs the gateway's own SystemMsg/ErrorMsg records, which is
    # half the point of running this at all.
    logging.getLogger("databento").setLevel(logging.DEBUG)
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
