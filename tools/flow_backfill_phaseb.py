#!/usr/bin/env python3
"""Flow 3.3 backfill phase B, stage 1 — the registered pipeline over the
purchased tcbbo windows.

Registered context: `References/FLOW_ROADMAP.md` → backfill round 3 (phase-A
block). Input = the 6,651 corrected-registration ±330s parent windows
(`select/windows.json`, all purchased — the corrected-basis top-10k; 1,338
round-1-only windows remain on disk unused) + the phase-A shortlist. Output =
the
backfill stage-1 candidate set with the same fields the corpus writes, plus a
per-window provenance record (join rate, drops).

Fidelity contract — every load-bearing primitive is IMPORTED from the corpus
code, never re-implemented:
  - grouping `_order_groups`, package heuristic `_multileg`, quote fence + side
    `fence_and_classify` (tools/flow_corpus_rollup.py — the ONE implementation);
  - condition-code join `match_prints` at the REGISTERED ±100ms tolerance
    (tools/condition_labels.py) — measured on the corrected-registration run
    2026-08-02 (post halo-clip): session-aggregate join rates 0.787–1.000;
    40 sessions (all 2024) fell below the 0.98 floor and were DROPPED by the
    session gate (132 records). The a-side comes from FREE historical Alpaca
    option trades — the
    same vendor feed the corpus's live capture recorded, so this is the
    corpus's own provenance, fetched after the fact;
  - `single_leg_groups`, `group_side`, `fit_delta`, `hedge_shares`,
    `time_to_expiry_years`, window/fence predicates and every registered
    constant (tools/hedge_footprint.py).

Disclosed adaptations for window fragments (the addendum's own clauses):
  - the ≥98% join floor (Addendum E) applies at the SESSION AGGREGATE across
    that session's windows — the floor is a capture-quality gate measured at
    session grain, and a 10-print fragment fails it stochastically on one
    unjoined print (measured: parity misses were 100% this). Print-level
    protection is the registered per-group unjoined rule, unchanged;
  - contract identity comes from the OSI raw symbol (strike/exp/cp parsed
    directly), not the definitions rollup — same information, no defs file;
  - the isolation base is the window's own single-leg groups, and a candidate
    is ELIGIBLE only when its full ±120s neighbourhood lies inside the window
    (drop_edge_context) — the window-defining shortlist print always qualifies
    (±330s pad); incidental groups near an edge are excluded rather than
    scored on a truncated base (parity measured the truncation as pure
    over-keep);
  - prior_close/ADV20 come from `flow_backfill_select.prior_close_adv`: RAW
    Alpaca dailies (equity_bars_raw) on the AS-OF-SESSION split basis via the
    date-scoped splits table — the basis the session's OSI strikes are on
    (`adjustment=split` today-basis was review blocker 2), same
    strictly-before window shape as db/flow_corpus.py.

Corpus-parity gate (2026-03-06, measured 2026-08-02): 925/927 candidates
reproduced with h_select exact to 1e-6, 0 extras. The 2 residual misses are
marginal isolation flips ($12 and $535 over the 25% line) where the CORPUS
side under-counted its neighbour base — its realtime capture left neighbour
prints unjoined (session-wide k-rank misalignment from capture gaps) that the
complete historical a-side joins; the registered rule on the more complete
a-side is the stricter, more correct application.

Usage (dev tree):
  .venv/bin/python tools/flow_backfill_phaseb.py --run
  .venv/bin/python tools/flow_backfill_phaseb.py --parity 2026-03-06   # corpus-parity check
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.condition_labels import JOIN_FLOOR, TOL_NS, match_prints  # noqa: E402
from tools.flow_backfill_crawl import (  # noqa: E402
    Alpaca,
    Pacer,
    load_env,
    parse_ns,
)
from tools.flow_backfill_select import prior_close_adv  # noqa: E402
from tools.flow_corpus_rollup import (  # noqa: E402
    PX,
    _multileg,
    _order_groups,
    fence_and_classify,
)
from tools.hedge_footprint import (  # noqa: E402
    ADV_MIN,
    DELTA_BAND,
    DELTA_BAND_SECONDARY,
    DTE_MIN,
    ISOLATION_MAX_FRAC,
    ISOLATION_WINDOW_NS,
    PREMIUM_MIN,
    PRICE_MIN,
    et_second_of_day,
    fit_delta,
    hedge_shares,
    in_release_fence,
    in_trading_window,
    session_midnight_ns,
    single_leg_groups,
    time_to_expiry_years,
)
from tools.hedge_footprint import (  # noqa: E402
    group_side as hf_group_side,
)
from tools.root_classification_build import load as load_classification  # noqa: E402

DATA_DIR = ROOT / "data" / "research" / "flow_backfill"
TCBBO_WIN_DIR = DATA_DIR / "tcbbo"
OUT_DIR = DATA_DIR / "phaseb"
FENCE_PATH = ROOT / "signals" / "options_flow" / "root_classification.json"
UNDEF_SIZE = 4_294_967_295

LEDGER_KEYS = (
    "windows", "join_matched", "join_total", "groups",
    "drop_not_single_leg_unjoined", "drop_not_single_leg_package_or_heuristic",
    "drop_side_not_unanimous", "drop_premium", "drop_dte", "drop_time_window",
    "drop_release_fence", "drop_edge_context", "drop_no_prior_close",
    "drop_price_min", "drop_adv",
    "drop_no_option_quote", "drop_iv_sub_intrinsic", "drop_iv_no_solution",
    "drop_delta_band", "drop_delta_unstable", "drop_isolation",
    "kept_primary_band", "kept_secondary_band",
)


def parse_osi_arr(sym: str) -> tuple[str, int, bool, float]:
    """OSI → (root, exp_days_since_epoch, is_put, strike)."""
    body = sym[-15:]
    root = sym[:-15]
    exp = date(2000 + int(body[0:2]), int(body[2:4]), int(body[4:6]))
    return root, (exp - date(1970, 1, 1)).days, body[6] == "P", int(body[7:]) / 1000.0


def fetch_window_conds(ap: Alpaca, contracts: list[str], start_ns: int,
                       end_ns: int) -> tuple[list, list, list, list, list]:
    """Historical Alpaca option trades for the window — the a-side of the join."""
    def iso(ns: int) -> str:
        return datetime.fromtimestamp(ns / 1e9, tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # Halo both bounds by the registered ±100ms join tolerance: an a-side
    # trade stamped just outside the window can still be the join partner of
    # a boundary b-side print (review fix). The halo also covers iso()'s
    # ms-floor, which alone cost the window's LAST print — very often the
    # candidate itself (isolated blocks in sparse names).
    start_ns -= TOL_NS
    end_ns += TOL_NS + 1_000_000
    a_sym, a_t, a_cents, a_size, a_cond = [], [], [], [], []
    for i in range(0, len(contracts), 50):
        js = ap.paged("/v1beta1/options/trades", {
            "symbols": ",".join(contracts[i:i + 50]), "start": iso(start_ns),
            "end": iso(end_ns), "limit": 10_000}, key="trades")
        for sym, rows in js.items():
            for r in rows:
                a_sym.append(sym)
                a_t.append(parse_ns(r["t"]))
                a_cents.append(round(r["p"] * 100))
                a_size.append(int(r["s"]))
                c = r.get("c", "")
                a_cond.append(c if isinstance(c, str) else (c[0] if c else ""))
    return a_sym, a_t, a_cents, a_size, a_cond


def window_stage1(df, session: str, root: str, ident: str,
                  pc_adv: dict[str, tuple[float, float]],
                  ap: Alpaca | None, led: dict,
                  a_side: tuple | None = None,
                  win_lo: int | None = None,
                  win_hi: int | None = None) -> tuple[list[dict], float | None]:
    """The registered stage-1 pass over one window's prints.

    `df` must be the tcbbo frame with RAW fixed-point prices (pretty_px=False)
    and int ts (pretty_ts=False), columns incl. symbol, instrument_id, price,
    size, bid_px_00, ask_px_00 and ts_event (column or index).
    `a_side` overrides the Alpaca fetch (parity mode feeds the corpus capture).
    `win_lo`/`win_hi` are the TRUE window bounds (ns): a candidate is eligible
    only when its full ±120s isolation neighbourhood lies inside the window —
    an edge-truncated base under-counts neighbour premium and over-keeps
    (measured in parity: every extra_kept traced to this). The window-defining
    shortlist print always qualifies (±330s pad).
    """
    if "ts_event" in df.columns:
        ts_ev = df["ts_event"].to_numpy().astype(np.int64)
    else:
        ts_ev = df.index.to_numpy().astype(np.int64)
    keep_rows = (df["size"].to_numpy() != UNDEF_SIZE) & df["symbol"].notna().to_numpy()
    df = df[keep_rows]
    ts_ev = ts_ev[keep_rows]
    if not len(df):
        return [], None
    syms = df["symbol"].str.replace(" ", "").to_numpy().astype("U24")
    iid = df["instrument_id"].to_numpy().astype(np.int64)
    price_raw = df["price"].to_numpy().astype(np.int64)
    size = df["size"].to_numpy().astype(np.int64)

    # --- condition codes: registered join core on the historical a-side -----
    contracts = sorted(set(syms.tolist()))
    cindex = {s: i for i, s in enumerate(contracts)}
    if a_side is None:
        a_sym, a_t, a_cents, a_size, a_cond = fetch_window_conds(
            ap, contracts, int(ts_ev.min()), int(ts_ev.max()) + 1)
    else:
        a_sym, a_t, a_cents, a_size, a_cond = a_side
    # CLIP the a-side to the print tape's own [min, max] before the join.
    # match_prints joins on occurrence RANK per (contract, cents, size) key —
    # a halo-strip a-side trade with no b-side counterpart shifts the rank of
    # every later same-key trade and can desynchronize an arbitrarily long
    # run (delta-review-B blocker: KSS 2024-09-16, 40 orphans -> 1,800 lost
    # joins + 158 silent relabels). The halo stays on the FETCH (it defeats
    # iso()'s ms floor); measured across 61 A/B windows, zero recovered joins
    # had a partner outside these bounds (participant-stamp offset = 0 ns).
    if len(a_t):
        _at = np.asarray(a_t, dtype=np.int64)
        keep = (_at >= int(ts_ev.min())) & (_at <= int(ts_ev.max()))
        a_sym = np.asarray(a_sym)[keep]
        a_t = _at[keep]
        a_cents = np.asarray(a_cents, dtype=np.int64)[keep]
        a_size = np.asarray(a_size, dtype=np.int64)[keep]
        a_cond = np.asarray(a_cond, dtype="U1")[keep]
    d_cid = np.array([cindex[s] for s in syms], dtype=np.int64)
    a_cid = np.array([cindex.get(s, -1) for s in a_sym], dtype=np.int64)
    d_cents = np.round(price_raw / PX * 100.0).astype(np.int64)
    cond = np.full(len(ts_ev), "", dtype="U1")
    n_matched = 0
    if len(a_t):
        dr, ar = match_prints(
            d_cid, d_cents, size, ts_ev, a_cid,
            np.asarray(a_cents, dtype=np.int64),
            np.asarray(a_size, dtype=np.int64),
            np.asarray(a_t, dtype=np.int64))
        cond[dr] = np.asarray(a_cond, dtype="U1")[ar]
        n_matched = len(dr)
    join_rate = n_matched / len(ts_ev)
    # NO per-window floor: Addendum E's 98% floor is a SESSION-grain capture
    # gate (measured over millions of prints); at 10-100-print fragments one
    # unjoined print fails it stochastically. The caller aggregates
    # (matched, total) per session and applies the floor at the corpus's own
    # grain; print-level protection is the registered per-group unjoined rule.
    led["join_matched"] += n_matched
    led["join_total"] += len(ts_ev)

    # --- registered grouping + fences --------------------------------------
    arr = {k: df[k].to_numpy() for k in ("price", "size", "bid_px_00", "ask_px_00")}

    class _A:
        def __init__(self, d):
            self._d = d

        def __getitem__(self, k):
            return self._d[k]

        def __len__(self):
            return len(self._d["price"])

    fx = fence_and_classify(_A(arr))
    ml_heur = _multileg(ts_ev, iid)
    gid, n_groups = _order_groups(ts_ev, iid)
    led["groups"] += n_groups

    first = np.zeros(n_groups, dtype=np.int64)
    first[gid] = np.arange(len(gid), dtype=np.int64)
    g_ts = ts_ev[first]
    g_sym = syms[first]
    g_contracts = np.bincount(gid, weights=fx.size, minlength=n_groups)
    g_premium = np.bincount(gid, weights=fx.premium, minlength=n_groups)

    single, has_unjoined, has_pkg = single_leg_groups(gid, n_groups, cond, ml_heur)
    led["drop_not_single_leg_unjoined"] += int(has_unjoined.sum())
    led["drop_not_single_leg_package_or_heuristic"] += int((~has_unjoined & has_pkg).sum())
    keep = single.copy()

    sides = hf_group_side(gid, n_groups, fx.side)
    led["drop_side_not_unanimous"] += int((keep & (sides < 0)).sum())
    keep &= sides >= 0

    def _drop(mask: np.ndarray, key: str) -> None:
        nonlocal keep
        led[key] += int((keep & ~mask).sum())
        keep &= mask

    _drop(g_premium >= PREMIUM_MIN, "drop_premium")
    meta = [parse_osi_arr(s) for s in g_sym]
    exp_days = np.array([m[1] for m in meta], dtype=np.int64)
    is_put = np.array([m[2] for m in meta], dtype=bool)
    strike = np.array([m[3] for m in meta], dtype=np.float64)
    ses_days = (date.fromisoformat(session) - date(1970, 1, 1)).days
    dte = exp_days - ses_days
    _drop(dte >= DTE_MIN, "drop_dte")
    midnight = session_midnight_ns(date.fromisoformat(session))
    sec = et_second_of_day(g_ts, midnight)
    _drop(in_trading_window(sec), "drop_time_window")
    _drop(~in_release_fence(sec), "drop_release_fence")
    if win_lo is not None and win_hi is not None:
        _drop((g_ts - win_lo >= ISOLATION_WINDOW_NS)
              & (win_hi - g_ts >= ISOLATION_WINDOW_NS), "drop_edge_context")

    pc = pc_adv.get(ident)
    if pc is None:
        led["drop_no_prior_close"] += int(keep.sum())
        return [], join_rate
    prior_close, adv = pc
    if prior_close < PRICE_MIN:
        led["drop_price_min"] += int(keep.sum())
        return [], join_rate
    if adv < ADV_MIN:
        led["drop_adv"] += int(keep.sum())
        return [], join_rate

    good = fx.good_q
    bid = arr["bid_px_00"].astype(np.float64) / PX
    ask = arr["ask_px_00"].astype(np.float64) / PX
    mid = np.where(good, (bid + ask) / 2.0, np.nan)
    q_n = np.bincount(gid, weights=good.astype(np.float64), minlength=n_groups)
    q_sum = np.bincount(gid, weights=np.where(good, mid, 0.0), minlength=n_groups)
    with np.errstate(invalid="ignore", divide="ignore"):
        g_mid = np.where(q_n > 0, q_sum / np.maximum(q_n, 1.0), np.nan)
    _drop(np.isfinite(g_mid) & (g_mid > 0.0), "drop_no_option_quote")

    # isolation base: every single-leg group in the window, any size
    iso_sel = single
    iso_ts = g_ts[iso_sel]
    iso_prem = g_premium[iso_sel]
    iso_order = np.argsort(iso_ts, kind="stable")
    iso_ts, iso_prem = iso_ts[iso_order], iso_prem[iso_order]
    cum = np.concatenate([[0.0], np.cumsum(iso_prem, dtype=np.float64)])

    band_lo, band_hi = DELTA_BAND
    wide_lo, wide_hi = DELTA_BAND_SECONDARY
    recs: list[dict] = []
    for i in np.flatnonzero(keep):
        t_years = time_to_expiry_years(int(g_ts[i]), int(exp_days[i]))
        if t_years <= 0.0:
            led["drop_iv_no_solution"] += 1
            continue
        fit = fit_delta(float(g_mid[i]), float(prior_close),
                        float(strike[i]), t_years, bool(is_put[i]))
        if not fit.ok:
            led[{"sub_intrinsic": "drop_iv_sub_intrinsic",
                 "no_iv": "drop_iv_no_solution",
                 "unstable": "drop_delta_unstable"}[fit.reason]] += 1
            continue
        a = abs(fit.delta)
        if not (wide_lo <= a <= wide_hi):
            led["drop_delta_band"] += 1
            continue
        lo = np.searchsorted(iso_ts, g_ts[i] - ISOLATION_WINDOW_NS, side="left")
        hi = np.searchsorted(iso_ts, g_ts[i] + ISOLATION_WINDOW_NS, side="right")
        others = float(cum[hi] - cum[lo]) - float(g_premium[i])
        others = max(others, 0.0)
        if others > ISOLATION_MAX_FRAC * float(g_premium[i]):
            led["drop_isolation"] += 1
            continue
        side_sign = 1 if sides[i] == 0 else -1
        h = hedge_shares(side_sign, fit.delta, g_contracts[i])
        band = "primary" if band_lo <= a <= band_hi else "secondary"
        led["kept_primary_band" if band == "primary" else "kept_secondary_band"] += 1
        recs.append({
            "session": session, "ts_ns": int(g_ts[i]), "root": root,
            "symbol": ident, "osi": str(g_sym[i]), "stratum": "equity",
            "is_put": bool(is_put[i]), "strike": float(strike[i]),
            "exp_days": int(exp_days[i]), "dte": int(dte[i]),
            "contracts": int(g_contracts[i]), "premium": float(g_premium[i]),
            "side_sign": side_sign, "option_quote_mid": float(g_mid[i]),
            "prior_close": float(prior_close), "adv": float(adv),
            "iv_select": float(fit.iv), "delta_select": float(fit.delta),
            "delta_swing": float(fit.delta_swing),
            "h_select": float(h), "abs_h_select": abs(float(h)),
            "neighbour_premium": others, "band": band,
        })
    return recs, join_rate


def run(workers: int = 12) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    import databento as dbn
    env = load_env()
    ap = Alpaca(env, Pacer(4_000))
    rc = load_classification(FENCE_PATH)
    windows = json.loads((DATA_DIR / "select" / "windows.json").read_text())
    done_path = OUT_DIR / "done_windows.jsonl"
    done = set()
    if done_path.exists():
        done = {json.loads(x)["window"] for x in done_path.read_text().splitlines() if x}
    todo = [w for w in windows
            if f"{w['root']}_{w['session']}_{w['start_ns']}" not in done]
    print(f"phase B stage 1: {len(todo)}/{len(windows)} windows to process")

    import sqlite3
    con = sqlite3.connect(DATA_DIR / "backfill.db", check_same_thread=False)
    pc_cache: dict[str, dict] = {}
    pc_lock = threading.Lock()

    def pc_for(session: str) -> dict:
        with pc_lock:
            if session not in pc_cache:
                if len(pc_cache) > 40:
                    pc_cache.clear()
                pc_cache[session] = prior_close_adv(con, session)
            return pc_cache[session]

    led = defaultdict(int)
    led_lock = threading.Lock()
    out_lock = threading.Lock()
    rec_f = open(OUT_DIR / "stage1_records.jsonl", "a")
    done_f = open(done_path, "a")
    counter = [0]

    def one(w: dict) -> None:
        wid = f"{w['root']}_{w['session']}_{w['start_ns']}"
        p = TCBBO_WIN_DIR / f"{wid}.dbn.zst"
        df = dbn.DBNStore.from_file(p).to_df(
            price_type="fixed", pretty_ts=False, map_symbols=True)
        my_led = defaultdict(int)
        my_led["windows"] = 1
        ident = rc.resolve(w["root"], w["session"])
        recs, jr = window_stage1(df, w["session"], w["root"], ident,
                                 pc_for(w["session"]), ap, my_led,
                                 win_lo=w["start_ns"], win_hi=w["end_ns"])
        with led_lock:
            for k, v in my_led.items():
                led[k] += v
        with out_lock:
            for r in recs:
                rec_f.write(json.dumps(r) + "\n")
            done_f.write(json.dumps(
                {"window": wid, "session": w["session"], "join_rate": jr,
                 "matched": my_led["join_matched"],
                 "total": my_led["join_total"], "kept": len(recs)}) + "\n")
            rec_f.flush()
            done_f.flush()
            counter[0] += 1
            if counter[0] % 250 == 0:
                print(f"  {counter[0]}/{len(todo)} windows")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, todo))

    # --- Addendum E floor at the corpus's own grain: SESSION aggregate ------
    ses_join: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for line in done_path.read_text().splitlines():
        if not line:
            continue
        row = json.loads(line)
        ses_join[row.get("session", row["window"].split("_")[-2])][0] += row.get("matched", 0)
        ses_join[row.get("session", row["window"].split("_")[-2])][1] += row.get("total", 0)
    below = {s for s, (m, t) in ses_join.items() if t and m / t < JOIN_FLOOR}
    seen: set[tuple] = set()
    final: list[dict] = []
    dropped_floor = 0
    for line in (OUT_DIR / "stage1_records.jsonl").read_text().splitlines():
        if not line:
            continue
        r = json.loads(line)
        key = (r["session"], r["ts_ns"], r["osi"])
        if key in seen:
            continue
        seen.add(key)
        if r["session"] in below:
            dropped_floor += 1
            continue
        final.append(r)
    led["sessions_below_join_floor"] = len(below)
    led["records_dropped_below_floor"] = dropped_floor
    (OUT_DIR / "stage1_records_final.json").write_text(json.dumps(final, indent=1))
    (OUT_DIR / "session_join_rates.json").write_text(json.dumps(
        {s: {"matched": m, "total": t, "rate": (m / t if t else None)}
         for s, (m, t) in sorted(ses_join.items())}, indent=1))
    (OUT_DIR / "stage1_ledger.json").write_text(
        json.dumps({k: led[k] for k in LEDGER_KEYS} |
                   {k: v for k, v in led.items() if k not in LEDGER_KEYS},
                   indent=1))
    print(f"STAGE1 COMPLETE: {len(final)} records "
          f"({len(below)} sessions below join floor, {dropped_floor} records dropped)")
    print(json.dumps({k: led[k] for k in LEDGER_KEYS}))


def parity(session: str) -> None:
    """Corpus-parity check: cut ±330s windows around the corpus WIDE stage-1
    candidates from the on-disk full-session tcbbo tape, run this pipeline on
    the fragments with the SAME historical-Alpaca a-side, and diff against the
    corpus's own per-session candidate records."""
    import databento as dbn

    from tools.flow_corpus_rollup import TCBBO_DIR
    wide = ROOT / "data" / "research" / "hedge_footprint_wide" / "equity" / "select"
    cand_path = wide / f"{session}_candidates.json"
    corpus = json.loads(cand_path.read_text())
    if not corpus:
        print(f"{session}: no corpus candidates")
        return
    env = load_env()
    ap = Alpaca(env, Pacer(4_000))
    rc = load_classification(FENCE_PATH)
    store = dbn.DBNStore.from_file(TCBBO_DIR / f"{session}.dbn.zst")
    df_all = store.to_df(price_type="fixed", pretty_ts=False, map_symbols=False)
    # ALL_SYMBOLS session tapes carry no symbology — map ids via the corpus
    # defs file, exactly as the corpus pipeline does
    defs = dbn.DBNStore.from_file(
        ROOT / "data" / "databento" / "opra_defs" / f"{session}.dbn.zst"
    ).to_ndarray()
    cls_ = defs["instrument_class"]
    defs = defs[(cls_ == b"C") | (cls_ == b"P")]
    id2sym = dict(zip(defs["instrument_id"].astype(np.int64).tolist(),
                      np.char.replace(defs["raw_symbol"].astype("U24"), " ", "").tolist()))
    df_all = df_all.assign(symbol=df_all["instrument_id"].map(id2sym))
    ts_all = (df_all["ts_event"] if "ts_event" in df_all.columns
              else df_all.index).to_numpy().astype(np.int64)

    # corpus-era session: prior_close/ADV must come from the corpus DB so the
    # inputs are byte-identical to the corpus's own run
    from db.flow_corpus import bars_before_date
    from tools.hedge_footprint import adv_before_date
    closes = bars_before_date(session)
    adv_map = adv_before_date(date.fromisoformat(session))
    pc_adv = {s: (closes[s][0], adv_map[s]) for s in closes if s in adv_map}

    pad = 330 * 1_000_000_000
    by_root: dict[str, list[dict]] = defaultdict(list)
    for c in corpus:
        by_root[c["root"]].append(c)
    n_match = n_miss = n_extra = 0
    for root, cands in sorted(by_root.items()):
        ident = rc.resolve(root, session)
        roots_arr = df_all["symbol"].str.replace(" ", "").str.startswith(root).fillna(False)
        spans = sorted((c["ts_ns"] - pad, c["ts_ns"] + pad) for c in cands)
        merged = [list(spans[0])]
        for lo, hi in spans[1:]:
            if lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        led = defaultdict(int)
        recs = []
        for lo, hi in merged:
            sel = roots_arr.to_numpy(dtype=bool) & (ts_all >= lo) & (ts_all <= hi)
            sub = df_all[sel]
            # guard against sibling roots sharing a prefix (e.g. BRK vs BRKB)
            osi_roots = sub["symbol"].str.replace(" ", "").str[:-15]
            sub = sub[(osi_roots == root).to_numpy()]
            if not len(sub):
                continue
            r, jr = window_stage1(sub, session, root, ident, pc_adv, ap, led,
                                  win_lo=lo, win_hi=hi)
            recs.extend(r)
        ours = {(r["ts_ns"], r["osi"]): r for r in recs}
        for c in cands:
            hit = next((r for (ts, _), r in ours.items() if ts == c["ts_ns"]
                        and abs(r["h_select"] - c["h_select"]) < 1e-6), None)
            if hit:
                n_match += 1
            else:
                near = next((r for (ts, _), r in ours.items()
                             if ts == c["ts_ns"]), None)
                n_miss += 1
                print(f"  MISS {root} ts={c['ts_ns']} h={c['h_select']:.1f}"
                      f" ours={'h=%.1f' % near['h_select'] if near else 'absent'}")
        cand_ts = {c["ts_ns"] for c in cands}
        for (ts, osi), r in ours.items():
            if ts not in cand_ts:
                n_extra += 1
                print(f"  EXTRA {root} ts={ts} osi={osi} h={r['h_select']:.1f}"
                      f" prem={r['premium']:.0f} neigh={r['neighbour_premium']:.0f}")
    print(f"PARITY {session}: corpus={len(corpus)} matched={n_match} "
          f"missed={n_miss} extra_kept={n_extra}")


