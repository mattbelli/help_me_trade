"""Replay a captured OPRA tape through the stream's frame handler, offline.

Companion to `scripts/opra_rate_probe.py --capture`: benchmark or regression-test
the hot path against REAL market-hours frames without waiting for an open. Reports
throughput and the projected share of one core at the tape's recorded rate.

    uv run python scripts/opra_replay_bench.py research/options_flow/tape_X.mpk

Meta lookup returns a fixed record for every OCC (worst case: every trade takes
the full normalize path); pass --no-meta to model the skip path instead.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import msgpack

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signals.options_flow.stream import OptionsFlowStream  # noqa: E402

_META = {
    "occ_symbol": "REPLAY",
    "underlying": "REPLAY",
    "right": "call",
    "strike": 100.0,
    "expiry": "2099-01-01",
    "dte": 30,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", help="file written by opra_rate_probe.py --capture")
    ap.add_argument("--no-meta", action="store_true", help="model the no-meta skip path")
    args = ap.parse_args()

    frames: list[bytes] = []
    first_ns = last_ns = 0
    with Path(args.capture).open("rb") as fh:
        for recv_ns, raw in msgpack.Unpacker(fh, use_list=False, raw=False):
            if not first_ns:
                first_ns = recv_ns
            last_ns = recv_ns
            frames.append(raw)
    if not frames:
        raise SystemExit("empty capture")
    tape_secs = max((last_ns - first_ns) / 1e9, 1e-9)

    meta = None if args.no_meta else _META
    stream = OptionsFlowStream(
        key="k",
        secret="s",
        on_prints=lambda b: None,
        get_subscription_set=lambda: [],
        meta_for=lambda occ: meta,
    )
    start = time.perf_counter()
    for raw in frames:
        stream._handle_frame(raw)
    dur = time.perf_counter() - start

    us_per_msg = dur / stream.msgs * 1e6
    recorded_rate = stream.msgs / tape_secs
    print(f"frames: {len(frames):,}  msgs: {stream.msgs:,}  prints: {stream.prints:,}")
    print(f"tape: {tape_secs:.0f}s at {recorded_rate:,.0f} msgs/s recorded")
    print(f"replay: {dur:.2f}s = {stream.msgs / dur:,.0f} msgs/s ({us_per_msg:.2f} us/msg)")
    print(f"projected: {recorded_rate * us_per_msg / 1e4:.1f}% of one core at recorded rate")


if __name__ == "__main__":
    main()
