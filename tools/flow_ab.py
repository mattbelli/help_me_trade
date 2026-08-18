#!/usr/bin/env python
"""Diff two flow-collector replay runs of the same tape — the Python↔Rust A/B.

Each side is the sandbox directory of a `tools/flow_collector_replay.py` run
(the --db file's directory). Compared:
  * ``options_flow_events`` by id — every content column except the ones the
    replay does not own or that are write-order/wall-clock: ``detail`` (Alpaca
    enrichment, off), ``rarity_*`` (nightly lookup, absent), ``ingest_seq``,
    ``created_at``. JSON columns (``exchanges``) compare structurally.
  * ``index_pressure_minutes`` by (underlying, date, window, minute), all
    columns but ``updated_at``; ``index_pressure_days`` by (date, window):
    session_type / minutes_observed / syms / coverage_modes / finalized (bool).
  * the print-lab JSONL under ``research/print_lab/`` — same file names, same
    line count, each line structurally equal.
Exit 0 = IDENTICAL, 1 = DIFFERENT (first --show per bucket printed).

Run: .venv/bin/python tools/flow_ab.py /tmp/hmt_flow_ab/py /tmp/hmt_flow_ab/rs
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

EVENT_COLS = (
    "ts", "underlying", "occ_symbol", "right", "strike", "expiry", "dte", "otm_pct",
    "event_type", "contracts", "premium_usd", "vol_oi_ratio", "vol_zscore",
    "direction", "direction_basis", "spread_flag", "n_prints", "exchanges",
    "reason", "caveat", "ret_1d", "ret_5d", "coverage", "minutes_into_session",
    "mid_side",
)
MINUTE_COLS = ("bull_usd", "bear_usd", "unclassified_usd", "n_prints", "coverage")
DAY_COLS = ("session_type", "minutes_observed", "syms", "coverage_modes")


def _ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _has(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None


def _norm(col: str, v):
    if col == "exchanges" and isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _events(db: Path) -> dict[str, dict]:
    con = _ro(db)
    try:
        if not _has(con, "options_flow_events"):
            return {}
        return {
            r["id"]: {c: _norm(c, r[c]) for c in EVENT_COLS}
            for r in con.execute(f"SELECT id, {', '.join(EVENT_COLS)} FROM options_flow_events")
        }
    finally:
        con.close()


def _minutes(db: Path) -> dict[tuple, dict]:
    con = _ro(db)
    try:
        if not _has(con, "index_pressure_minutes"):
            return {}
        return {
            (r["underlying"], r["date"], r["window"], r["minute"]): {c: r[c] for c in MINUTE_COLS}
            for r in con.execute(
                f"SELECT underlying, date, window, minute, {', '.join(MINUTE_COLS)} FROM index_pressure_minutes"
            )
        }
    finally:
        con.close()


def _days(db: Path) -> dict[tuple, dict]:
    con = _ro(db)
    try:
        if not _has(con, "index_pressure_days"):
            return {}
        out = {}
        for r in con.execute(
            f"SELECT date, window, finalized_at, {', '.join(DAY_COLS)} FROM index_pressure_days"
        ):
            d = {c: r[c] for c in DAY_COLS}
            d["finalized"] = r["finalized_at"] is not None
            out[(r["date"], r["window"])] = d
        return out
    finally:
        con.close()


def _print_lab(sandbox: Path) -> dict[str, list]:
    d = sandbox / "research" / "print_lab"
    if not d.is_dir():
        return {}
    out = {}
    for f in sorted(d.glob("*.jsonl")):
        out[f.name] = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    return out


def _diff_keyed(a: dict, b: dict) -> dict:
    only_a = sorted(set(a) - set(b), key=str)
    only_b = sorted(set(b) - set(a), key=str)
    changed = []
    for k in sorted(set(a) & set(b), key=str):
        diffs = {c: (a[k][c], b[k][c]) for c in a[k] if a[k][c] != b[k][c]}
        if diffs:
            changed.append((k, diffs))
    return {"a": len(a), "b": len(b), "only_a": only_a, "only_b": only_b, "changed": changed}


def _diff_lab(a: dict[str, list], b: dict[str, list]) -> dict:
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    changed = []
    for name in sorted(set(a) & set(b)):
        la, lb = a[name], b[name]
        if len(la) != len(lb):
            changed.append((name, {"lines": (len(la), len(lb))}))
            continue
        for i, (x, y) in enumerate(zip(la, lb)):
            if x != y:
                changed.append((name, {f"line {i}": (x, y)}))
                break
    return {
        "a": sum(len(v) for v in a.values()), "b": sum(len(v) for v in b.values()),
        "only_a": only_a, "only_b": only_b, "changed": changed,
    }


def compare(a: Path, b: Path) -> dict:
    da, db_ = a / "trading.db", b / "trading.db"
    return {
        "events": _diff_keyed(_events(da), _events(db_)),
        "minutes": _diff_keyed(_minutes(da), _minutes(db_)),
        "days": _diff_keyed(_days(da), _days(db_)),
        "print_lab": _diff_lab(_print_lab(a), _print_lab(b)),
    }


def _clean(r: dict) -> bool:
    return not (r["only_a"] or r["only_b"] or r["changed"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", type=Path, help="sandbox dir of run A (contains trading.db)")
    ap.add_argument("b", type=Path, help="sandbox dir of run B")
    ap.add_argument("--show", type=int, default=10)
    args = ap.parse_args(argv)
    result = compare(args.a, args.b)
    for name, r in result.items():
        print(
            f"{name}: A={r['a']} B={r['b']} only_A={len(r['only_a'])} "
            f"only_B={len(r['only_b'])} changed={len(r['changed'])}"
        )
        for k in r["only_a"][: args.show]:
            print(f"  only A: {k}")
        for k in r["only_b"][: args.show]:
            print(f"  only B: {k}")
        for k, diffs in r["changed"][: args.show]:
            print(f"  changed {k}: {diffs}")
    identical = all(_clean(r) for r in result.values())
    print("IDENTICAL" if identical else "DIFFERENT")
    return 0 if identical else 1


if __name__ == "__main__":
    sys.exit(main())
