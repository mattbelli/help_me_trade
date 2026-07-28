"""The canonical Addendum E condition-label join: Alpaca capture → tcbbo prints.

Addendum E froze the join mechanics — per contract, per identical
(price_cents, size) key, k-th occurrence on one tape matches the k-th on the
other, accepted when |Δt| ≤ 100ms (measured participant-timestamp offset:
exactly 0ns, both vendors passing through the same OPRA stamp). Frozen means
ONE implementation: the report tool (`tools/condition_label_join.py`) and the
corpus rollup (`tools/flow_corpus_rollup.py`) both call this module, so the
join that gets floor-checked is the join that labels the corpus.

Changing anything here is a new addendum, not a tuning knob.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np

TOL_NS = 100_000_000  # ±100ms, Addendum E
# The package family (Addendum E): every multi-leg option package
# (MLET/MLAT/MLCT/MLFT/MESL/MASL/MFSL/CBMO/MCTP) AND every stock-option combo
# (TLAT/TLET/TLCT/TLFT/TESL/TASL/TFSL). A leg of either is priced by the firm
# allocating the package, not by its own NBBO — equally true when one leg is
# stock, which is why the tied-to-stock codes are in.
MULTILEG_CODES = frozenset("fghijlmtu")
TIED_CODES = frozenset("knopqrs")
PACKAGE_CODES = MULTILEG_CODES | TIED_CODES
# The registered per-session adoption floor: below this, the whole session
# stays on the ns-collision heuristic (mixed provenance is worse than an
# honestly-heuristic session).
JOIN_FLOOR = 0.98


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


def _packed(cid: np.ndarray, cents: np.ndarray, size: np.ndarray,
            k: np.ndarray) -> np.ndarray:
    rec = np.empty(len(cid), dtype=[("c", np.int64), ("p", np.int64),
                                    ("s", np.int64), ("k", np.int64)])
    rec["c"], rec["p"], rec["s"], rec["k"] = cid, cents, size, k
    return rec


def match_prints(
    d_cid: np.ndarray, d_cents: np.ndarray, d_size: np.ndarray, d_ts: np.ndarray,
    a_cid: np.ndarray, a_cents: np.ndarray, a_size: np.ndarray, a_t: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """The frozen join core, IO-free: rows with cid < 0 are unresolvable and
    never match. Returns (d_rows, a_rows) — parallel index arrays into the
    inputs, one pair per matched print."""
    d_keep = d_cid >= 0
    a_keep = a_cid >= 0
    if not d_keep.any() or not a_keep.any():
        empty = np.empty(0, dtype=np.int64)
        return empty, empty
    do, dk = _key_rank(d_cid[d_keep], d_cents[d_keep], d_size[d_keep],
                       d_ts[d_keep])
    ao, ak = _key_rank(a_cid[a_keep], a_cents[a_keep], a_size[a_keep],
                       a_t[a_keep])
    d_idx = np.flatnonzero(d_keep)[do]
    a_idx = np.flatnonzero(a_keep)[ao]
    d_rec = _packed(d_cid[d_idx], d_cents[d_idx], d_size[d_idx], dk)
    a_rec = _packed(a_cid[a_idx], a_cents[a_idx], a_size[a_idx], ak)

    # (key, k) is unique per tape by construction, so this is an exact 1:1 merge
    ai = np.clip(np.searchsorted(a_rec, d_rec), 0, len(a_rec) - 1)
    m = (a_rec[ai] == d_rec) & (np.abs(a_t[a_idx[ai]] - d_ts[d_idx]) <= TOL_NS)
    return d_idx[m], a_idx[ai[m]]


def capture_complete(session: date, npz_dir: Path) -> bool:
    """The backfill writes the npz first and its stats JSON last, so the JSON is
    the only completion marker — an npz alone may be a torn write."""
    return (npz_dir / f"{session.isoformat()}.json").exists() and \
        (npz_dir / f"{session.isoformat()}.npz").exists()


def join_condition_codes(
    session: date,
    d_ts: np.ndarray,
    d_iid: np.ndarray,
    d_cents: np.ndarray,
    d_size: np.ndarray,
    *,
    data_dir: Path,
    npz_dir: Path,
) -> tuple[np.ndarray, dict] | None:
    """Label each tcbbo print with its Alpaca condition code.

    Inputs are the print arrays in file order (any subset, e.g. post-defs-join —
    k counts occurrences per (contract, price, size) key, so dropping ALL prints
    of an unresolvable contract never disturbs another contract's k). Returns
    (cond, stats): ``cond`` is 'U1' aligned to the input order, '' where no
    trade joined; ``stats`` carries the counts the Addendum E floor check needs.
    None when the session has no completed capture.
    """
    import databento as dbn

    if not capture_complete(session, npz_dir):
        return None
    npz = np.load(npz_dir / f"{session.isoformat()}.npz")
    a_sym_names = npz["symbols"].astype("U24")
    a_t = npz["t_ns"].astype(np.int64)
    a_cents = npz["price_cents"].astype(np.int64)
    a_size = npz["size"].astype(np.int64)
    a_cond = npz["cond"].astype("U1")
    a_symi = npz["sym_idx"].astype(np.int64)

    defs = dbn.DBNStore.from_file(
        data_dir / "opra_defs" / f"{session.isoformat()}.dbn.zst"
    ).to_ndarray()
    cls = defs["instrument_class"]
    defs = defs[(cls == b"C") | (cls == b"P")]
    def_ids = defs["instrument_id"].astype(np.int64)
    def_syms = np.char.replace(defs["raw_symbol"].astype("U24"), " ", "")
    del defs

    # one shared contract-id space: index into the sorted-by-id defs
    order = np.argsort(def_ids, kind="stable")
    def_ids, def_syms = def_ids[order], def_syms[order]
    pos = np.clip(np.searchsorted(def_ids, d_iid), 0, len(def_ids) - 1)
    d_cid = np.where(def_ids[pos] == d_iid, pos, -1)
    sym_order = np.argsort(def_syms, kind="stable")
    sorted_syms = def_syms[sym_order]
    spos = np.clip(np.searchsorted(sorted_syms, a_sym_names), 0,
                   len(sorted_syms) - 1)
    a_sym_cid = np.where(sorted_syms[spos] == a_sym_names, sym_order[spos], -1)
    a_cid = a_sym_cid[a_symi]

    n_d = int((d_cid >= 0).sum())
    n_a = int((a_cid >= 0).sum())
    cond = np.full(len(d_ts), "", dtype="U1")
    if not n_d or not n_a:
        return cond, {"trades_alpaca": len(a_t), "dbn_resolved": n_d,
                      "alpaca_resolved": n_a, "matched": 0,
                      "join_rate_dbn": 0.0, "join_rate_alpaca": 0.0}

    d_rows, a_rows = match_prints(d_cid, d_cents, d_size, d_ts,
                                  a_cid, a_cents, a_size, a_t)
    cond[d_rows] = a_cond[a_rows]
    n_m = len(d_rows)
    return cond, {
        "trades_alpaca": len(a_t),
        "dbn_resolved": n_d,
        "alpaca_resolved": n_a,
        "matched": n_m,
        # The floor metric: share of contract-resolvable prints that joined.
        # The probe and the 07-24 whole-market run were both measured on this
        # denominator, so the registered 98% floor is comparable to it.
        "join_rate_dbn": n_m / n_d,
        "join_rate_alpaca": n_m / n_a,
    }


def read_capture_stats(session: date, npz_dir: Path) -> dict | None:
    path = npz_dir / f"{session.isoformat()}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())
