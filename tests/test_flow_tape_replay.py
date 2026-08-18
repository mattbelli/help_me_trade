"""OPRA tcbbo tape → flow-collector replay → A/B harness (stage 0 of the Rust port).

Pins: the replay clock rule (T0 = first second + 1; ingest in file order while
ts_recv < T; one sweep cycle per virtual second; END_GRACE_S more after the
tape); a synthetic tape yields the block + sweep the live pipeline would; and
two replays of one tape are IDENTICAL under `tools/flow_ab.py` — the property
that makes the Rust collector's diff meaningful.
"""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import databento_dbn as dbn
import pytest

from tools import flow_ab
from tools.flow_collector_replay import EndOfTape, TapeReplay
from trading.market_hours import _ET

ROOT = Path(__file__).resolve().parent.parent
DATE = "2026-08-18"  # a Tuesday: is_trading_day() must hold for the detectors to run
T0 = int(datetime(2026, 8, 18, 10, 0, tzinfo=_ET).timestamp()) * 1_000_000_000
NS = 1_000_000_000
AAPL_C = "AAPL  260918C00200000"  # id 1
MSFT_P = "MSFT  260918P00400000"  # id 2


def _rec(ts: int, iid: int, px: float, sz: int, pub: int, bid: float, ask: float):
    lvl = dbn.ConsolidatedBidAskPair(
        bid_px=int(round(bid * 1e9)), ask_px=int(round(ask * 1e9)),
        bid_sz=1, ask_sz=1, bid_pb=1, ask_pb=1,
    )
    return dbn.CMBP1Msg(
        rtype=dbn.RType.CMBP_1, publisher_id=pub, instrument_id=iid,
        ts_event=ts - 200_000, price=int(round(px * 1e9)), size=sz,
        action=dbn.Action.TRADE, side=dbn.Side.NONE, ts_recv=ts, flags=0, levels=lvl,
    )


def _meta(schema: dbn.Schema) -> dbn.Metadata:
    return dbn.Metadata(
        dataset="OPRA.PILLAR", start=T0, stype_in=dbn.SType.RAW_SYMBOL,
        stype_out=dbn.SType.INSTRUMENT_ID, schema=schema, symbols=["ALL_SYMBOLS"],
        end=T0 + 600 * NS, mappings=[],
    )


def _synthetic_tape(d: Path) -> tuple[Path, Path]:
    """A $150k/300-contract AAPL block at the ask, then a 3-venue MSFT sweep 2s
    later (three prints inside 500ms on publishers 25/32/35, all at the ask),
    then a lone late print 4s on so the clusters close inside the tape."""
    recs = [
        _rec(T0 + int(0.4 * NS), 1, 5.00, 300, 35, 4.90, 5.00),
        _rec(T0 + int(2.100 * NS), 2, 3.00, 40, 25, 2.90, 3.00),
        _rec(T0 + int(2.250 * NS), 2, 3.00, 40, 32, 2.90, 3.00),
        _rec(T0 + int(2.400 * NS), 2, 3.00, 40, 35, 2.90, 3.00),
        _rec(T0 + int(6.000 * NS), 1, 5.10, 1, 35, 5.00, 5.20),
    ]
    tape = d / "t.dbn"
    buf = bytearray(_meta(dbn.Schema.TCBBO).encode())
    for r in recs:
        buf += bytes(r)
    tape.write_bytes(buf)

    defs = d / "d.dbn"
    buf = bytearray(_meta(dbn.Schema.DEFINITION).encode())
    for iid, sym, cls in ((1, AAPL_C, dbn.InstrumentClass.CALL), (2, MSFT_P, dbn.InstrumentClass.PUT)):
        buf += bytes(dbn.InstrumentDefMsg(
            publisher_id=35, instrument_id=iid, ts_event=T0, ts_recv=T0,
            min_price_increment=1_000_000, display_factor=1, raw_symbol=sym,
            asset=sym[:4].strip(), security_type="OPT", instrument_class=cls,
            security_update_action=dbn.SecurityUpdateAction.ADD,
        ))
    defs.write_bytes(buf)
    return tape, defs


def _replay(tape: Path, defs: Path, sandbox: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "flow_collector_replay.py"), DATE,
         "--tape", str(tape), "--defs", str(defs), "--db", str(sandbox / "trading.db"), *extra],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )


def test_replay_clock_rule_ingests_in_file_order_per_virtual_second():
    seen: list[int] = []
    stream = SimpleNamespace(_on_record=lambda r: seen.append(r.ts_recv), _drain=lambda: [])
    runner = SimpleNamespace(on_prints=lambda b: None)
    recs = [SimpleNamespace(ts_recv=T0 + int(f * NS)) for f in (0.4, 0.9, 2.1, 1.7, 2.4)]
    rp = TapeReplay(recs, stream, runner, end_grace_s=2)

    async def drive() -> list[tuple[int, int]]:
        out = []
        try:
            while True:
                await rp.sleep(1.0)
                out.append((rp.now, len(seen)))
        except EndOfTape:
            return out

    ticks = asyncio.run(drive())
    t0 = T0 // NS + 1
    # T0+1: the two sub-second prints; T0+2: nothing new; T0+3: 2.1 then the
    # 1.7 that sat behind it (file order, backwards in time), then 2.4.
    assert ticks[:3] == [(t0, 2), (t0 + 1, 2), (t0 + 2, 5)]
    assert seen == [T0 + int(f * NS) for f in (0.4, 0.9, 2.1, 1.7, 2.4)]
    # tape ends at T0+3 → grace 2 more cycles (T0+4) then EndOfTape before T0+5 runs
    assert ticks[-1][0] == t0 + 3 and rp.tape_end_now == t0 + 2


def test_two_replays_of_one_tape_are_identical(tmp_path: Path):
    tape, defs = _synthetic_tape(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    for sandbox in (a, b):
        proc = _replay(tape, defs, sandbox)
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert "records=5 prints=5 no_meta=0" in proc.stderr

    con = sqlite3.connect(a / "trading.db")
    rows = con.execute(
        "SELECT event_type, occ_symbol, direction, contracts, n_prints FROM options_flow_events ORDER BY ts"
    ).fetchall()
    con.close()
    assert rows == [
        ("block", "AAPL260918C00200000", "buy_side", 300, 1),
        ("sweep", "MSFT260918P00400000", "buy_side", 120, 3),
    ]
    # 10:00 ET is inside the print-lab window: every print lands in the sandbox, not data/
    lab = a / "research" / "print_lab" / f"{DATE}.jsonl"
    assert lab.exists() and len(lab.read_text().splitlines()) == 5

    result = flow_ab.compare(a, b)
    assert result["events"]["a"] == 2
    for bucket in result.values():
        assert not bucket["only_a"] and not bucket["only_b"] and not bucket["changed"], result
    assert flow_ab.main([str(a), str(b)]) == 0


def test_time_window_clips_the_tape(tmp_path: Path):
    tape, defs = _synthetic_tape(tmp_path)
    sandbox = tmp_path / "w"
    proc = _replay(tape, defs, sandbox, "--start", "10:00", "--end", "10:00")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "records=0 prints=0" in proc.stderr
    assert not (sandbox / "research").exists()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
