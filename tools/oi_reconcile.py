"""Roadmap 2.3: reconcile tape volume against daily OI change (registered spec 2026-07-28).

Per consecutive-session pair (D, Dnext): tape volume V per contract from D's tcbbo
prints; ΔOI = oi[file Dnext] − oi[file D] (file D = D−1's CLOSE positions, so this
delta IS session D's net opening); f_open = (V + ΔOI) / (2V) clipped to [0,1].
Contracts join across days on OCC symbol via each day's definitions, never raw
instrument_id — id stability is MEASURED and reported, not assumed. Flags, never
silent drops: |ΔOI|>V (exercise/assignment/off-tape), expired-by-D (OI dies
without closing trades — excluded from f_open), absent from either day's defs,
V=0 with ΔOI≠0. Honest ceiling: ΔOI is a daily NET — f_open is an aggregate
estimate, never a per-print label.

Output per session: data/oi_reconcile/YYYY-MM-DD.npz (per-contract arrays) and a
rollup row set in data/oi_reconcile.db (underlying × right × dte_bucket) shaped
for 2.4. Resumable: sessions with existing npz are skipped; pairs missing either
OI file or the defs abstain loudly.

    python tools/oi_reconcile.py                  # all reconcilable pairs
    python tools/oi_reconcile.py --limit 3        # smoke: first 3 pairs
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import date
from pathlib import Path

import databento as db
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "databento"
OI_DIR = DATA / "opra_oi"
DEFS_DIR = DATA / "opra_defs"
TCBBO_DIR = DATA / "opra_tcbbo"
OUT_DIR = ROOT / "data" / "oi_reconcile"
OUT_DB = ROOT / "data" / "oi_reconcile.db"
CHUNK = 5_000_000
UNDEF_SIZE = np.uint32(np.iinfo(np.uint32).max)  # Databento UNDEF for uint32 size
# dte buckets match the flow rollup's edges so 2.4 can join lanes directly
DTE_EDGES = [0, 1, 5, 30, 90, 365, 10_000]
DTE_LABELS = ["0", "1-4", "5-29", "30-89", "90-364", "365+"]

logger = logging.getLogger("oi_reconcile")


def _sessions_with_oi() -> list[date]:
    """Trading sessions = non-empty OI npz files (holiday markers are empty)."""
    out = []
    for p in sorted(OI_DIR.glob("*.npz")):
        with np.load(p) as z:
            if len(z["iid"]):
                out.append(date.fromisoformat(p.stem))
    return out


def _defs_symbols(session: date) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(iid sorted, raw_symbol, underlying, days-to-expiry) from the day's definitions."""
    store = db.DBNStore.from_file(DEFS_DIR / f"{session.isoformat()}.dbn.zst")
    arr = store.to_ndarray()
    iid = arr["instrument_id"]
    sym = np.char.strip(arr["raw_symbol"].astype("U21"))
    und = np.char.strip(sym.astype("U6"))  # OPRA: root padded to 6, U6 cast truncates
    exp_days = (arr["expiration"] // 86_400_000_000_000).astype(np.int64)
    dte = exp_days - np.int64(session.toordinal() - date(1970, 1, 1).toordinal())
    order = np.argsort(iid, kind="stable")
    return iid[order], sym[order], und[order], dte[order]


def _tape_volume(session: date) -> tuple[np.ndarray, np.ndarray, int]:
    """(iid sorted, summed contract volume, dropped-sentinel count) from the
    session's tcbbo prints.

    ⚠️ tcbbo `size` is uint32 and carries Databento's UNDEF sentinel
    (4,294,967,295) on rows with no published size — ~0.16% of prints, measured
    2026-07-28. Summing them raw inflates a session's volume to ~1e14 (the first
    2.3 smoke did exactly that). Sentinel rows are DROPPED and counted, never
    coerced to zero: an unknown size is an abstention, not a zero-volume trade.
    """
    store = db.DBNStore.from_file(TCBBO_DIR / f"{session.isoformat()}.dbn.zst")
    parts: dict[int, int] = {}
    n_undef = 0
    for chunk in store.to_ndarray(count=CHUNK):
        good = chunk["size"] != UNDEF_SIZE
        n_undef += int((~good).sum())
        chunk = chunk[good]
        iid = chunk["instrument_id"]
        sz = chunk["size"].astype(np.int64)
        u, inv = np.unique(iid, return_inverse=True)
        v = np.bincount(inv, weights=sz).astype(np.int64)
        for k, n in zip(u.tolist(), v.tolist()):
            parts[k] = parts.get(k, 0) + n
    iids = np.fromiter(parts.keys(), np.int64, len(parts))
    vols = np.fromiter(parts.values(), np.int64, len(parts))
    order = np.argsort(iids, kind="stable")
    return iids[order], vols[order], n_undef


def _oi_by_symbol(session: date) -> tuple[dict[str, int], np.ndarray, np.ndarray]:
    """OI keyed by OCC symbol via the day's defs; also returns (iid, sym) for
    the id-stability measurement."""
    with np.load(OI_DIR / f"{session.isoformat()}.npz") as z:
        oi_iid, oi = z["iid"].astype(np.int64), z["oi"].astype(np.int64)
    d_iid, d_sym, _, _ = _defs_symbols(session)
    pos = np.searchsorted(d_iid, oi_iid)
    ok = (pos < len(d_iid)) & (d_iid[np.minimum(pos, len(d_iid) - 1)] == oi_iid)
    sym = d_sym[np.minimum(pos, len(d_iid) - 1)]
    return dict(zip(sym[ok].tolist(), oi[ok].tolist())), oi_iid[ok], sym[ok]


def _init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(OUT_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oi_reconcile_rollup (
            session TEXT NOT NULL, underlying TEXT NOT NULL,
            right TEXT NOT NULL, dte_bucket TEXT NOT NULL,
            contracts INTEGER, volume INTEGER, doi_net INTEGER,
            vol_weighted_f_open REAL,
            flag_doi_gt_v INTEGER, flag_expired INTEGER,
            PRIMARY KEY (session, underlying, right, dte_bucket))""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oi_reconcile_sessions (
            session TEXT PRIMARY KEY, contracts INTEGER, volume INTEGER,
            iid_stability REAL, f_open_vw REAL, flag_absent_defs INTEGER)""")
    return conn


def _process_pair(d: date, dnext: date, conn: sqlite3.Connection) -> None:
    out = OUT_DIR / f"{d.isoformat()}.npz"
    if out.exists():
        return
    oi_d, iid_d, sym_d = _oi_by_symbol(d)
    oi_n, iid_n, sym_n = _oi_by_symbol(dnext)
    # id-stability measurement (spec: measured, never assumed)
    shared, ia, ib = np.intersect1d(sym_d, sym_n, return_indices=True)
    stab = float((iid_d[ia] == iid_n[ib]).mean()) if len(shared) else 0.0

    v_iid, vols, n_undef = _tape_volume(d)
    d_iid, d_sym, d_und, d_dte = _defs_symbols(d)
    pos = np.searchsorted(d_iid, v_iid)
    ok = (pos < len(d_iid)) & (d_iid[np.minimum(pos, len(d_iid) - 1)] == v_iid)
    sym = d_sym[np.minimum(pos, len(d_iid) - 1)]
    und = d_und[np.minimum(pos, len(d_iid) - 1)]
    dte = d_dte[np.minimum(pos, len(d_iid) - 1)]

    n = int(ok.sum())
    sym, und, dte, vols = sym[ok], und[ok], dte[ok], vols[ok]
    flag_absent = int((~ok).sum())
    # A symbol missing from EITHER day's OI must abstain, never default to 0:
    # defaulting day D to 0 would book the contract's entire next-day OI as
    # "opening" and bias f_open upward (newly-listed strikes are exactly the
    # contracts this would hit).
    have_both = np.array([(s in oi_d) and (s in oi_n) for s in sym.tolist()])
    doi = np.array([oi_n.get(s, 0) - oi_d.get(s, 0) for s in sym.tolist()],
                   dtype=np.int64)
    doi[~have_both] = 0
    absent_next = ~have_both
    expired = dte <= 0
    gt_v = (~absent_next) & (np.abs(doi) > vols)
    clean = (~absent_next) & (~expired) & (vols > 0)
    f_open = np.full(n, np.nan)
    f_open[clean] = np.clip((vols[clean] + doi[clean]) / (2.0 * vols[clean]), 0.0, 1.0)
    right = np.array([s[12] if len(s) >= 13 else "?" for s in sym.tolist()])

    np.savez_compressed(out, sym=sym, und=und, volume=vols, doi=doi,
                        f_open=f_open, expired=expired, gt_v=gt_v)
    bucket = np.digitize(np.maximum(dte, 0), DTE_EDGES[1:], right=False)
    rows = {}
    for i in range(n):
        key = (und[i], right[i], DTE_LABELS[min(bucket[i], 5)])
        r = rows.setdefault(key, [0, 0, 0, 0.0, 0.0, 0, 0, 0])
        r[0] += 1
        r[1] += int(vols[i])
        if clean[i]:
            r[2] += int(doi[i])
            r[3] += float(f_open[i]) * int(vols[i])
            r[4] += int(vols[i])
        r[5] += int(gt_v[i])
        r[6] += int(expired[i])
    conn.executemany(
        """INSERT OR REPLACE INTO oi_reconcile_rollup VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [(d.isoformat(), k[0], k[1], k[2], r[0], r[1], r[2],
          (r[3] / r[4]) if r[4] else None, r[5], r[6])
         for k, r in rows.items()])
    vw = f_open[clean] * vols[clean]
    f_vw = float(vw.sum() / vols[clean].sum()) if clean.any() else None
    conn.execute("INSERT OR REPLACE INTO oi_reconcile_sessions VALUES (?,?,?,?,?,?)",
                 (d.isoformat(), n, int(vols.sum()), stab, f_vw, flag_absent))
    conn.commit()
    logger.info("%s: %s traded contracts, vol %s, iid-stability %.4f, "
                "vol-weighted f_open %.4f, clean %s/%s, flags gt_v %s expired %s "
                "no-oi-both-days %s undef-size-prints %s",
                d, f"{n:,}", f"{int(vols.sum()):,}", stab, f_vw if f_vw else -1,
                f"{int(clean.sum()):,}", f"{n:,}",
                f"{int(gt_v.sum()):,}", f"{int(expired.sum()):,}",
                f"{int((~have_both).sum()):,}", f"{n_undef:,}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = _init_db()
    sessions = _sessions_with_oi()
    pairs = [(a, b) for a, b in zip(sessions, sessions[1:])
             if (DEFS_DIR / f"{a.isoformat()}.dbn.zst").exists()
             and (DEFS_DIR / f"{b.isoformat()}.dbn.zst").exists()
             and (TCBBO_DIR / f"{a.isoformat()}.dbn.zst").exists()]
    if args.limit:
        pairs = pairs[: args.limit]
    logger.info("%d reconcilable pairs", len(pairs))
    for a, b in pairs:
        _process_pair(a, b, conn)
    logger.info("done")


if __name__ == "__main__":
    main()
