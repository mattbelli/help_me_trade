#!/usr/bin/env python3
"""Stage 1 of the SPX structural-strikes study: condense Databento statistics DBN files
into one queryable daily OI table.

Sources (all already purchased, data/databento/):
  - OPRA-20260719-AJ9Q3FL4CR/  SPX+SPXW statistics 2013-04 -> 2023-01 (daily files)
  - OPRA-20260719-TPUHTM5E5V/  SPX+SPXW statistics 2025-01 -> 2026-07 (daily files)
  - glbx_stats_sme_opt_2025-2026.dbn.zst  CME SME options (single file)

Output: data/oi_walls.db, table oi_daily(date, source, root, expiry, cp, strike, oi)
— open interest only (stat_type=9); settlements stay in the raw DBN for later stages.
Idempotent + resumable: processed files tracked in `done_files`; re-runs skip them.

Usage:
    uv run python tools/oi_condense.py --smoke   # 3 known files, print parsed samples
    uv run python tools/oi_condense.py           # full run (hours; nice it)
"""

import argparse
import sqlite3
from datetime import date
from pathlib import Path

import databento as db

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = DATA / "oi_walls.db"
OPRA_DIRS = [DATA / "databento" / "OPRA-20260719-AJ9Q3FL4CR",
             DATA / "databento" / "OPRA-20260719-TPUHTM5E5V"]
SME_FILE = DATA / "databento" / "glbx_stats_sme_opt_2025-2026.dbn.zst"
GLBX_MONTH = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
              "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}


def parse_opra(sym: str):
    """'SPXW  221230P03390000' -> (root, '2022-12-30', 'P', 3390.0)"""
    tail = sym[-15:]
    root = sym[:-15].strip()
    if not root or not tail[:6].isdigit() or tail[6] not in "CP":
        return None
    expiry = f"20{tail[:2]}-{tail[2:4]}-{tail[4:6]}"
    return root, expiry, tail[6], int(tail[7:]) / 1000.0


def parse_sme(sym: str):
    """'SMEM6 P6180' -> ('SME', '2026-06', 'P', 6180.0); month-end product, month granularity."""
    parts = sym.split()
    if len(parts) != 2 or parts[1][0] not in "CP":
        return None
    contract = parts[0]
    mcode, ycode = contract[-2], contract[-1]
    if mcode not in GLBX_MONTH or not ycode.isdigit():
        return None
    expiry = f"202{ycode}-{GLBX_MONTH[mcode]:02d}"
    try:
        strike = float(parts[1][1:])
    except ValueError:
        return None
    return contract[:-2], expiry, parts[1][0], strike


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(OUT)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS oi_daily (
        date TEXT NOT NULL, source TEXT NOT NULL, root TEXT NOT NULL,
        expiry TEXT NOT NULL, cp TEXT NOT NULL, strike REAL NOT NULL,
        oi INTEGER NOT NULL,
        PRIMARY KEY (date, source, root, expiry, cp, strike))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oi_expiry ON oi_daily (expiry, date)")
    conn.execute("CREATE TABLE IF NOT EXISTS done_files (path TEXT PRIMARY KEY)")
    return conn


def condense_file(conn: sqlite3.Connection, path: Path, source: str, parser, day: str | None):
    df = db.DBNStore.from_file(path).to_df(map_symbols=True)
    if df.empty:  # holiday / nothing published yet — an empty df has no columns to filter
        conn.execute("INSERT OR REPLACE INTO done_files (path) VALUES (?)", (str(path),))
        conn.commit()
        return 0
    oi = df[df["stat_type"] == 9]
    rows = {}
    for ts, sym, qty in zip(oi.index, oi["symbol"], oi["quantity"]):
        parsed = parser(sym)
        if parsed is None or qty is None or qty <= 0:
            continue
        d = day if day is not None else str(ts.date())
        key = (d, source, *parsed)
        rows[key] = max(rows.get(key, 0), int(qty))
    conn.executemany(
        "INSERT OR REPLACE INTO oi_daily (date, source, root, expiry, cp, strike, oi)"
        " VALUES (?,?,?,?,?,?,?)",
        [(*k, v) for k, v in rows.items()])
    conn.execute("INSERT OR REPLACE INTO done_files (path) VALUES (?)", (str(path),))
    conn.commit()
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--smoke", action="store_true", help="3 known files + samples, no full run")
    args = ap.parse_args()

    conn = open_db()
    done = {r[0] for r in conn.execute("SELECT path FROM done_files")}

    opra_files = sorted(f for d in OPRA_DIRS for f in d.glob("opra-pillar-*.statistics.dbn.zst"))
    if args.smoke:
        picks = [f for f in opra_files if f.name in
                 ("opra-pillar-20221003.statistics.dbn.zst",
                  "opra-pillar-20260701.statistics.dbn.zst")]
        for f in picks:
            n = condense_file(conn, f, "opra", parse_opra, day=f.name[12:16] + "-" + f.name[16:18] + "-" + f.name[18:20])
            print(f"{f.name}: {n} strikes")
        n = condense_file(conn, SME_FILE, "sme", parse_sme, day=None)
        print(f"SME: {n} rows")
        print("samples:")
        for r in conn.execute("SELECT * FROM oi_daily WHERE oi > 30000 ORDER BY oi DESC LIMIT 6"):
            print(" ", r)
        return

    total = 0
    for i, f in enumerate(opra_files):
        if str(f) in done:
            continue
        d = f.name[12:16] + "-" + f.name[16:18] + "-" + f.name[18:20]
        total += condense_file(conn, f, "opra", parse_opra, day=d)
        if (i + 1) % 100 == 0:
            print(f"{i + 1}/{len(opra_files)} files, {total} rows so far ({d})", flush=True)
    if str(SME_FILE) not in done:
        total += condense_file(conn, SME_FILE, "sme", parse_sme, day=None)
    n, lo, hi = conn.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM oi_daily").fetchone()
    print(f"done: table has {n} rows, {lo} -> {hi} (stamped {date.today()})")


if __name__ == "__main__":
    main()