def stage2() -> None:
    """Registered stage 2 over the stage-1 records: prior-20-session same-clock-
    minute profiles → d = |h_select| / expected_29s ≥ 3.0, news fence
    [T−5min, T], rank by d per band, per-underlying + top-N caps.

    Disclosed scaled caps (rate-preserving): the REGISTERED 3.2 base caps are
    STAGE2_TOP=300 and PER_UNDERLYING_CAP=10 over the corpus's 105 qualifying
    sessions; this backfill spans 357 sessions, so the caps scale pro-rata to
    1,020 and 34 — the same per-session selection rate as the registered base.
    (The wide run itself selected at DOUBLE that rate — ladder step 1, top-600 /
    cap-20 — so this scaling is also strictly tighter than the label set it
    grows.) Measured: neither cap materially binds (0 beyond-top, 6
    underlying-cap drops). Every other constant is the corpus's own import.
    """
    from datetime import timedelta

    import httpx

    from tools.hedge_footprint import _ET as HF_ET
    from tools.hedge_footprint import (
        D_MIN,
        NEWS_FENCE_S,
        alpaca_config,
        detectability,
        expected_29s_volume,
        fetch_minute_bars,
        fetch_news,
        minute_profile,
        save_json,
    )
    ADV_LOOKBACK = 20
    STAGE2_TOP_SCALED = 1020        # 300 × 357/105, rate-preserving
    PER_UNDERLYING_CAP_SCALED = 34  # 10 × 357/105, rate-preserving

    out_dir = OUT_DIR / "stage2"
    out_dir.mkdir(parents=True, exist_ok=True)
    recs = json.loads((OUT_DIR / "stage1_records_final.json").read_text())
    led = {"candidates": len(recs), "drop_no_bars": 0, "drop_zero_expected": 0,
           "drop_detectability": 0, "drop_news": 0, "drop_underlying_cap": 0,
           "drop_beyond_top": 0}
    cfg = alpaca_config()
    if not cfg["api_key_id"]:
        raise SystemExit("no Alpaca credentials")
    headers = {"APCA-API-KEY-ID": cfg["api_key_id"],
               "APCA-API-SECRET-KEY": cfg["api_secret_key"]}
    scored: list[dict] = []
    with httpx.Client(headers=headers, timeout=60.0) as client:
        for n, c in enumerate(recs):
            session = date.fromisoformat(c["session"])
            cache = out_dir / "bars" / f"{c['symbol']}_{session.isoformat()}.json"
            if cache.exists():
                profile = {int(k): v for k, v in json.loads(cache.read_text()).items()}
            else:
                bars = fetch_minute_bars(
                    client, cfg["data_base_url"], c["symbol"],
                    session - timedelta(days=40), session - timedelta(days=1))
                profile = minute_profile(bars)
                save_json(cache, {str(k): v for k, v in profile.items()})
            minute = datetime.fromtimestamp(c["ts_ns"] / 1e9, tz=HF_ET)
            vols = profile.get(minute.hour * 60 + minute.minute, [])[-ADV_LOOKBACK:]
            if not vols:
                led["drop_no_bars"] += 1
                continue
            expected = expected_29s_volume(np.array(vols, dtype=np.float64))
            d = detectability(c["abs_h_select"], expected)
            if not np.isfinite(d):
                led["drop_zero_expected"] += 1
                continue
            if d < D_MIN:
                led["drop_detectability"] += 1
                continue
            news_cache = out_dir / "news" / f"{c['symbol']}_{c['ts_ns']}.json"
            if news_cache.exists():
                stories = json.loads(news_cache.read_text())
            else:
                stories = fetch_news(client, cfg["data_base_url"], c["symbol"],
                                     c["ts_ns"] - NEWS_FENCE_S * 1_000_000_000,
                                     c["ts_ns"])
                save_json(news_cache, stories)
            if stories:
                led["drop_news"] += 1
                continue
            scored.append({**c, "expected_29s": expected, "d": d,
                           "minute_samples": len(vols)})
            if (n + 1) % 100 == 0:
                print(f"  {n + 1}/{len(recs)} scored={len(scored)}", flush=True)

    selected: list[dict] = []
    for band in ("primary", "secondary"):
        pool = sorted((s for s in scored if s["band"] == band), key=lambda s: -s["d"])
        per_name: dict[str, int] = {}
        picked: list[dict] = []
        for s in pool:
            if per_name.get(s["symbol"], 0) >= PER_UNDERLYING_CAP_SCALED:
                led["drop_underlying_cap"] += 1
                continue
            if len(picked) >= STAGE2_TOP_SCALED:
                led["drop_beyond_top"] += 1
                continue
            per_name[s["symbol"]] = per_name.get(s["symbol"], 0) + 1
            picked.append(s)
        selected.extend(picked)

    out = {"stage": "backfill-stage2", "ledger": led, "selected": selected,
           "n_primary": sum(1 for s in selected if s["band"] == "primary"),
           "n_secondary": sum(1 for s in selected if s["band"] == "secondary"),
           "caps": {"top": STAGE2_TOP_SCALED,
                    "per_underlying": PER_UNDERLYING_CAP_SCALED,
                    "scaling": "300,10 × 357/105 sessions, rate-preserving"}}
    save_json(out_dir / "stage2_selected.json", out)
    print(f"STAGE2 COMPLETE: {len(selected)} selected "
          f"({out['n_primary']} primary / {out['n_secondary']} secondary)")
    print(json.dumps(led))


