#!/usr/bin/env python
"""Replay a recorded OPRA tcbbo session through the Python flow collector.

The A/B baseline for the Rust port (References/FLOW_COLLECTOR_RS_SPEC.md) and,
on its own, the first deterministic end-to-end run of the flow detectors
against the tape: ``data/databento/opra_tcbbo/<date>.dbn.zst`` (the same
tcbbo records the live gateway sends; symbology from
``data/databento/opra_defs/<date>.dbn.zst`` because ALL_SYMBOLS files ship no
mappings). Runs the same spine (`DatabentoFlowStream._on_record`) and the same
consumer (`OptionsFlowRunner.sweep_loop`) as `hmt-flow-collector`, on a
virtual clock, into a scratch DB. OFF: poll lane, enrichment/broker,
convergence/pushes, pairing sidecar (phase 2), rarity lookup (no table in the
scratch DB → NULL).

Clock rules (a port must follow these exactly to diff IDENTICAL):
  * tape time ``T`` is whole seconds; ``T0 = floor(first record's ts_recv / 1e9) + 1``.
  * every cycle: (1) T += 1 (first cycle: T = T0); (2) ingest records IN FILE
    ORDER while ``ts_recv < T * 1e9`` (records that go backwards in time are
    ingested at once — the file is not strictly ts_recv-ordered); (3) drain the
    stream's pending prints into ``collected_prints``; (4) run one sweep cycle
    with every "now" == T (``time.time()`` == T, ``datetime.now(ET)`` == T).
  * end of tape: keep cycling with no new records for END_GRACE_S more virtual
    seconds (open clusters close, the last rolling check fires), then stop.

Isolation: ``TRADING_DB_PATH`` = --db, so events, index_pressure rows AND the
print-lab JSONL (``<db dir>/research/print_lab/<date>.jsonl``) all land beside
the scratch DB — never in data/. Give every run its own directory.

Run:
  .venv/bin/python tools/flow_collector_replay.py 2026-08-17 --db /tmp/hmt_flow_ab/py/trading.db
  .venv/bin/python tools/flow_collector_replay.py 2026-08-17 --db ... --start 09:30 --end 09:40
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAPE_DIR = ROOT / "data" / "databento" / "opra_tcbbo"
DEFS_DIR = ROOT / "data" / "databento" / "opra_defs"
END_GRACE_S = 120


class EndOfTape(Exception):
    """Raised from the sleep seam once the tape + grace are exhausted."""


class TapeReplay:
    """Drives `OptionsFlowRunner.sweep_loop` from a DBN record iterator.

    Plugged in as the runner's ``sleep`` (advance one virtual second, ingest,
    drain) and ``clock`` (the current virtual second) — see the module doc.
    """

    def __init__(self, records, stream, runner, *, end_grace_s: int = END_GRACE_S) -> None:
        self._records = iter(records)
        self._peek = next(self._records, None)
        self._stream = stream
        self._runner = runner
        self._end_grace_s = end_grace_s
        self.now: int | None = None
        self.start_now: int | None = None
        self.tape_end_now: int | None = None
        self.records = 0
        self.cycles = 0

    def clock(self) -> float:
        return float(self.now if self.now is not None else 0)

    async def sleep(self, _seconds: float) -> None:
        if self._peek is None and self.now is None:
            raise EndOfTape  # empty tape
        if self.now is None:
            self.now = self.start_now = int(self._peek.ts_recv // 1_000_000_000) + 1
        else:
            self.now += 1
        limit_ns = self.now * 1_000_000_000
        while self._peek is not None and self._peek.ts_recv < limit_ns:
            self._stream._on_record(self._peek)
            self.records += 1
            self._peek = next(self._records, None)
        if self._peek is None:
            if self.tape_end_now is None:
                self.tape_end_now = self.now
            elif self.now - self.tape_end_now >= self._end_grace_s:
                raise EndOfTape
        batch = self._stream._drain()
        if batch:
            self._runner.on_prints(batch)
        self.cycles += 1


def _et_window_ns(date_s: str, hhmm: str) -> int:
    from datetime import datetime

    from trading.market_hours import _ET

    h, m = (int(x) for x in hhmm.split(":"))
    y, mo, d = (int(x) for x in date_s.split("-"))
    return int(datetime(y, mo, d, h, m, tzinfo=_ET).timestamp() * 1_000_000_000)


def _tape_records(path: Path, start_ns: int | None, end_ns: int | None):
    """tcbbo records in file order, optionally clipped to [start_ns, end_ns) by ts_recv."""
    import databento

    for rec in databento.DBNStore.from_file(path):
        ts = rec.ts_recv
        if start_ns is not None and ts < start_ns:
            continue
        if end_ns is not None and ts >= end_ns:
            break
        yield rec


def load_symbology(defs_path: Path) -> dict[int, str]:
    """instrument_id → OSI raw_symbol from the day's definition file (start-of-day snapshot)."""
    import databento

    return {rec.instrument_id: rec.raw_symbol for rec in databento.DBNStore.from_file(defs_path)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("date", help="session date YYYY-MM-DD (tape + defs looked up under data/databento/)")
    ap.add_argument("--db", required=True, type=Path, help="scratch trading.db path (its directory is the run's sandbox)")
    ap.add_argument("--start", help="HH:MM ET — skip records before this time of day")
    ap.add_argument("--end", help="HH:MM ET — stop at the first record at/after this time of day")
    ap.add_argument("--tape", type=Path, help=f"tcbbo file (default {TAPE_DIR}/<date>.dbn.zst)")
    ap.add_argument("--defs", type=Path, help=f"definition file (default {DEFS_DIR}/<date>.dbn.zst)")
    args = ap.parse_args(argv)

    tape = args.tape or TAPE_DIR / f"{args.date}.dbn.zst"
    defs = args.defs or DEFS_DIR / f"{args.date}.dbn.zst"
    for p in (tape, defs):
        if not p.exists():
            print(f"missing: {p}", file=sys.stderr)
            return 2

    # DEFAULT_DB_PATH is read at import time — set the override before any db import.
    args.db.parent.mkdir(parents=True, exist_ok=True)
    os.environ["TRADING_DB_PATH"] = str(args.db)
    sys.path.insert(0, str(ROOT))

    import asyncio
    import logging
    import sqlite3
    from types import SimpleNamespace

    from signals.options_flow.dbn_stream import DatabentoFlowStream
    from signals.options_flow.runner import OptionsFlowRunner

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("flow_collector_replay")

    t0 = time.monotonic()
    symbology = load_symbology(defs)
    log.info("symbology: %d instruments from %s in %.1fs", len(symbology), defs.name, time.monotonic() - t0)

    start_ns = _et_window_ns(args.date, args.start) if args.start else None
    end_ns = _et_window_ns(args.date, args.end) if args.end else None

    replay: TapeReplay | None = None
    runner = OptionsFlowRunner(
        watchlist_symbols=lambda: [],
        clock=lambda: replay.clock(),
        sleep=lambda s: replay.sleep(s),
        enrich=False,
    )
    stream = DatabentoFlowStream(key="replay", on_prints=runner.on_prints, meta_for=runner.meta_for)
    # `_on_record` resolves symbols through the live client's symbology_map; the
    # file's stand-in is the definition snapshot.
    stream._client = SimpleNamespace(symbology_map=symbology)
    runner.stream = stream
    replay = TapeReplay(_tape_records(tape, start_ns, end_ns), stream, runner)

    async def run() -> None:
        try:
            await runner.sweep_loop()
        except EndOfTape:
            pass

    t1 = time.monotonic()
    asyncio.run(run())
    elapsed = time.monotonic() - t1

    def _count(sql: str):
        try:
            return con.execute(sql).fetchall()
        except sqlite3.OperationalError:  # table never created: nothing was written
            return []

    con = sqlite3.connect(args.db)
    counts = dict(_count("SELECT event_type, COUNT(*) FROM options_flow_events GROUP BY 1"))
    ip_rows = sum(n for (n,) in _count("SELECT COUNT(*) FROM index_pressure_minutes"))
    con.close()
    log.info(
        "replay finished: records=%d prints=%d no_meta=%d unresolved=%d cycles=%d "
        "virtual=%s..%s in %.1fs (%.0f rec/s) | events=%s index_pressure_minutes=%d",
        replay.records, stream.prints, stream.skipped_no_meta, stream.unresolved_symbols,
        replay.cycles, replay.start_now, replay.now,
        elapsed, replay.records / elapsed if elapsed else 0.0, counts, ip_rows,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
