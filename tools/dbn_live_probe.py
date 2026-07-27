"""Prove the Databento lane against a real socket WITHOUT touching production.

This runs the real `DatabentoFlowStream` with the real `runner.meta_for`, in its
own process. It does not read `options_flow.provider`, does not touch the
collector, and does not write to any table — so it can be run while the Alpaca
lane is live and owning its own socket.

Two modes:

  * **live** (default) — connect now, whole market. Off-hours this costs nothing
    and still exercises auth, subscription, the gateway ack and the full
    symbology delivery (1,957,342 mappings, ~25s, measured 2026-07-27).

  * **--replay-from** — intraday replay. The live gateway resends the last ≤24h
    from a start time, through this same code path. Two things only replay can
    prove: the callback chain on a real session's tape, and the slow-reader path
    (replay arrives as fast as the gateway will push it, which is a harder test
    than real time). ⚠️ The floor snaps to a UTC midnight — 24-48h back depending
    on the hour — and the gateway names it on rejection. A Friday session is
    already gone by Sunday night; the usable slot is a weekday after the close.

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


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seconds", type=float, default=90.0,
                   help="how long to stay connected (default 90)")
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

    got: list[dict] = []
    # The REAL meta resolver, not a stub: it is the per-record hot path and the
    # thing most likely to reject every print for an uninteresting reason.
    runner = OptionsFlowRunner.__new__(OptionsFlowRunner)
    stream = DatabentoFlowStream(
        key=key,
        on_prints=got.extend,
        meta_for=runner.meta_for,
        replay_from=args.replay_from,
    )

    mode = f"replay from {args.replay_from}" if args.replay_from else "live now"
    print(f"[probe] {mode}; WHOLE MARKET (ALL_SYMBOLS); {args.seconds:.0f}s\n")

    def _mapped() -> int:
        try:
            return len(stream._client.symbology_map)  # type: ignore[union-attr]
        except Exception:
            return 0

    t0 = time.monotonic()
    mapped = 0
    task = asyncio.create_task(stream.run())
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=args.seconds)
    except asyncio.TimeoutError:
        # BEFORE stop(): run()'s finally clears _client, so reading the
        # symbology map afterwards always reported 0 and the probe cheerfully
        # printed "fully mapped" next to it.
        mapped = _mapped()
        stream.stop()
        await asyncio.wait_for(task, timeout=15)
    except Exception as exc:
        print(f"[probe] run() RAISED: {type(exc).__name__}: {exc}")
    mapped = mapped or _mapped()
    elapsed = time.monotonic() - t0

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

    print("\n[probe] SUBSCRIPTION:", end=" ")
    if stream.subscription_acks:
        print(f"acked ({stream.subscription_acks}), {mapped:,} symbols mapped. "
              f"A healthy off-hours run looks exactly like this: acked, fully "
              f"mapped, zero data.")
    else:
        print("NOT ACKED. Measured latency for a good one is ~17ms at any hour, "
              "so this is a rejection — read the gateway error above.")
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # protocol logs the gateway's own SystemMsg/ErrorMsg records, which is half
    # the point of running this. The CLIENT logger is silenced: it emits one
    # INFO line per symbol mapping, and ALL_SYMBOLS delivers ~1.96M of them.
    logging.getLogger("databento.live.protocol").setLevel(logging.DEBUG)
    logging.getLogger("databento.live.client").setLevel(logging.WARNING)
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