def fetch() -> None:
    """Registered stage-3 fetch over the stage-2 events: [T−300s, T+300s]
    equity trades+quotes snapshots for each event AND its 20 controls,
    mirroring `hedge_footprint.stage_fetch` (imported `fetch_window` with its
    SIP feed assertions, imported `control_sessions` with the registered
    symmetric-20 rule). Snapshots cache under phaseb/fetch/snap/ — resumable.

    Disclosed adaptations:
    - control pool = the sessions table's contiguous crawl span
      (2024-02-15..2025-07-25, status done) with half-days excluded via the
      trading calendar (review fix: the first run's DISTINCT-prints pool
      admitted 4 half-days and 8 post-span parity/pilot strays) — NO
      cond-provenance requirement, because controls consume only the equity
      tape and the activity filter, never option condition codes;
    - the PRE-D contamination filter reads the crawl DB's ≥$25k groups — a
      SUPERSET of the registered single-leg filter set (packages included,
      the crawl cannot classify them): strictly conservative, it can only
      skip MORE control sessions, never admit a contaminated one.
    """
    import sqlite3

    import httpx

    from tools.hedge_footprint import (
        FeedAssertionError,
        MissingDailyBar,
        alpaca_config,
        control_sessions,
        fetch_window,
        save_json,
    )

    out_dir = OUT_DIR / "fetch"
    out_dir.mkdir(parents=True, exist_ok=True)
    events = json.loads(
        (OUT_DIR / "stage2" / "stage2_selected.json").read_text())["selected"]
    rc = load_classification(FENCE_PATH)
    con = sqlite3.connect(DATA_DIR / "backfill.db", check_same_thread=False)
    act_cache: dict[date, tuple[np.ndarray, np.ndarray]] = {}

    def _activity(sess: date):
        if sess not in act_cache:
            rows = con.execute(
                "SELECT root, ts_ns FROM prints WHERE session=?",
                (sess.isoformat(),)).fetchall()
            idents = {}
            syms, ts = [], []
            for root, t in rows:
                if root not in idents:
                    idents[root] = rc.resolve(root, sess.isoformat())
                syms.append(idents[root])
                ts.append(t)
            act_cache[sess] = (np.array(syms, dtype="U16"),
                               np.array(ts, dtype=np.int64))
            if len(act_cache) > 60:
                for k in list(act_cache)[:20]:
                    if k != sess:
                        del act_cache[k]
        return act_cache[sess]

    cfg = alpaca_config()
    if not cfg["api_key_id"]:
        raise SystemExit("no Alpaca credentials")
    headers = {"APCA-API-KEY-ID": cfg["api_key_id"],
               "APCA-API-SECRET-KEY": cfg["api_secret_key"]}
    # control pool = the crawl's contiguous span with HALF-DAYS EXCLUDED (the
    # corpus pool's own rule). Review fix: DISTINCT prints sessions admitted 4
    # half-days (29 empty controls) and 8 post-span parity/pilot strays.
    cal = httpx.get("https://paper-api.alpaca.markets/v2/calendar",
                    params={"start": "2024-02-15", "end": "2025-07-25"},
                    headers=headers, timeout=60)
    cal.raise_for_status()
    halfs = {row["date"] for row in cal.json() if row["close"] != "16:00"}
    pool = [date.fromisoformat(s) for (s,) in con.execute(
        "SELECT session FROM sessions WHERE status='done' AND session "
        "BETWEEN '2024-02-15' AND '2025-07-25' ORDER BY session")
        if s not in halfs]
    daily_path = out_dir / "daily_volume.json"
    daily = json.loads(daily_path.read_text()) if daily_path.exists() else {}
    ledger_path = out_dir / "fetch_ledger.jsonl"
    done = set()
    if ledger_path.exists():
        done = {(json.loads(x)["symbol"], json.loads(x)["ts_ns"])
                for x in ledger_path.read_text().splitlines() if x}
    led_f = open(ledger_path, "a")
    io_lock = threading.Lock()      # ledger + daily dict
    act_lock = threading.Lock()     # activity cache + sqlite (single conn)
    counter = [0]
    todo = [ev for ev in events if (ev["symbol"], ev["ts_ns"]) not in done]
    # shard by symbol: same-symbol events share one worker, so two events can
    # never race a np.savez to the same control-snapshot path
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for ev in todo:
        by_symbol[ev["symbol"]].append(ev)
    print(f"fetch: {len(todo)}/{len(events)} events across "
          f"{len(by_symbol)} symbols", flush=True)

    with httpx.Client(headers=headers, timeout=60.0) as client:
        def one_symbol(evs: list[dict]) -> None:
            for ev in evs:
                session = date.fromisoformat(ev["session"])
                with act_lock:
                    ctrls = control_sessions(session, ev["symbol"],
                                             ev["ts_ns"], pool, _activity)
                base = session_midnight_ns(session)
                row: dict
                # a failed window is DROPPED and counted, never admitted:
                # MissingDailyBar = pre-IPO/halt day; FeedAssertionError = the
                # registered missing-prints detector (the corpus's crash-and-
                # investigate default cannot govern 19k windows — the drop
                # ledger + the spec's 20% STOP rule replace it, disclosed)
                try:
                    fetch_window(out_dir, client, cfg["data_base_url"],
                                 ev["symbol"], ev["ts_ns"], daily)
                except (MissingDailyBar, FeedAssertionError) as exc:
                    row = {**ev, "control_sessions": [], "control_ts_ns": [],
                           "fetch_dropped": str(exc)}
                else:
                    kept_sessions, kept_ts = [], []
                    ctrl_feed_drops = 0
                    ctrl_bar_drops = 0
                    for c in ctrls:
                        ts = session_midnight_ns(c) + int(ev["ts_ns"] - base)
                        try:
                            fetch_window(out_dir, client, cfg["data_base_url"],
                                         ev["symbol"], ts, daily)
                        except MissingDailyBar:
                            # counted (review fix: was a bare continue) — an
                            # Alpaca daily-bar gap, not only pre-IPO/halt days
                            ctrl_bar_drops += 1
                            continue
                        except FeedAssertionError:
                            ctrl_feed_drops += 1
                            continue
                        kept_sessions.append(c.isoformat())
                        kept_ts.append(ts)
                    row = {**ev, "control_sessions": kept_sessions,
                           "control_ts_ns": kept_ts,
                           "control_feed_drops": ctrl_feed_drops,
                           "control_bar_drops": ctrl_bar_drops}
                with io_lock:
                    led_f.write(json.dumps(row) + "\n")
                    led_f.flush()
                    # dict() snapshots under the GIL — other workers mutate
                    # `daily` outside this lock, and serializing the live dict
                    # races ("dictionary changed size during iteration")
                    save_json(daily_path, dict(daily))
                    counter[0] += 1
                    if counter[0] % 10 == 0:
                        print(f"  fetched {counter[0]}/{len(todo)} events",
                              flush=True)

        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(one_symbol, by_symbol.values()))
    print(f"FETCH COMPLETE: {len(events)} events")


