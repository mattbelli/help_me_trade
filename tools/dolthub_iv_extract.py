"""Rebuild data/options_iv_history.db from the local dolt clone (data/dolthub_options/).

Usage (from repo root; full rebuild takes ~20s):
    ~/.local/bin/dolt --data-dir data/dolthub_options sql -r csv \
        -q "SELECT * FROM volatility_history ORDER BY date, act_symbol" \
        | uv run python tools/dolthub_iv_extract.py

Run only after a fresh re-clone of the upstream snapshot (the clone is a frozen historical
capture — no standing sync; forward data comes from our own Alpaca sampler). Drops and
recreates the table, so the pipe must deliver the complete corpus.
"""

import csv
import sqlite3
import sys

COLS = ["date","act_symbol","hv_current","hv_week_ago","hv_month_ago","hv_year_high","hv_year_high_date","hv_year_low","hv_year_low_date","iv_current","iv_week_ago","iv_month_ago","iv_year_high","iv_year_high_date","iv_year_low","iv_year_low_date"]
conn = sqlite3.connect("/home/mbelli/dev/help_me_trade/data/options_iv_history.db")
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("DROP TABLE IF EXISTS volatility_history")
conn.execute("CREATE TABLE volatility_history (" + ", ".join(f"{c} TEXT" for c in COLS) + ", PRIMARY KEY (date, act_symbol))")

r = csv.DictReader(sys.stdin)
assert r.fieldnames == COLS, f"column mismatch: {r.fieldnames}"
batch, n = [], 0
ins = f"INSERT INTO volatility_history ({','.join(COLS)}) VALUES ({','.join('?'*len(COLS))})"
for row in r:
    batch.append(tuple(row[c] if row[c] != "" else None for c in COLS))
    if len(batch) >= 50000:
        conn.executemany(ins, batch)
        n += len(batch)
        batch = []
conn.executemany(ins, batch)
n += len(batch)
conn.execute("CREATE INDEX IF NOT EXISTS idx_volhist_symbol_date ON volatility_history (act_symbol, date)")
conn.commit()
print("inserted:", n)
print("count:", conn.execute("SELECT COUNT(*) FROM volatility_history").fetchone()[0])
for sym, d in [("AAPL","2019-02-09"), ("SPY","2026-07-17"), ("NVDA","2026-07-17"), ("TSLA","2026-07-17")]:
    print(sym, d, conn.execute("SELECT iv_current FROM volatility_history WHERE act_symbol=? AND date=?", (sym, d)).fetchone())
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.close()
