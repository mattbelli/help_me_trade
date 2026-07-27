"""Whole-market condition-label join for one session: captured Alpaca npz ⟂ tcbbo.

Addendum E's join, at full session scale, report-only. Consumes the raw capture
written by `tools/alpaca_condition_backfill.py` and the session's tcbbo/defs
files; emits the numbers the migration decision needs:

  - join rate both sides (Addendum E adopts a session's labels iff ≥98%)
  - the whole-market confusion matrix: registered ns-collision heuristic vs
    the true multi-leg/package code family (the 8-name probe, at scale)
  - condition census by prints and premium

Vectorized k-th-identical join: both tapes keyed (contract, price_cents, size),
k = occurrence index in time order within the key; a DBN print matches the
Alpaca trade with the same (key, k) when timestamps agree within ±100ms
(measured offset: exactly 0). No DB writes.

    python tools/condition_label_join.py --date 2026-07-24 \
        --data-dir ~/dev/help_me_trade/data/databento \
        --npz-dir ~/dev/help_me_trade/data/alpaca_conditions
"""

from __future__ import annotations

import argparse
import collections
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.flow_corpus_rollup import _multileg  # noqa: E402  (pure helper)

PX = 1e9
TOL_NS = 100_000_000  # ±100ms, Addendum E
PACKAGE_CODES = frozenset("fghijlmtu" + "knopqrs")  # Addendum E package family
MULTILEG_CODES = frozenset("fghijlmtu")


