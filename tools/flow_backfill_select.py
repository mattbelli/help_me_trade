#!/usr/bin/env python3
"""Flow 3.3 backfill phase-A selection — pick which tcbbo windows to buy.

Registered context: `References/FLOW_ROADMAP.md` → backfill discovery round 3.
Input = the discovery crawl's 36.5M >= $25k print groups (backfill.db). Output =
a proxy-ranked shortlist and the merged ±330s parent-scoped tcbbo windows, with
an EXACT Databento get_cost quote. THIS SCRIPT NEVER BUYS DATA — the purchase is
a separate founder-authorized step gated on the quote (~$10-30 communicated).

Phase A applies every registered stage-1 filter computable WITHOUT the tcbbo
tape, mirroring tools/hedge_footprint.py semantics exactly:
  - equity stratum via the frozen root-classification fence, date-scoped
    (unclassified = roots dead before the corpus era: measured 0.20% of groups /
    0.13% of premium over the backfill window — disclosed drop, fence NOT
    regenerated);
  - premium >= $25k (applied by the crawl), DTE >= 1;
  - T in [09:35, 15:55] ET, outside the [09:55,10:05] & [13:55,14:05] release
    fences — RELAXED by ±2s at every boundary because 2024 Alpaca timestamps are
    second-grain: phase A may only drop what the registered pipeline would
    certainly drop, so boundary events stay in;
  - half-day sessions excluded (the corpus pool's own rule);
  - prior_close >= $5 and ADV20 >= 200k shares from RAW Alpaca daily bars on
    the AS-OF-SESSION split basis (raw x date-scoped split factors — the basis
    contemporaneous OSI strikes are on; `adjustment=split` today-basis was
    review blocker 2), same strictly-before window shape as db/flow_corpus.py.

What phase A CANNOT apply (needs the purchased tape, phase B): side unanimity,
option quote mid, delta band, single-leg/package codes, isolation, |h| ranking,
detectability d. The budget therefore forces a DISCLOSED proxy pre-rank:
contracts / ADV20 — monotone in |h|/expected-volume up to the unobserved |Δ|
and the time-of-day volume profile, built only from fully-observed tape/bars
quantities (nothing label-blind gates anything). Per-(name, session) cap 10
(generous vs the corpus's 10-per-name-total) guards budget diversity. Rank
slippage vs the true |h|/d ordering is measurable post-purchase at the
purchase boundary; tranche 2 covers the tail if the true ranking saturates it.

Windows: [ts-330s, ts+330s] per candidate (330 = the registered 300s snapshot
+ 30s pad covering timestamp-grain slop), union-merged per (root, session),
parent symbology (`ROOT.OPT`).

Usage (dev tree):
  .venv/bin/python tools/flow_backfill_select.py --top-k 3500        # quote only
  .venv/bin/python tools/flow_backfill_select.py --fetch-bars        # stage the bars
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.flow_backfill_crawl import (  # noqa: E402
    DATA_DIR,
    Alpaca,
    Pacer,
    db_connect,
    load_env,
)
from tools.root_classification_build import load as load_classification  # noqa: E402

# registered constants mirrored from tools/hedge_footprint.py
DTE_MIN = 1
PRICE_MIN = 5.0
ADV_MIN = 200_000.0
ADV_LOOKBACK = 20
T_WINDOW_ET = (9 * 3600 + 35 * 60, 15 * 3600 + 55 * 60)
RELEASE_FENCES_ET = ((9 * 3600 + 55 * 60, 10 * 3600 + 5 * 60),
                     (13 * 3600 + 55 * 60, 14 * 3600 + 5 * 60))
TS_SLOP_S = 2                    # 2024 Alpaca ts are second-grain; never drop boundary events
WINDOW_PAD_S = 330               # 300s registered snapshot + 30s grain slop
PER_NAME_SESSION_CAP = 10
BARS_BATCH = 500
START, END = "2024-02-15", "2025-07-25"
FENCE_PATH = ROOT / "signals" / "options_flow" / "root_classification.json"
_ET = ZoneInfo("America/New_York")


def half_days(ap: Alpaca, env: dict) -> set[str]:
    base = env.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    base = base.rstrip("/").removesuffix("/v2")
    import httpx
    r = httpx.get(f"{base}/v2/calendar", params={"start": START, "end": END},
                  headers=ap.headers, timeout=60)
    r.raise_for_status()
    return {row["date"] for row in r.json() if row["close"] != "16:00"}


def fetch_daily_bars(ap: Alpaca, symbols: list[str], con: sqlite3.Connection) -> None:
    """RAW (unadjusted) daily bars for the backfill window + ADV lookback runway,
    into backfill.db equity_bars_raw. Raw + the splits table -> as-of-session
    basis in prior_close_adv; `adjustment=split` was the review blocker: it
    returns TODAY-basis prices while OSI strikes are contemporaneous."""
    con.execute("""CREATE TABLE IF NOT EXISTS equity_bars_raw(
        symbol TEXT, date TEXT, c REAL, v REAL, PRIMARY KEY(symbol, date))""")
    have = {r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM equity_bars_raw").fetchall()}
    todo = [s for s in symbols if s not in have]
    print(f"bars: {len(have)} cached, fetching {len(todo)}")
    start = (date.fromisoformat(START) - timedelta(days=45)).isoformat()
    for i in range(0, len(todo), BARS_BATCH):
        chunk = todo[i:i + BARS_BATCH]
        bars = ap.paged("/v2/stocks/bars", {
            "symbols": ",".join(chunk), "timeframe": "1Day",
            "start": f"{start}T00:00:00Z", "end": f"{END}T23:59:59Z",
            "adjustment": "raw", "feed": "sip", "limit": 10_000,
        }, key="bars")
        rows = [(sym, b["t"][:10], float(b["c"]), float(b["v"]))
                for sym, bs in bars.items() for b in bs]
        con.executemany(
            "INSERT OR REPLACE INTO equity_bars_raw VALUES (?,?,?,?)", rows)
        con.commit()
        print(f"  bars {i + len(chunk)}/{len(todo)} (+{len(rows)} rows)")


def fetch_splits(env: dict, rc, con: sqlite3.Connection) -> None:
    """Every forward/reverse split over the bars span, keyed by the TERMINAL
    ident (the symbol Alpaca's stitched bar history lives under), so the
    as-of-session factors join equity_bars_raw directly. The feed's symbol is
    contemporaneous at ex-date; rc.resolve maps it forward through renames."""
    import httpx
    con.execute("""CREATE TABLE IF NOT EXISTS splits(
        ident TEXT, ex_date TEXT, old_rate REAL, new_rate REAL, feed_symbol TEXT,
        kind TEXT, PRIMARY KEY(ident, ex_date, feed_symbol))""")
    headers = {"APCA-API-KEY-ID": env["APCA_API_KEY_ID"],
               "APCA-API-SECRET-KEY": env["APCA_API_SECRET_KEY"]}
    start = (date.fromisoformat(START) - timedelta(days=45)).isoformat()
    out: list[dict] = []
    token: str | None = None
    seen: set[str] = set()
    with httpx.Client(timeout=90.0) as client:
        for _page in range(200):
            params: dict[str, object] = {"types": "forward_split,reverse_split",
                                         "start": start, "end": END, "limit": 1000}
            if token:
                params["page_token"] = token
            r = client.get("https://data.alpaca.markets/v1/corporate-actions",
                           headers=headers, params=params)
            r.raise_for_status()
            payload = r.json()
            ca = payload.get("corporate_actions") or {}
            for kind in ("forward_splits", "reverse_splits"):
                for rec in ca.get(kind) or []:
                    out.append({**rec, "kind": kind})
            token = payload.get("next_page_token")
            if not token:
                break
            if token in seen:
                raise ValueError("corporate-actions pager repeated its token")
            seen.add(token)
        else:
            raise ValueError("corporate-actions pager exceeded the page cap")
    rows = [(rc.resolve(rec["symbol"], rec["ex_date"]), rec["ex_date"],
             float(rec["old_rate"]), float(rec["new_rate"]), rec["symbol"],
             rec["kind"]) for rec in out]
    con.execute("DELETE FROM splits")
    con.executemany("INSERT OR REPLACE INTO splits VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    print(f"splits: {len(rows)} records staged "
          f"({sum(1 for r in rows if r[0] != r[4])} resolved through a rename)")


_SPLITS: dict[str, list[tuple[str, float, float]]] | None = None


def _splits_for(con: sqlite3.Connection) -> dict[str, list[tuple[str, float, float]]]:
    global _SPLITS
    if _SPLITS is None:
        _SPLITS = {}
        for ident, ex, old_r, new_r in con.execute(
                "SELECT ident, ex_date, old_rate, new_rate FROM splits"):
            _SPLITS.setdefault(ident, []).append((ex, float(old_r), float(new_r)))
    return _SPLITS


def prior_close_adv(con: sqlite3.Connection, session: str) -> dict[str, tuple[float, float]]:
    """symbol -> (prior close strictly before, ADV20 strictly before) on the
    AS-OF-SESSION split basis: raw bars, each scaled by the splits with
    bar_date < ex_date <= session — the basis the session's OSI strikes and
    the corpus's contemporaneous fetch were on. Same strictly-before window
    shape as db/flow_corpus.py."""
    splits = _splits_for(con)
    out: dict[str, tuple[float, float]] = {}
    cur: str | None = None
    closes: list[float] = []
    vols: list[float] = []

    def _flush() -> None:
        if cur is not None and closes and vols:
            out[cur] = (closes[0], sum(vols) / len(vols))

    for sym, bar_date, c, v in con.execute(
        "SELECT symbol, date, c, v FROM ("
        "  SELECT symbol, date, c, v, ROW_NUMBER() OVER ("
        "    PARTITION BY symbol ORDER BY date DESC) rn"
        "  FROM equity_bars_raw WHERE date < ?"
        ") WHERE rn <= ? ORDER BY symbol, date DESC",
            (session, ADV_LOOKBACK)):
        if sym != cur:
            _flush()
            cur, closes, vols = sym, [], []
        pf = vf = 1.0
        for ex, old_r, new_r in splits.get(sym, ()):
            if bar_date < ex <= session:
                pf *= old_r / new_r
                vf *= new_r / old_r
        closes.append(float(c) * pf)
        vols.append(float(v) * vf)
    _flush()
    return out


def phase_a(con: sqlite3.Connection, skip_sessions: set[str]) -> list[dict]:
    rc = load_classification(FENCE_PATH)
    sessions = [s for (s,) in con.execute(
        "SELECT session FROM sessions WHERE session >= ? AND session <= ? "
        "AND status='done' ORDER BY session", (START, END)) if s not in skip_sessions]
    print(f"phase A over {len(sessions)} sessions ({len(skip_sessions)} half-days excluded)")
    led = {"groups": 0, "drop_universe": 0, "drop_dte": 0, "drop_time": 0,
           "drop_fence": 0, "drop_no_bars": 0, "drop_price_min": 0, "drop_adv": 0,
           "kept": 0}
    kept: list[dict] = []
    for ses in sessions:
        midnight = int(datetime.combine(
            date.fromisoformat(ses), dtime(0, 0), tzinfo=_ET).timestamp())
        ses_days = (date.fromisoformat(ses) - date(1970, 1, 1)).days
        pc_adv = prior_close_adv(con, ses)
        cls_cache: dict[str, tuple[str, str]] = {}
        for root, exp, ts_ns, size, premium, cp, strike, symbol in con.execute(
                "SELECT root, exp, ts_ns, size, premium, cp, strike, symbol "
                "FROM prints WHERE session = ?", (ses,)):
            led["groups"] += 1
            if root not in cls_cache:
                cls_cache[root] = (rc.classify(root, ses), rc.resolve(root, ses))
            cls, ident = cls_cache[root]
            if cls != "equity":
                led["drop_universe"] += 1
                continue
            dte = (date.fromisoformat(exp) - date(1970, 1, 1)).days - ses_days
            if dte < DTE_MIN:
                led["drop_dte"] += 1
                continue
            sec = ts_ns // 1_000_000_000 - midnight
            if not (T_WINDOW_ET[0] - TS_SLOP_S <= sec <= T_WINDOW_ET[1] + TS_SLOP_S):
                led["drop_time"] += 1
                continue
            if any(lo + TS_SLOP_S <= sec <= hi - TS_SLOP_S
                   for lo, hi in RELEASE_FENCES_ET):
                led["drop_fence"] += 1
                continue
            pc = pc_adv.get(ident)
            if pc is None:
                led["drop_no_bars"] += 1
                continue
            prior_close, adv = pc
            if prior_close < PRICE_MIN:
                led["drop_price_min"] += 1
                continue
            if adv < ADV_MIN:
                led["drop_adv"] += 1
                continue
            led["kept"] += 1
            kept.append({"session": ses, "root": root, "symbol": ident,
                         "osi": symbol, "exp": exp, "cp": cp, "strike": strike,
                         "ts_ns": ts_ns, "contracts": size, "premium": premium,
                         "dte": dte, "prior_close": prior_close, "adv": adv,
                         "proxy": size / adv})
    print("ledger:", json.dumps(led))
    return kept


def shortlist(kept: list[dict], top_k: int) -> list[dict]:
    kept.sort(key=lambda r: -r["proxy"])
    per: dict[tuple[str, str], int] = {}
    out: list[dict] = []
    for r in kept:
        key = (r["symbol"], r["session"])
        if per.get(key, 0) >= PER_NAME_SESSION_CAP:
            continue
        per[key] = per.get(key, 0) + 1
        out.append(r)
        if len(out) >= top_k:
            break
    return out


def merge_windows(cands: list[dict]) -> list[dict]:
    """±WINDOW_PAD_S per candidate, union-merged per (root, session)."""
    by: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for r in cands:
        lo = r["ts_ns"] - WINDOW_PAD_S * 1_000_000_000
        hi = r["ts_ns"] + WINDOW_PAD_S * 1_000_000_000
        by.setdefault((r["root"], r["session"]), []).append((lo, hi))
    out: list[dict] = []
    for (root, ses), spans in by.items():
        spans.sort()
        merged = [list(spans[0])]
        for lo, hi in spans[1:]:
            if lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        for lo, hi in merged:
            out.append({"root": root, "session": ses, "start_ns": lo, "end_ns": hi})
    return out


def quote_windows(windows: list[dict], env: dict) -> tuple[float, list[float]]:
    import databento as dbn
    client = dbn.Historical(env["DATABENTO_API_KEY"])
    lock = threading.Lock()
    costs = [0.0] * len(windows)
    done = [0]

    def one(i: int) -> None:
        w = windows[i]
        for attempt in range(5):
            try:
                c = client.metadata.get_cost(
                    dataset="OPRA.PILLAR", schema="tcbbo",
                    symbols=f"{w['root']}.OPT", stype_in="parent",
                    start=datetime.fromtimestamp(w["start_ns"] / 1e9, tz=ZoneInfo("UTC")),
                    end=datetime.fromtimestamp(w["end_ns"] / 1e9, tz=ZoneInfo("UTC")))
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
        costs[i] = float(c)
        with lock:
            done[0] += 1
            if done[0] % 250 == 0:
                print(f"  quoted {done[0]}/{len(windows)} running=${sum(costs):.2f}")

    with ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(one, range(len(windows))))
    return sum(costs), costs


AUTHORIZED_MAX = 30.0            # founder ceiling: "~$10-30, exact quote first"


def buy_windows(env: dict) -> None:
    """The founder-authorized charged step. Requests EXACTLY the quoted windows
    in select/windows.json — nothing else — and only if their quoted total is
    within the authorized ceiling. Resumable: windows already on disk are
    skipped (their cost was already incurred)."""
    import databento as dbn
    windows = json.loads((DATA_DIR / "select" / "windows.json").read_text())
    total = sum(w["cost"] for w in windows)
    assert total <= AUTHORIZED_MAX, f"quoted ${total:.2f} exceeds authorized ${AUTHORIZED_MAX}"
    out_dir = DATA_DIR / "tcbbo"
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = []
    for w in windows:
        w["path"] = out_dir / f"{w['root']}_{w['session']}_{w['start_ns']}.dbn.zst"
        if not w["path"].exists():
            todo.append(w)
    spent_skip = total - sum(w["cost"] for w in todo)
    print(f"buying {len(todo)}/{len(windows)} windows, quoted ${total:.2f} total "
          f"(${spent_skip:.2f} already on disk)")
    lock = threading.Lock()
    done = [0]
    local = threading.local()

    def one(w: dict) -> None:
        if not hasattr(local, "c"):
            local.c = dbn.Historical(env["DATABENTO_API_KEY"])
        for attempt in range(6):
            try:
                store = local.c.timeseries.get_range(
                    dataset="OPRA.PILLAR", schema="tcbbo",
                    symbols=f"{w['root']}.OPT", stype_in="parent",
                    start=datetime.fromtimestamp(w["start_ns"] / 1e9, tz=ZoneInfo("UTC")),
                    end=datetime.fromtimestamp(w["end_ns"] / 1e9, tz=ZoneInfo("UTC")))
                tmp = w["path"].with_suffix(".tmp")
                store.to_file(tmp)
                tmp.rename(w["path"])
                break
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(2 ** attempt)
        with lock:
            done[0] += 1
            if done[0] % 250 == 0:
                print(f"  bought {done[0]}/{len(todo)}")

    with ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(one, todo))
    n = len(list(out_dir.glob("*.dbn.zst")))
    print(f"PURCHASE COMPLETE: {n} window files on disk, quoted total ${total:.2f}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--top-k", type=int, default=3500)
    p.add_argument("--fetch-bars", action="store_true")
    p.add_argument("--buy", action="store_true")
    args = p.parse_args()

    env = load_env()
    if args.buy:
        buy_windows(env)
        return
    pacer = Pacer(4_000)
    ap = Alpaca(env, pacer)
    con = db_connect()
    skip = half_days(ap, env)

    if args.fetch_bars:
        rc = load_classification(FENCE_PATH)
        pairs = con.execute(
            "SELECT DISTINCT root, session FROM prints "
            "WHERE session >= ? AND session <= ?", (START, END)).fetchall()
        idents = sorted({rc.resolve(r, s) for r, s in pairs
                         if rc.classify(r, s) == "equity"})
        print(f"{len(idents)} equity underlyings to stage bars for")
        fetch_splits(env, rc, con)
        fetch_daily_bars(ap, idents, con)
        return

    kept = phase_a(con, skip)
    cands = shortlist(kept, args.top_k)
    windows = merge_windows(cands)
    print(f"shortlist {len(cands)} candidates -> {len(windows)} merged windows "
          f"({len({(w['root'], w['session']) for w in windows})} root-sessions)")
    total, costs = quote_windows(windows, env)
    out_dir = DATA_DIR / "select"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "shortlist.json").write_text(json.dumps(cands, indent=1))
    (out_dir / "windows.json").write_text(json.dumps(
        [{**w, "cost": c} for w, c in zip(windows, costs)], indent=1))
    print(f"EXACT QUOTE: ${total:.2f} for {len(windows)} tcbbo windows "
          f"(top-{args.top_k} proxy shortlist)")


if __name__ == "__main__":
    main()
