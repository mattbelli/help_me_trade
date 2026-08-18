#!/usr/bin/env python
"""Diff two collector runs of the same recorded tape — the Python↔Rust A/B.

Compares ``equity_tape_events`` (by id; every content column, ignoring
``ingest_seq``/``created_at`` which are write-order/wall-clock) and
``fusion_spot_cache`` (by symbol; price + ts, ignoring ``updated_at``).
Exit 0 = identical, 1 = differences (printed, first ``--show`` per bucket).

Run: .venv/bin/python tools/equity_tape_ab.py /tmp/py.db /tmp/rs.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

EVENT_COLS = (
    "ts", "symbol", "event_type", "exchange", "price", "size", "notional_usd",
    "direction", "direction_basis", "conditions", "reason", "caveat", "detail",
)


def _norm(col: str, v):
    # JSON columns compare structurally (key order / whitespace are not diffs).
    if col in ("conditions", "detail") and isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _events(path: Path) -> dict[str, dict]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        if not con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='equity_tape_events'"
        ).fetchone():
            return {}
        return {
            r["id"]: {c: _norm(c, r[c]) for c in EVENT_COLS}
            for r in con.execute(f"SELECT id, {', '.join(EVENT_COLS)} FROM equity_tape_events")
        }
    finally:
        con.close()


def _spots(path: Path) -> dict[str, tuple[str, str]]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if not con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fusion_spot_cache'"
        ).fetchone():
            return {}
        return {
            s: (p, t) for s, p, t in con.execute("SELECT symbol, price, ts FROM fusion_spot_cache")
        }
    finally:
        con.close()


def compare(a: Path, b: Path) -> dict:
    ea, eb = _events(a), _events(b)
    only_a = sorted(set(ea) - set(eb))
    only_b = sorted(set(eb) - set(ea))
    changed = []
    for eid in sorted(set(ea) & set(eb)):
        diffs = {c: (ea[eid][c], eb[eid][c]) for c in EVENT_COLS if ea[eid][c] != eb[eid][c]}
        if diffs:
            changed.append((eid, diffs))
    sa, sb = _spots(a), _spots(b)
    spot_only_a = sorted(set(sa) - set(sb))
    spot_only_b = sorted(set(sb) - set(sa))
    spot_changed = [(s, sa[s], sb[s]) for s in sorted(set(sa) & set(sb)) if sa[s] != sb[s]]
    return {
        "events": {"a": len(ea), "b": len(eb), "only_a": only_a, "only_b": only_b, "changed": changed},
        "spots": {
            "a": len(sa), "b": len(sb), "only_a": spot_only_a, "only_b": spot_only_b,
            "changed": spot_changed,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    ap.add_argument("--show", type=int, default=10)
    args = ap.parse_args(argv)
    r = compare(args.a, args.b)
    ev, sp = r["events"], r["spots"]
    print(f"events: A={ev['a']} B={ev['b']} only_A={len(ev['only_a'])} only_B={len(ev['only_b'])} changed={len(ev['changed'])}")
    for eid in ev["only_a"][: args.show]:
        print(f"  only A: {eid}")
    for eid in ev["only_b"][: args.show]:
        print(f"  only B: {eid}")
    for eid, diffs in ev["changed"][: args.show]:
        print(f"  changed {eid}: {diffs}")
    print(f"spots:  A={sp['a']} B={sp['b']} only_A={len(sp['only_a'])} only_B={len(sp['only_b'])} changed={len(sp['changed'])}")
    for s in sp["only_a"][: args.show]:
        print(f"  only A: {s}")
    for s in sp["only_b"][: args.show]:
        print(f"  only B: {s}")
    for s, va, vb in sp["changed"][: args.show]:
        print(f"  changed {s}: {va} vs {vb}")
    identical = not (ev["only_a"] or ev["only_b"] or ev["changed"] or sp["only_a"] or sp["only_b"] or sp["changed"])
    print("IDENTICAL" if identical else "DIFFERENT")
    return 0 if identical else 1


if __name__ == "__main__":
    sys.exit(main())
