"""Alpha Vantage EARNINGS fetcher — cache historical EPS surprises per symbol.

One `function=EARNINGS` call returns a symbol's FULL quarterly history (back to
~1996) with reportedDate, reportedEPS, estimatedEPS, surprise, surprisePercentage.
We cache each symbol once into a gitignored DB so the pull is resumable across the
free tier's 25-calls/day limit (and the 1-request/second limit — we space calls).

Point-in-time: `reported_date` is the as-of gate (the surprise was unknown until
the company reported). Join setups on the PRIOR earnings' reported_date < entry.

Usage:
  python -m tools.av_earnings --from-earnings-db --limit 20   # top-N uncached symbols by setup count
  python -m tools.av_earnings --symbols AAPL,MSFT,JPM
  python -m tools.av_earnings --status                        # what's cached / remaining
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import time
from pathlib import Path

import httpx

from config.settings import alphavantage_api_key

ROOT = Path(__file__).resolve().parent.parent
AV_DB = ROOT / "data" / "research" / "av_earnings.db"
EARNINGS_DB = ROOT / "data" / "research" / "earnings_labeled.db"
BASE = "https://www.alphavantage.co/query"
SPACING_S = 1.6  # stay under the 1-request/second free limit


class Throttled(Exception):
    """AV returned a rate-limit notice instead of data."""

    def __init__(self, msg: str):
        super().__init__(msg)
        self.daily = "per day" in msg or "25 requests" in msg  # vs the 1/second limit


def _keys() -> list[str]:
    """All configured AV keys (ALPHAVANTAGE_API_KEYS csv, else the single key)."""
    raw = os.environ.get("ALPHAVANTAGE_API_KEYS", "").strip()
    if raw:
        return [k.strip() for k in raw.split(",") if k.strip()]
    k = alphavantage_api_key()
    return [k] if k else []


def _conn() -> sqlite3.Connection:
    AV_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(AV_DB)
    c.execute(
        """CREATE TABLE IF NOT EXISTS av_quarterly_surprises (
            symbol TEXT, fiscal_date_ending TEXT, reported_date TEXT,
            reported_eps REAL, estimated_eps REAL, surprise REAL, surprise_pct REAL,
            PRIMARY KEY (symbol, fiscal_date_ending))"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS av_fetch_log (
            symbol TEXT PRIMARY KEY, fetched_at TEXT, n_quarters INTEGER, status TEXT)"""
    )
    return c


def _f(v) -> float | None:
    if v is None or str(v) in ("None", "", "null", "-"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_one(sym: str, key: str, conn: sqlite3.Connection) -> int:
    """Fetch + cache one symbol's quarterly surprises. Returns # quarters stored."""
    r = httpx.get(BASE, params={"function": "EARNINGS", "symbol": sym, "apikey": key}, timeout=40).json()
    if "Note" in r or "Information" in r:
        raise Throttled(str(r.get("Note") or r.get("Information"))[:160])
    quarters = r.get("quarterlyEarnings", []) or []
    rows = [
        (sym, q.get("fiscalDateEnding"), q.get("reportedDate"),
         _f(q.get("reportedEPS")), _f(q.get("estimatedEPS")),
         _f(q.get("surprise")), _f(q.get("surprisePercentage")))
        for q in quarters if q.get("fiscalDateEnding")
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO av_quarterly_surprises
           (symbol, fiscal_date_ending, reported_date, reported_eps, estimated_eps, surprise, surprise_pct)
           VALUES (?,?,?,?,?,?,?)""", rows)
    conn.execute(
        "INSERT OR REPLACE INTO av_fetch_log (symbol, fetched_at, n_quarters, status) VALUES (?,?,?,?)",
        (sym, time.strftime("%Y-%m-%dT%H:%M:%S"), len(rows), "ok" if rows else "empty"))
    conn.commit()
    return len(rows)


def _setup_symbols_by_count() -> list[str]:
    c = sqlite3.connect(EARNINGS_DB)
    rows = c.execute(
        "SELECT symbol, COUNT(*) n FROM strategy_setups GROUP BY symbol ORDER BY n DESC").fetchall()
    c.close()
    return [s for s, _ in rows]


def _cached(conn: sqlite3.Connection) -> set[str]:
    return {s for (s,) in conn.execute("SELECT symbol FROM av_fetch_log")}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="comma-separated symbols")
    ap.add_argument("--from-earnings-db", action="store_true", help="pull uncached setup symbols, most-setups first")
    ap.add_argument("--limit", type=int, default=48, help="max symbols this run (free tier ~25/day per key; keys rotate)")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args(argv)

    conn = _conn()
    if args.status:
        cached = _cached(conn)
        total = len(_setup_symbols_by_count())
        nq = conn.execute("SELECT COUNT(*) FROM av_quarterly_surprises").fetchone()[0]
        print(f"cached symbols: {len(cached)}/{total} setup-symbols | {nq} quarterly rows | DB={AV_DB}")
        return

    keys = _keys()
    if not keys:
        raise SystemExit("No AV key (set ALPHAVANTAGE_API_KEY or ALPHAVANTAGE_API_KEYS)")

    if args.symbols:
        targets = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.from_earnings_db:
        cached = _cached(conn)
        targets = [s for s in _setup_symbols_by_count() if s not in cached]
    else:
        raise SystemExit("pass --symbols or --from-earnings-db")
    targets = targets[: args.limit]

    print(f"fetching up to {len(targets)} symbols with {len(keys)} key(s)...")
    ki = done = i = 0
    sec_retries = 0
    while i < len(targets):
        sym = targets[i]
        try:
            n = fetch_one(sym, keys[ki], conn)
            done += 1
            sec_retries = 0
            print(f"  [{done}] {sym}: {n} quarters")
            i += 1
            if i < len(targets):
                time.sleep(SPACING_S)
        except Throttled as t:
            if t.daily:
                ki += 1
                if ki >= len(keys):
                    print(f"  all {len(keys)} key(s) at daily limit -> stopping at {done} fetched")
                    break
                print(f"  key #{ki} at daily limit -> rotating to key #{ki + 1}")
            elif sec_retries < 3:
                sec_retries += 1
                time.sleep(2.5)  # 1-request/second limit: brief backoff, retry same symbol
            else:
                print(f"  {sym}: persistent 1/sec throttle -> skipping")
                sec_retries = 0
                i += 1
        except Exception as e:  # noqa: BLE001 - log and continue
            print(f"  {sym}: error {type(e).__name__}: {e}")
            i += 1
    print(f"done: {done} fetched this run. Run --status to see totals.")


if __name__ == "__main__":
    main()
