"""Join probe: Alpaca OPRA condition codes onto the Databento tcbbo tape.

Design doc §6b (FLOW_PACKAGE_ORIENTATION_DESIGN_2026-07-27.md): Alpaca's OPRA
entitlement delivers per-trade condition codes that Databento's tcbbo normalizes
away. This probe proves (or kills) the trade-for-trade join on one session,
BEFORE any capture daemon or sidecar is built. Read-only: no DB writes, no
production imports beyond the rollup's pure helpers.

Measures, in order:
  1. clock offset       — Alpaca `t` (SIP) minus DBN `ts_event` (exchange), on
                          prints whose (price, size) is unique on BOTH tapes
                          within a contract. Never assume the clocks agree.
  2. join rate          — order-preserving k-th-identical match per contract
                          within ±TOLERANCE_MS of the measured median offset.
  3. count reconciliation — per-contract print counts, both tapes, common
                          RTH window, with Alpaca cancel codes broken out.
  4. THE PAYOFF         — confusion matrix: the registered ns-collision
                          multileg heuristic (Addendum D) vs the actual OPRA
                          condition-code family, on matched prints. This is
                          roadmap 1.9's ground truth.
  5. venue map          — empirical publisher_id -> Alpaca exchange letter
                          vote table from matched prints (free byproduct).

    python tools/alpaca_condition_join_probe.py --date 2026-07-24 \
        --data-dir ~/dev/help_me_trade/data/databento

Needs APCA_API_KEY_ID / APCA_API_SECRET_KEY in the environment (or --env-file).
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.flow_corpus_rollup import _multileg  # noqa: E402  (pure helper)

PX = 1e9  # DBN fixed-point scale
TOLERANCE_MS = 100  # k-th-match window around the measured median offset
ALPACA_BATCH = 100  # symbols per historical-trades request (vendor cap)
PAGE_LIMIT = 10_000
# Common window both tapes are clipped to before counting anything: 09:30-16:00
# ET. The tcbbo file's own bounds and Alpaca's session edges differ; comparing
# uncliipped counts would manufacture mismatches at the edges.
RTH_START_ET, RTH_END_ET = "09:30", "16:00"

# OPRA post-2019 message-type families (verified against Alpaca's
# /v1beta1/options/meta/conditions/trade, 2026-07-27 — design doc §6b).
MULTILEG_CODES = frozenset("fghijlmtu")  # option-package legs
TIED_CODES = frozenset("knopqrs")  # stock-option combos (equity leg invisible here)
CANCEL_CODES = frozenset("ACEG")


def _et_bounds_ns(session: date) -> tuple[int, int]:
    """RTH window as epoch-ns, honest about DST via zoneinfo."""
    from datetime import datetime
    from datetime import time as dtime
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    lo = datetime.combine(session, dtime.fromisoformat(RTH_START_ET), tzinfo=et)
    hi = datetime.combine(session, dtime.fromisoformat(RTH_END_ET), tzinfo=et)
    return int(lo.timestamp() * 1e9), int(hi.timestamp() * 1e9)


def _load_defs_symbols(defs_path: Path) -> dict[int, tuple[str, str]]:
    """instrument_id -> (alpaca_symbol, underlying). C/P only.

    raw_symbol is the padded 21-char OSI; removing every space IS the
    conversion to Alpaca's compact OCC form (OSI roots never contain one) —
    same fact the live lane's occ_from_osi() fix established on 07-27.
    """
    import databento as dbn

    arr = dbn.DBNStore.from_file(defs_path).to_ndarray()
    cls = arr["instrument_class"]
    arr = arr[(cls == b"C") | (cls == b"P")]
    out: dict[int, tuple[str, str]] = {}
    ids = arr["instrument_id"].astype(np.int64)
    raws = arr["raw_symbol"].astype("U32")
    unds = np.char.strip(arr["underlying"].astype("U16"))
    for i in range(len(arr)):
        out[int(ids[i])] = (raws[i].replace(" ", ""), str(unds[i]))
    return out


def _fetch_alpaca_trades(
    symbols: list[str], session: date, headers: dict[str, str]
) -> dict[str, list[tuple[int, int, int, str, str]]]:
    """symbol -> [(t_ns, price_cents, size, exchange, condition), ...] sorted."""
    out: dict[str, list[tuple[int, int, int, str, str]]] = collections.defaultdict(list)
    start = f"{session}T{RTH_START_ET}:00-04:00"
    end = f"{session}T{RTH_END_ET}:00-04:00"
    n_req = 0
    for i in range(0, len(symbols), ALPACA_BATCH):
        batch = ",".join(symbols[i : i + ALPACA_BATCH])
        tok = None
        while True:
            q = {"symbols": batch, "start": start, "end": end, "limit": PAGE_LIMIT}
            if tok:
                q["page_token"] = tok
            url = (
                "https://data.alpaca.markets/v1beta1/options/trades?"
                + urllib.parse.urlencode(q)
            )
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                d = json.load(resp)
            n_req += 1
            for sym, trades in (d.get("trades") or {}).items():
                for t in trades:
                    ts = np.datetime64(t["t"].replace("Z", ""), "ns").astype(np.int64)
                    cond = t.get("c")
                    out[sym].append(
                        (
                            int(ts),
                            int(round(float(t["p"]) * 100)),
                            int(t["s"]),
                            str(t.get("x") or ""),
                            str(cond) if cond is not None else "",
                        )
                    )
            tok = d.get("next_page_token")
            if not tok:
                break
    for sym in out:
        out[sym].sort()
    print(f"  alpaca: {n_req} requests, {sum(len(v) for v in out.values()):,} trades")
    return dict(out)


def probe(session: date, data_dir: Path, underlyings: list[str], max_contracts: int) -> None:
    import databento as dbn

    tpath = data_dir / "opra_tcbbo" / f"{session.isoformat()}.dbn.zst"
    dpath = data_dir / "opra_defs" / f"{session.isoformat()}.dbn.zst"
    for p in (tpath, dpath):
        if not p.exists():
            raise SystemExit(f"missing: {p}")

    print(f"=== condition-code join probe — {session} ===")
    id_map = _load_defs_symbols(dpath)
    print(f"  defs: {len(id_map):,} C/P contracts")

    arr = dbn.DBNStore.from_file(tpath).to_ndarray()
    ts_all = arr["ts_event"].astype(np.int64)
    iid_all = arr["instrument_id"].astype(np.int64)
    # The multileg flag needs the FULL tape — a collision partner may be a
    # contract outside the probe's underlying set.
    ml_all = _multileg(ts_all, iid_all)

    lo, hi = _et_bounds_ns(session)
    want = set(underlyings)
    keep = np.zeros(len(arr), dtype=bool)
    sym_of: dict[int, str] = {}
    for i, pid in enumerate(iid_all):
        m = id_map.get(int(pid))
        if m is not None and m[1] in want:
            keep[i] = True
            sym_of[int(pid)] = m[0]
    keep &= (ts_all >= lo) & (ts_all < hi)
    prints = arr[keep]
    ml = ml_all[keep]
    ts = ts_all[keep]
    iid = iid_all[keep]
    cents = np.round(prints["price"].astype(np.float64) / PX * 100).astype(np.int64)
    size = prints["size"].astype(np.int64)
    pub = prints["publisher_id"].astype(np.int64)
    print(f"  dbn: {len(prints):,} prints on {len(want)} underlyings in RTH")

    # top-N contracts per underlying by print count
    per_und: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for pid_i in iid:
        s, u = id_map[int(pid_i)][0], id_map[int(pid_i)][1]
        per_und[u][int(pid_i)] += 1
    chosen: set[int] = set()
    for u, ctr in per_und.items():
        chosen.update(pid for pid, _ in ctr.most_common(max_contracts))
    sel = np.isin(iid, np.fromiter(chosen, dtype=np.int64))
    ts, iid, cents, size, pub, ml = ts[sel], iid[sel], cents[sel], size[sel], pub[sel], ml[sel]
    print(f"  dbn: {len(ts):,} prints on {len(chosen):,} selected contracts "
          f"(top {max_contracts}/underlying)")

    key, sec = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not key or not sec:
        raise SystemExit("APCA_API_KEY_ID / APCA_API_SECRET_KEY not in environment")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}
    al = _fetch_alpaca_trades(sorted({sym_of[int(p)] for p in chosen}), session, headers)

    # ---- per-contract structures on the DBN side --------------------------------
    dbn_by_sym: dict[str, list[tuple[int, int, int, int, bool]]] = collections.defaultdict(list)
    for i in range(len(ts)):
        dbn_by_sym[sym_of[int(iid[i])]].append(
            (int(ts[i]), int(cents[i]), int(size[i]), int(pub[i]), bool(ml[i]))
        )
    for v in dbn_by_sym.values():
        v.sort()

    # ---- 1. clock offset on doubly-unique (price,size) keys ---------------------
    deltas = []
    for sym, dv in dbn_by_sym.items():
        av = al.get(sym)
        if not av:
            continue
        dkeys = collections.Counter((c, s) for _, c, s, _, _ in dv)
        akeys = collections.Counter((c, s) for _, c, s, _, _ in av)
        uniq = {k for k, n in dkeys.items() if n == 1} & {
            k for k, n in akeys.items() if n == 1
        }
        if not uniq:
            continue
        dmap = {(c, s): t for t, c, s, _, _ in dv if (c, s) in uniq}
        amap = {(c, s): t for t, c, s, _, _ in av if (c, s) in uniq}
        deltas.extend(amap[k] - dmap[k] for k in uniq)
    if not deltas:
        raise SystemExit("no doubly-unique keys — cannot measure offset")
    deltas = np.array(deltas, dtype=np.int64)
    med_off = int(np.median(deltas))
    ms = deltas / 1e6
    print(f"\n[1] clock offset (alpaca − dbn), {len(deltas):,} doubly-unique prints:")
    print(f"    p1 {np.percentile(ms, 1):+.3f}ms  p50 {np.percentile(ms, 50):+.3f}ms  "
          f"p99 {np.percentile(ms, 99):+.3f}ms   within ±{TOLERANCE_MS}ms: "
          f"{(np.abs(ms - med_off / 1e6) <= TOLERANCE_MS).mean():.2%}")

    # ---- 2+3. order-preserving k-th match + reconciliation ----------------------
    tol_ns = TOLERANCE_MS * 1_000_000
    matched = 0
    dbn_total = 0
    al_total = 0
    mismatch_syms = 0
    census = collections.Counter()
    census_matched_ml = collections.Counter()  # condition -> heuristic-flag counter
    census_prem = collections.Counter()
    venue_votes = collections.Counter()
    cancels = 0
    for sym, dv in dbn_by_sym.items():
        av = al.get(sym, [])
        dbn_total += len(dv)
        al_total += len(av)
        cancels += sum(1 for _, _, _, _, c in av if c in CANCEL_CODES)
        if len(dv) != len(av):
            mismatch_syms += 1
        dq: dict[tuple[int, int], list] = collections.defaultdict(list)
        for t, c, s, p, m in dv:
            dq[(c, s)].append((t, p, m))
        aq: dict[tuple[int, int], list] = collections.defaultdict(list)
        for t, c, s, x, cond in av:
            census[cond or "<absent>"] += 1
            census_prem[cond or "<absent>"] += c / 100.0 * s * 100.0
            aq[(c, s)].append((t, x, cond))
        for k_, dl in dq.items():
            alk = aq.get(k_)
            if not alk:
                continue
            for j in range(min(len(dl), len(alk))):
                dt, dp, dm = dl[j]
                at, ax, ac = alk[j]
                if abs((at - dt) - med_off) <= tol_ns:
                    matched += 1
                    venue_votes[(dp, ax)] += 1
                    census_matched_ml[(ac, dm)] += 1

    print(f"\n[2] join: dbn {dbn_total:,} / alpaca {al_total:,} prints; "
          f"matched {matched:,} = {matched / max(dbn_total, 1):.2%} of dbn, "
          f"{matched / max(al_total, 1):.2%} of alpaca")
    print(f"[3] per-contract count mismatch: {mismatch_syms}/{len(dbn_by_sym)} contracts; "
          f"alpaca cancel-coded prints: {cancels:,} "
          f"({cancels / max(al_total, 1):.3%})")

    # ---- 4. THE PAYOFF: heuristic vs codes --------------------------------------
    tp = sum(n for (c, m), n in census_matched_ml.items() if m and c in MULTILEG_CODES)
    fp = sum(n for (c, m), n in census_matched_ml.items() if m and c not in MULTILEG_CODES)
    fn = sum(n for (c, m), n in census_matched_ml.items() if not m and c in MULTILEG_CODES)
    tn = sum(n for (c, m), n in census_matched_ml.items() if not m and c not in MULTILEG_CODES)
    print(f"\n[4] ns-collision heuristic vs OPRA multi-leg codes ({MULTILEG_CODES!r}),"
          f" on {tp + fp + fn + tn:,} matched prints:")
    print(f"    TP {tp:,}  FP {fp:,}  FN {fn:,}  TN {tn:,}")
    if tp + fp:
        print(f"    precision {tp / (tp + fp):.2%}  ", end="")
    if tp + fn:
        print(f"recall {tp / (tp + fn):.2%}")
    fp_codes = collections.Counter(
        {c: n for (c, m), n in census_matched_ml.items() if m and c not in MULTILEG_CODES}
    )
    fn_codes = collections.Counter(
        {c: n for (c, m), n in census_matched_ml.items() if not m and c in MULTILEG_CODES}
    )
    print(f"    heuristic-flagged, non-ML codes (top): {fp_codes.most_common(6)}")
    print(f"    heuristic-missed, ML codes (top):      {fn_codes.most_common(6)}")

    tot_p = sum(census_prem.values()) or 1.0
    print(f"\n[5] condition census (alpaca side, {sum(census.values()):,} prints):")
    for c, n in census.most_common(14):
        fam = (
            "MULTI-LEG" if c in MULTILEG_CODES
            else "TIED" if c in TIED_CODES
            else "CANCEL" if c in CANCEL_CODES
            else ""
        )
        print(f"    c={c!r:6} {n:>9,} ({n / max(al_total, 1):6.2%})  "
              f"premium {census_prem[c] / 1e6:>8.2f}M ({census_prem[c] / tot_p:6.2%})  {fam}")

    top_v = venue_votes.most_common(20)
    pubs = collections.defaultdict(collections.Counter)
    for (p, x), n in venue_votes.items():
        pubs[p][x] += n
    amb = sum(1 for p, ctr in pubs.items() if ctr.most_common(1)[0][1] / sum(ctr.values()) < 0.95)
    print(f"\n[6] venue map: {len(pubs)} publisher_ids seen; "
          f"{amb} ambiguous (<95% one letter). Top pairs: {top_v[:8]}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="2026-07-24")
    p.add_argument("--data-dir", default=None,
                   help="databento data dir (default: <repo>/data/databento)")
    p.add_argument("--underlyings",
                   default="TSLA,NVDA,AAPL,AMD,META,PLTR,COIN,HOOD")
    p.add_argument("--max-contracts", type=int, default=250)
    p.add_argument("--env-file", default=None,
                   help="optional .env to source APCA keys from")
    a = p.parse_args()
    if a.env_file:
        for line in Path(a.env_file).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    dd = Path(a.data_dir).expanduser() if a.data_dir else (
        Path(__file__).resolve().parent.parent / "data" / "databento"
    )
    probe(
        date.fromisoformat(a.date),
        dd,
        [u.strip().upper() for u in a.underlyings.split(",") if u.strip()],
        a.max_contracts,
    )