def freeze() -> None:
    """3.3 backfill label freeze — `hedge_scores_frozen_backfill.json` + SHA.

    Mirrors `tools/hedge_footprint_freeze.py` (build_rows + main) over the
    backfill fetch ledger: `measure_event` per event, the registered
    quiet/primary/64g cut, `hedge_aligned` scores, HIGH/NULL/ANTI strata, the
    same covariates, deterministic canonical bytes, mismatch-guarded write.
    NO forward price is read — outcomes stay sealed. Disclosed schema
    difference: rows carry `osi` (the backfill's contract identity, parsed from
    the raw OPRA symbol) where the corpus rows carry the defs `instrument_id`.
    """
    import hashlib
    from datetime import timedelta

    from db.earnings_calendar import earnings_dates_for
    from tools.hedge_footprint import (
        G_NS,
        PRIMARY_HORIZON_G,
        _arrays,
        build_condition_table,
        hedge_aligned,
        measure_event,
    )
    from tools.hedge_footprint_freeze import (
        EARNINGS_LOOKAHEAD_DAYS as EARN_45D,
    )
    from tools.hedge_footprint_freeze import (
        HIGH_THRESHOLD,
        _aligned_z,
        _mark_covariates,
        balance_table,
        canonical_bytes,
        otm_pct,
        stratum_for,
    )

    out_dir = OUT_DIR / "fetch"
    raw_rows = [json.loads(x)
                for x in (out_dir / "fetch_ledger.jsonl").read_text().splitlines()
                if x.strip()]
    # the jsonl ledger is append-only and resumable — a crashed-then-resumed
    # fetch can re-append an event; keep-LAST dedup (review fix: duplicates
    # would be measured twice and double-enter the canonical bytes)
    by_key = {(ev["symbol"], ev["ts_ns"]): ev for ev in raw_rows}
    events = list(by_key.values())
    if len(events) != len(raw_rows):
        print(f"ledger dedup: {len(raw_rows)} rows -> {len(events)} unique")
    table = build_condition_table()
    records = []
    silent_drops = []
    for i, ev in enumerate(events, 1):
        r = measure_event(out_dir, ev, table)
        if r is not None:
            records.append(r)
        else:
            # measure_event returns bare None when the snapshot is missing or
            # the event-time NBBO yields no usable mid (locked/crossed/zero —
            # the ALDX/MPLX class); record it (review fix: was silent)
            silent_drops.append({"symbol": ev["symbol"],
                                 "session": ev["session"],
                                 "ts_ns": ev["ts_ns"],
                                 "dropped": "no_snapshot_or_no_nbbo_mid"})
        if i % 100 == 0:
            print(f"  measured {i}/{len(events)}", flush=True)
    if silent_drops:
        print(f"silent measure_event drops (recorded): "
              f"{[(d['symbol'], d['session']) for d in silent_drops]}")
    usable = [r for r in records if not r.get("dropped")]
    dropped = [r for r in records if r.get("dropped")] + silent_drops
    cut = _arrays(usable, PRIMARY_HORIZON_G, tier="quiet", band="primary")
    print(f"measured {len(records)}, usable {len(usable)}, dropped {len(dropped)}, "
          f"cut (quiet/primary/{PRIMARY_HORIZON_G}g): {len(cut['records'])}",
          flush=True)

    rows = []
    for rec, score in zip(cut["records"],
                          hedge_aligned(cut["value"], cut["h_sign"]), strict=True):
        ev = by_key[(rec["symbol"], rec["ts_ns"])]
        session = date.fromisoformat(rec["session"])
        horizon_end = session + timedelta(days=EARN_45D)
        earnings = [d for d in earnings_dates_for(rec["symbol"])
                    if session.isoformat() < d <= horizon_end.isoformat()]
        spot = float(rec["spot_measure"])
        rows.append({
            "symbol": rec["symbol"],
            "session": rec["session"],
            "ts_ns": int(rec["ts_ns"]),
            "osi": str(ev["osi"]),
            "root": ev["root"],
            "strike": float(ev["strike"]),
            "is_put": bool(ev["is_put"]),
            "side_sign": int(ev["side_sign"]),
            "contracts": int(rec["contracts"]),
            "premium": float(rec["premium"]),
            "dte": int(ev["dte"]),
            "expiry_date": (date(1970, 1, 1)
                            + timedelta(days=int(ev["exp_days"]))).isoformat(),
            "h_sign": float(rec["h_sign"]),
            "abs_h": round(float(rec["abs_h"]), 2),
            "d": round(float(rec["d"]), 4),
            "delta_measure": round(float(rec["delta_measure"]), 6),
            "spot_measure": round(spot, 6),
            "otm_pct": round(otm_pct(float(ev["strike"]), spot,
                                     bool(ev["is_put"])), 6),
            "score": round(float(score), 6),
            "stratum": stratum_for(float(score)),
            "cluster": f"{rec['symbol']}|{rec['session']}",
            "score_trf_incl": round(_aligned_z(rec, PRIMARY_HORIZON_G,
                                               "trf_incl"), 6),
            "score_tick": round(_aligned_z(rec, PRIMARY_HORIZON_G, "tick"), 6),
            "score_16g": round(_aligned_z(rec, 16, "primary"), 6),
            "score_165g": round(_aligned_z(rec, 165, "primary"), 6),
            "n_controls": int(rec["n_controls"]),
            "earnings_dates_45d": earnings,
            **_mark_covariates(out_dir, rec["symbol"], int(rec["ts_ns"]), table,
                               ev["control_ts_ns"], float(rec["h_sign"])),
        })
    rows.sort(key=lambda r: (r["symbol"], r["ts_ns"]))

    doc = {
        "spec": ("FLOW_ROADMAP.md 3.3 backfill round 3 — phase-B label freeze "
                 "(2026-08-02); schema = hedge_scores_frozen_3p3 with `osi` "
                 "in place of `instrument_id`"),
        "high_threshold": HIGH_THRESHOLD,
        "horizon_g": PRIMARY_HORIZON_G,
        "grain_ns": G_NS,
        "n_events": len(rows),
        "events": rows,
    }
    payload = canonical_bytes(doc)
    sha = hashlib.sha256(payload).hexdigest()
    frozen_path = OUT_DIR / "hedge_scores_frozen_backfill.json"
    if frozen_path.exists():
        existing = frozen_path.read_bytes()
        if hashlib.sha256(existing).hexdigest() != sha:
            print(f"FROZEN FILE MISMATCH: on-disk != recomputed {sha[:16]} — "
                  "investigate, never overwrite")
            raise SystemExit(1)
        print(f"frozen file reproduced exactly: sha256={sha}")
    else:
        frozen_path.write_bytes(payload)
        print(f"frozen file WRITTEN: sha256={sha}")
    bal = balance_table(rows)
    (OUT_DIR / "hedge_balance_backfill.json").write_text(json.dumps(bal, indent=1))
    # sidecar (NOT in the canonical bytes — schema stays corpus-parallel):
    # every dropped event with its reason, silent-None drops included
    (OUT_DIR / "freeze_drops.json").write_text(json.dumps(
        [{"symbol": r["symbol"], "session": r["session"],
          "ts_ns": r["ts_ns"], "dropped": r["dropped"]} for r in dropped],
        indent=1))
    counts = {s: bal.get(s, {}).get("n_events", 0)
              for s in ("HIGH", "NULL", "ANTI")}
    print(f"strata: {counts}")
    print(f"SHA256 {sha}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run", action="store_true")
    p.add_argument("--parity", metavar="SESSION")
    p.add_argument("--stage2", action="store_true")
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--freeze", action="store_true")
    p.add_argument("--workers", type=int, default=12)
    args = p.parse_args()
    if args.parity:
        parity(args.parity)
    elif args.run:
        run(args.workers)
    elif args.stage2:
        stage2()
    elif args.fetch:
        fetch()
    elif args.freeze:
        freeze()
    else:
        p.print_help()


if __name__ == "__main__":
    main()
