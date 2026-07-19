#!/usr/bin/env python3
"""ONE-SHOT gap bridge for the DoltHub `volatility_history` fast layer. No cron, no standing role.

History (2026-07-19): the corpus was captured via shallow `dolt clone` into
data/dolthub_options/ (3.4GB, both tables; volatility_history extracted to SQLite by
tools/dolthub_iv_extract.py — count-verified 1,870,937). Founder call: NO standing DoltHub
maintenance arm — forward data comes from our own Alpaca chain sampler (volume/OI included,
our-model IV). This script's only remaining job: run ONCE when the sampler goes live to
close the snapshot→sampler date gap in the SQLite fast layer, then retire.

Usage:
    uv run python tools/dolthub_iv_import.py              # resume from local max (the gap fill)
    uv run python tools/dolthub_iv_import.py --max-pages 3  # smoke test

Target: data/options_iv_history.db (gitignored — external licensed data, research use only;
multi-user serving may only expose DERIVED values per the shared-plane rule).

API notes: branch is `master` (not main); 1,000-row cap per query (status "RowLimit" marks
truncation); the API soft-throttles at bulk scale (~80 rapid pages stretched every query to
its ~50s deadline — why the clone became the backfill path), but a few-day gap fill is a
handful of queries, safely under it. option_chain is NOT reachable via the API at all.
"""

import argparse
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

API = "https://www.dolthub.com/api/v1alpha1/post-no-preference/options/master"
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "options_iv_history.db"
PAGE = 1000  # DoltHub API hard cap per query
SLEEP_BETWEEN = 0.35  # stay polite on their free API

COLS = [
    "date", "act_symbol",
    "hv_current", "hv_week_ago", "hv_month_ago",
    "hv_year_high", "hv_year_high_date", "hv_year_low", "hv_year_low_date",
    "iv_current", "iv_week_ago", "iv_month_ago",
    "iv_year_high", "iv_year_high_date", "iv_year_low", "iv_year_low_date",
]

DDL = f"""
CREATE TABLE IF NOT EXISTS volatility_history (
    {", ".join(f"{c} TEXT" for c in COLS)},
    PRIMARY KEY (date, act_symbol)
);
"""


def api_query(sql: str, retries: int = 5) -> dict:
    url = API + "?q=" + urllib.parse.quote(sql)
    delay = 2.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=90) as resp:
                data = json.load(resp)
            status = data.get("query_execution_status")
            if status in ("Success", "RowLimit"):
                return data
            raise RuntimeError(data.get("query_execution_message") or f"status={status}")
        except Exception as exc:  # noqa: BLE001 — network layer, retry everything
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt + 1}/{retries - 1} after: {exc}", flush=True)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(DDL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_volhist_symbol_date"
        " ON volatility_history (act_symbol, date)"
    )
    return conn


def local_cursor(conn: sqlite3.Connection, replay_days: int) -> tuple[str, str] | None:
    """Resume point: the max local PK, optionally floored back to catch upstream edits."""
    row = conn.execute(
        "SELECT date, act_symbol FROM volatility_history"
        " ORDER BY date DESC, act_symbol DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    if replay_days:
        floor = date.fromisoformat(row[0]) - timedelta(days=replay_days)
        return (floor.isoformat(), "")
    return (row[0], row[1])


def run(replay_days: int, max_pages: int | None) -> None:
    conn = open_db()
    cursor = local_cursor(conn, replay_days)
    col_list = ", ".join(COLS)
    insert_sql = (
        f"INSERT OR REPLACE INTO volatility_history ({col_list})"
        f" VALUES ({', '.join('?' for _ in COLS)})"
    )
    total, pages = 0, 0
    while True:
        if cursor is None:
            where = "1=1"
        else:
            d, s = cursor[0], cursor[1].replace("'", "''")
            where = f"(date > '{d}') OR (date = '{d}' AND act_symbol > '{s}')"
        resp = api_query(
            f"SELECT {col_list} FROM volatility_history WHERE {where}"
            f" ORDER BY date, act_symbol LIMIT {PAGE}"
        )
        rows = resp["rows"]
        if not rows:
            break
        conn.executemany(insert_sql, [tuple(r.get(c) for c in COLS) for r in rows])
        conn.commit()
        total += len(rows)
        pages += 1
        cursor = (rows[-1]["date"], rows[-1]["act_symbol"])
        if pages % 25 == 0:
            print(f"  page {pages}: {total} rows, cursor at {cursor[0]}", flush=True)
        if len(rows) < PAGE and resp["query_execution_status"] == "Success":
            break
        if max_pages is not None and pages >= max_pages:
            print(f"  stopping at --max-pages {max_pages}", flush=True)
            break
        time.sleep(SLEEP_BETWEEN)

    local_n = conn.execute("SELECT COUNT(*) FROM volatility_history").fetchone()[0]
    local_hi = conn.execute("SELECT MAX(date) FROM volatility_history").fetchone()[0]
    print(f"done: +{total} rows this run; local total {local_n}, max date {local_hi}")
    if max_pages is None:
        remote = api_query("SELECT COUNT(*) AS n FROM volatility_history")["rows"][0]["n"]
        print(f"remote total {remote} — {'MATCH' if int(remote) == local_n else 'MISMATCH'}")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sync", action="store_true",
        help="incremental daily pull; replays --replay-days back for upstream corrections",
    )
    parser.add_argument("--replay-days", type=int, default=None)
    parser.add_argument("--max-pages", type=int, default=None, help="smoke-test page cap")
    args = parser.parse_args()
    replay = args.replay_days if args.replay_days is not None else (5 if args.sync else 0)
    run(replay_days=replay, max_pages=args.max_pages)


if __name__ == "__main__":
    main()