def _key_rank(cid: np.ndarray, cents: np.ndarray, size: np.ndarray,
              ts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(sort_order, k) — k is the time-ordered occurrence index within each
    (cid, cents, size) key, computed on the lexsorted arrays."""
    order = np.lexsort((ts, size, cents, cid))
    c, p, s = cid[order], cents[order], size[order]
    new = np.empty(len(order), dtype=bool)
    new[0] = True
    new[1:] = (c[1:] != c[:-1]) | (p[1:] != p[:-1]) | (s[1:] != s[:-1])
    idx = np.arange(len(order), dtype=np.int64)
    starts = np.maximum.accumulate(np.where(new, idx, 0))
    return order, idx - starts


def run(session: date, data_dir: Path, npz_dir: Path) -> None:
    import databento as dbn

    npz = np.load(npz_dir / f"{session.isoformat()}.npz")
    a_sym_names = npz["symbols"]
    a_t = npz["t_ns"].astype(np.int64)
    a_cents = npz["price_cents"].astype(np.int64)
    a_size = npz["size"].astype(np.int64)
    a_cond = npz["cond"]
    a_symi = npz["sym_idx"].astype(np.int64)

    darr = dbn.DBNStore.from_file(
        data_dir / "opra_tcbbo" / f"{session.isoformat()}.dbn.zst"
    ).to_ndarray()
    d_ts = darr["ts_event"].astype(np.int64)
    d_iid = darr["instrument_id"].astype(np.int64)
    d_cents = np.round(darr["price"].astype(np.float64) / PX * 100).astype(np.int64)
    d_size = darr["size"].astype(np.int64)
    del darr
    d_ml = _multileg(d_ts, d_iid)

    defs = dbn.DBNStore.from_file(
        data_dir / "opra_defs" / f"{session.isoformat()}.dbn.zst"
    ).to_ndarray()
    cls = defs["instrument_class"]
    defs = defs[(cls == b"C") | (cls == b"P")]
    def_ids = defs["instrument_id"].astype(np.int64)
    def_syms = np.char.replace(defs["raw_symbol"].astype("U24"), " ", "")
    del defs

    # one shared contract-id space: index into def_syms
    order = np.argsort(def_ids, kind="stable")
    def_ids, def_syms = def_ids[order], def_syms[order]
    # dbn side: instrument_id -> contract idx
    pos = np.clip(np.searchsorted(def_ids, d_iid), 0, len(def_ids) - 1)
    d_ok = def_ids[pos] == d_iid
    d_cid = np.where(d_ok, pos, -1)
    # alpaca side: symbol -> contract idx
    sym_order = np.argsort(def_syms, kind="stable")
    sorted_syms = def_syms[sym_order]
    spos = np.clip(np.searchsorted(sorted_syms, a_sym_names), 0, len(sorted_syms) - 1)
    a_sym_cid = np.where(sorted_syms[spos] == a_sym_names, sym_order[spos], -1)
    a_cid = a_sym_cid[a_symi]

    d_keep = d_cid >= 0
    a_keep = a_cid >= 0
    n_d, n_a = int(d_keep.sum()), int(a_keep.sum())
    print(f"=== {session} whole-market label join ===")
    print(f"  dbn prints (C/P-resolved): {n_d:,} of {len(d_ts):,}   "
          f"alpaca trades (symbol-resolved): {n_a:,} of {len(a_t):,}")

    do, dk = _key_rank(d_cid[d_keep], d_cents[d_keep], d_size[d_keep], d_ts[d_keep])
    ao, ak = _key_rank(a_cid[a_keep], a_cents[a_keep], a_size[a_keep], a_t[a_keep])

    def packed(cid, cents, size, k) -> np.ndarray:
        rec = np.empty(len(cid), dtype=[("c", np.int64), ("p", np.int64),
                                        ("s", np.int64), ("k", np.int64)])
        rec["c"], rec["p"], rec["s"], rec["k"] = cid, cents, size, k
        return rec

    d_idx = np.flatnonzero(d_keep)[do]
    a_idx = np.flatnonzero(a_keep)[ao]
    d_rec = packed(d_cid[d_idx], d_cents[d_idx], d_size[d_idx], dk)
    a_rec = packed(a_cid[a_idx], a_cents[a_idx], a_size[a_idx], ak)

    # both rec arrays are already lexsorted by construction; walk the merge
    ai = np.searchsorted(a_rec, d_rec)
    ai = np.clip(ai, 0, len(a_rec) - 1)
    hit = a_rec[ai] == d_rec
    within = np.abs(a_t[a_idx[ai]] - d_ts[d_idx]) <= TOL_NS
    m = hit & within
    md_idx = d_idx[m]  # dbn row of each matched print
    ma_idx = a_idx[ai[m]]  # alpaca row it matched
    n_m = int(m.sum())
    print(f"  matched: {n_m:,} = {n_m / n_d:.2%} of dbn, {n_m / n_a:.2%} of alpaca"
          f"   (Addendum E floor: 98%)")

    conds = a_cond[ma_idx]
    ml = d_ml[md_idx]
    in_pkg = np.isin(conds, list(PACKAGE_CODES))
    in_ml = np.isin(conds, list(MULTILEG_CODES))
    for label, truth in (("package family (Addendum E)", in_pkg),
                         ("multi-leg only", in_ml)):
        tp = int((ml & truth).sum())
        fp = int((ml & ~truth).sum())
        fn = int((~ml & truth).sum())
        prec = tp / (tp + fp) if tp + fp else float("nan")
        rec_ = tp / (tp + fn) if tp + fn else float("nan")
        print(f"  heuristic vs {label:<28} precision {prec:.2%}  recall {rec_:.2%}"
              f"   (TP {tp:,} FP {fp:,} FN {fn:,})")

    prem = d_cents[md_idx].astype(np.float64) / 100.0 * d_size[md_idx] * 100.0
    tot_p = prem.sum() or 1.0
    census = collections.Counter()
    prem_by = collections.Counter()
    for c in np.unique(conds):
        sel = conds == c
        census[str(c)] = int(sel.sum())
        prem_by[str(c)] = float(prem[sel].sum())
    print(f"  census (matched prints, premium ${tot_p / 1e9:.2f}B):")
    for c, n in census.most_common(16):
        fam = ("PKG" if c in PACKAGE_CODES else "")
        print(f"    c={c!r:6} {n:>10,} ({n / n_m:6.2%})  "
              f"${prem_by[c] / 1e6:>9.1f}M ({prem_by[c] / tot_p:6.2%}) {fam}")
    pkg_prem = sum(v for k, v in prem_by.items() if k in PACKAGE_CODES)
    print(f"  package-family premium share (true codes): {pkg_prem / tot_p:.2%}")
    hml_prem = float(prem[ml].sum())
    print(f"  heuristic-flag premium share (matched):    {hml_prem / tot_p:.2%}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="2026-07-24")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--npz-dir", required=True)
    a = p.parse_args()
    run(date.fromisoformat(a.date), Path(a.data_dir).expanduser(),
        Path(a.npz_dir).expanduser())
