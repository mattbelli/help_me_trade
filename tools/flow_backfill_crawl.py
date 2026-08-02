#!/usr/bin/env python3
"""Flow 3.3 backfill discovery crawl — find every big single-print candidate in
the pre-corpus window (2024-02-15 .. 2025-07-25) using only free data.

Registered context: `References/FLOW_ROADMAP.md` → "Backfill funnel validation"
+ "Discovery, round 2" logged looks. This is DISCOVERY ONLY — no selection, no
outcomes, no forward returns; the sealed split stays sealed. The candidate
prints found here later feed the registered 3.2 stage-1 rules (premium >= $25k,
DTE >= 1, price/ADV floors) and the tcbbo drill-down windows (founder-authorized
purchase, exact quote first).

Funnel per session (measured recall 77/77 truth prints on the corpus sample):
  A. Databento OPRA `definition` ALL_SYMBOLS (free, cost-gated) -> every listed
     OSI symbol that day. Daily, never weekly: strikes listed intra-week near
     expiry are exactly the event-adjacent contracts we must not miss.
  B. Alpaca 1Day option bars (blocks INCLUDED — measured, unlike Databento
     ohlcv which excludes them) in 1,000-symbol batches. Net 1: a bar exists
     and (v - n + 1) * high * 100 >= $25,000. (v-n+1 lower-bounds the largest
     single trade: every other trade adds >= 1 to v for each 1 it adds to n.)
  C. Alpaca trades, FULL DAY, for every net-1 survivor. No minute-bar tier:
     measured 2026-08-02, Alpaca 1Min bars EXCLUDE cond-'B' prints that the
     1Day bars include (VRNS 4,180-lot had no minute bar at all) — an
     aggregate between us and the tape is a correlated blindness. Components
     are summed per 1ms bucket (Alpaca reports per-fill components; the
     corpus groups same-ns prints — GNW 305 = 45+259+1). Keep groups with
     premium >= $25,000.

Every Databento call is cost-quoted first and aborts if > $0.01. Alpaca pacing
stays well under the measured 10,000 req/min limit.

Usage (dev tree):
  .venv/bin/python tools/flow_backfill_crawl.py --pilot 2025-07-28 --validate
  .venv/bin/python tools/flow_backfill_crawl.py --run 2024-02-15 2025-07-25
  .venv/bin/python tools/flow_backfill_crawl.py --status
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "research" / "flow_backfill"
DB_PATH = DATA_DIR / "backfill.db"
DEFS_DIR = DATA_DIR / "defs"

PREMIUM_NET = 25_000.0          # registered stage-1 PREMIUM_MIN — strict superset
BUCKET_NS = 1_000_000           # 1ms bucket for summing per-fill components
BARS_BATCH = 1_000
TRADES_BATCH = 50
MAX_COST = 0.01                 # Databento free gate — abort any charged pull
REQ_PER_MIN = 4_000             # 40% of the measured 10k/min Alpaca limit
WORKERS = 16
PREFETCH_STREAMS = 3            # concurrent Databento definition streams

ALPACA_DATA = "https://data.alpaca.markets"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


class Pacer:
    """Global token-bucket keeping the whole process under REQ_PER_MIN."""

    def __init__(self, per_min: int):
        self.interval = 60.0 / per_min
        self.lock = threading.Lock()
        self.next_t = time.monotonic()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            self.next_t = max(self.next_t + self.interval, now)
            delay = self.next_t - now
        if delay > 0:
            time.sleep(delay)


class Alpaca:
    def __init__(self, env: dict[str, str], pacer: Pacer):
        self.headers = {
            "APCA-API-KEY-ID": env["APCA_API_KEY_ID"],
            "APCA-API-SECRET-KEY": env["APCA_API_SECRET_KEY"],
        }
        self.pacer = pacer
        self.local = threading.local()

    def _client(self) -> httpx.Client:
        if not hasattr(self.local, "client"):
            self.local.client = httpx.Client(headers=self.headers, timeout=60)
        return self.local.client

    def get(self, path: str, params: dict) -> dict:
        for attempt in range(6):
            self.pacer.wait()
            try:
                r = self._client().get(ALPACA_DATA + path, params=params)
            except httpx.HTTPError:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"alpaca {path} -> {r.status_code}: {r.text[:200]}")
        raise RuntimeError(f"alpaca {path}: retries exhausted")

    def paged(self, path: str, params: dict, key: str) -> dict[str, list]:
        out: dict[str, list] = defaultdict(list)
        params = dict(params)
        while True:
            js = self.get(path, params)
            for sym, rows in (js.get(key) or {}).items():
                out[sym].extend(rows)
            tok = js.get("next_page_token")
            if not tok:
                return out
            params["page_token"] = tok


def db_connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """CREATE TABLE IF NOT EXISTS sessions(
            session TEXT PRIMARY KEY, n_listed INT, n_net1 INT, n_flagged INT,
            n_prints INT, elapsed_s REAL, status TEXT, updated_at TEXT)"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS prints(
            session TEXT, symbol TEXT, root TEXT, exp TEXT, cp TEXT,
            strike REAL, ts_ns INT, size INT, n_components INT,
            price REAL, premium REAL, conds TEXT, exchs TEXT,
            bar_v INT, bar_n INT, bar_h REAL,
            PRIMARY KEY(session, symbol, ts_ns))"""
    )
    return con


# ---- stage A: enumerate listed contracts from free daily definitions --------

OSI_RE = re.compile(r"[A-Z]{1,5}\d{6,7}[CP]\d{8}")   # Alpaca's accepted grammar
# Non-matching roots are OPRA adjusted/test series (leading-digit corporate-action
# roots like 2CLSK) — the frozen root-classification fence already excludes them
# from the corpus equity stratum, so dropping them here is population-faithful.


def listed_symbols(session: str) -> list[str]:
    cache = DEFS_DIR / f"{session}.txt.gz"
    if cache.exists():
        raw = gzip.decompress(cache.read_bytes()).decode().split()
        return [s for s in raw if OSI_RE.fullmatch(s)]
    import databento as dbn

    client = dbn.Historical(os.environ["DATABENTO_API_KEY"])
    nxt = (date.fromisoformat(session) + timedelta(days=1)).isoformat()
    kw = dict(dataset="OPRA.PILLAR", schema="definition", symbols="ALL_SYMBOLS",
              start=session, end=nxt)
    cost = client.metadata.get_cost(**kw)
    if cost > MAX_COST:
        raise RuntimeError(f"ABORT: definition {session} would cost ${cost:.2f}")
    store = client.timeseries.get_range(**kw)
    syms: set[str] = set()
    for rec in store:
        raw = rec.raw_symbol
        if raw and len(raw) == 21:      # OSI: 6 root + 6 date + C/P + 8 strike
            syms.add(raw.replace(" ", ""))
    out = sorted(syms)
    DEFS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".gz.tmp")
    tmp.write_bytes(gzip.compress(" ".join(out).encode()))
    tmp.rename(cache)               # atomic vs a concurrent prefetcher
    return [s for s in out if OSI_RE.fullmatch(s)]


# ---- stages B/C/D: Alpaca bars nets + trades confirm ------------------------

def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def net1_daily(ap: Alpaca, session: str, symbols: list[str]) -> dict[str, tuple]:
    keep: dict[str, tuple] = {}
    lock = threading.Lock()

    def one(batch: list[str]) -> None:
        bars = ap.paged("/v1beta1/options/bars",
                        {"symbols": ",".join(batch), "timeframe": "1Day",
                         "start": session, "end": f"{session}T23:59:59Z",
                         "limit": 10_000}, "bars")
        local = {}
        for sym, rows in bars.items():
            for b in rows:
                if not b["t"].startswith(session):   # never the next session
                    continue
                if (b["v"] - b["n"] + 1) * b["h"] * 100.0 >= PREMIUM_NET:
                    local[sym] = (b["v"], b["n"], b["h"])
        with lock:
            keep.update(local)

    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(one, _chunks(symbols, BARS_BATCH)))
    return keep


def confirm_trades(ap: Alpaca, session: str, cands: list[str]) -> list[dict]:
    """Full-day trades for every net-1 survivor; no aggregate in between."""
    prints: list[dict] = []
    lock = threading.Lock()

    def one(batch: list[str]) -> None:
        trades = ap.paged("/v1beta1/options/trades",
                          {"symbols": ",".join(batch),
                           "start": f"{session}T13:00:00Z",
                           "end": f"{session}T22:15:00Z",
                           "limit": 10_000}, "trades")
        local: list[dict] = []
        for sym, rows in trades.items():
            local.extend(group_prints(sym, rows))
        with lock:
            prints.extend(local)

    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(one, _chunks(sorted(cands), TRADES_BATCH)))
    return prints


def parse_ns(iso: str) -> int:
    """Exact ns from an Alpaca RFC3339 timestamp — float would mangle sub-us.

    2024-era rows can carry no fractional part at all ('…T15:45:47Z')."""
    head, _, tail = iso.partition(".")
    head = head.rstrip("Z")
    frac = tail.rstrip("Z")
    base = int(datetime.fromisoformat(head + "+00:00").timestamp())
    return base * 1_000_000_000 + (int(frac.ljust(9, "0")) if frac else 0)


def group_prints(sym: str, rows: list[dict]) -> list[dict]:
    """Sum per-fill components per 1ms bucket; keep premium >= net.

    Buckets, not gap-chains: chaining welds a busy tape into one giant
    pseudo-print (measured: 183,646 'prints' on the 2025-07-28 pilot)."""
    buckets: dict[int, list] = defaultdict(list)
    for t in rows:
        ns = parse_ns(t["t"])
        cond = t.get("c", "")
        cond = cond if isinstance(cond, str) else "".join(cond)
        buckets[ns // BUCKET_NS].append((ns, int(t["s"]), float(t["p"]),
                                         cond, t.get("x", "")))
    out = []
    for _, grp in sorted(buckets.items()):
        size = sum(r[1] for r in grp)
        notional = sum(r[1] * r[2] * 100.0 for r in grp)
        if notional >= PREMIUM_NET:
            grp.sort()
            out.append({
                "symbol": sym, "ts_ns": grp[0][0], "size": size,
                "n_components": len(grp),
                "price": notional / (size * 100.0) if size else 0.0,
                "premium": notional,
                "conds": "".join(sorted({r[3] for r in grp if r[3]})),
                "exchs": "".join(sorted({r[4] for r in grp if r[4]})),
            })
    return out


def parse_osi(sym: str) -> tuple[str, str, str, float]:
    root = sym[:-15]
    exp = "20" + sym[-15:-9]
    cp = sym[-9]
    strike = int(sym[-8:]) / 1000.0
    return root, f"{exp[:4]}-{exp[4:6]}-{exp[6:]}", cp, strike


# ---- per-session driver ------------------------------------------------------

def run_session(con: sqlite3.Connection, ap: Alpaca, session: str,
                verbose: bool = False) -> dict:
    t_start = time.monotonic()
    syms = listed_symbols(session)
    if verbose:
        print(f"  A listed: {len(syms):,}", flush=True)
    net1 = net1_daily(ap, session, syms)
    if verbose:
        print(f"  B net1 candidates: {len(net1):,}", flush=True)
    prints = confirm_trades(ap, session, list(net1))
    if verbose:
        print(f"  C prints >= ${PREMIUM_NET:,.0f}: {len(prints):,}", flush=True)

    with con:
        con.execute("DELETE FROM prints WHERE session=?", (session,))
        for p in prints:
            root, exp, cp, strike = parse_osi(p["symbol"])
            bv, bn, bh = net1.get(p["symbol"], (None, None, None))
            con.execute(
                "INSERT OR REPLACE INTO prints VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (session, p["symbol"], root, exp, cp, strike, p["ts_ns"],
                 p["size"], p["n_components"], p["price"], p["premium"],
                 p["conds"], p["exchs"], bv, bn, bh))
        con.execute(
            "INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?)",
            (session, len(syms), len(net1), None, len(prints),
             round(time.monotonic() - t_start, 1), "done",
             datetime.now(UTC).isoformat(timespec="seconds")))
    return {"listed": len(syms), "net1": len(net1),
            "prints": len(prints),
            "elapsed_s": round(time.monotonic() - t_start, 1)}


def validate_against_truth(con: sqlite3.Connection, session: str) -> None:
    ledger = json.loads((ROOT / "data/research/hedge_footprint_wide/equity/"
                         "fetch_ledger_equity.json").read_text())
    truth = [e for e in ledger["events"] if e["session"] == session]
    rows = con.execute(
        "SELECT symbol, ts_ns, size FROM prints WHERE session=?",
        (session,)).fetchall()
    by_sym = defaultdict(list)
    for sym, ts, size in rows:
        by_sym[sym].append((ts, size))
    hit = miss = 0
    misses = []
    for e in truth:
        exp = date(1970, 1, 1) + timedelta(days=int(e["exp_days"]))
        sym = (f"{e['root']}{exp:%y%m%d}{'P' if e['is_put'] else 'C'}"
               f"{int(round(e['strike'] * 1000)):08d}")
        found = any(abs(ts - e["ts_ns"]) <= 2 * BUCKET_NS
                    and size >= e["contracts"]
                    for ts, size in by_sym.get(sym, []))
        if found:
            hit += 1
        else:
            miss += 1
            misses.append((sym, e["contracts"], e["premium"]))
    print(f"VALIDATE {session}: truth recall {hit}/{hit + miss}")
    for m in misses:
        print("  MISS:", m)


def trading_sessions(ap: Alpaca, env: dict, start: str, end: str) -> list[str]:
    with httpx.Client(headers={
        "APCA-API-KEY-ID": env["APCA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": env["APCA_API_SECRET_KEY"],
    }, timeout=30) as c:
        r = c.get("https://paper-api.alpaca.markets/v2/calendar",
                  params={"start": start, "end": end})
        r.raise_for_status()
        return [d["date"] for d in r.json()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", metavar="SESSION")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--run", nargs=2, metavar=("START", "END"))
    parser.add_argument("--prefetch", nargs=2, metavar=("START", "END"))
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    env = load_env()
    os.environ.setdefault("DATABENTO_API_KEY", env.get("DATABENTO_API_KEY", ""))
    con = db_connect()

    if args.status:
        done = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(n_prints),0) FROM sessions "
            "WHERE status='done'").fetchone()
        last = con.execute(
            "SELECT session, n_listed, n_net1, n_flagged, n_prints, elapsed_s "
            "FROM sessions ORDER BY session DESC LIMIT 5").fetchall()
        print(f"sessions done: {done[0]}, total prints: {done[1]:,}")
        for row in last:
            print("  ", row)
        return

    ap = Alpaca(env, Pacer(REQ_PER_MIN))

    if args.pilot:
        print(f"PILOT {args.pilot}", flush=True)
        stats = run_session(con, ap, args.pilot, verbose=True)
        print("stats:", stats)
        if args.validate:
            validate_against_truth(con, args.pilot)
        return

    if args.prefetch:
        sessions = trading_sessions(ap, env, args.prefetch[0], args.prefetch[1])
        todo = [s for s in sessions
                if not (DEFS_DIR / f"{s}.txt.gz").exists()]
        print(f"prefetch: {len(todo)}/{len(sessions)} sessions need defs",
              flush=True)

        def fetch(s: str) -> None:
            if (DEFS_DIR / f"{s}.txt.gz").exists():
                return
            n = len(listed_symbols(s))
            print(f"defs {s}: {n:,} symbols", flush=True)

        with ThreadPoolExecutor(PREFETCH_STREAMS) as ex:
            list(ex.map(fetch, todo))
        print("PREFETCH COMPLETE", flush=True)
        return

    if args.run:
        sessions = trading_sessions(ap, env, args.run[0], args.run[1])
        done = {r[0] for r in con.execute(
            "SELECT session FROM sessions WHERE status='done'")}
        todo = [s for s in sessions if s not in done]
        print(f"run: {len(sessions)} sessions, {len(done)} done, "
              f"{len(todo)} to go", flush=True)
        for s in todo:
            stats = run_session(con, ap, s)
            print(f"{s}: {stats}", flush=True)
        print("CRAWL COMPLETE", flush=True)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
