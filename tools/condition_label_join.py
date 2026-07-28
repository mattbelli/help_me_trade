"""Whole-market condition-label join: captured Alpaca npz ⟂ tcbbo. Report-only.

Two modes, both consuming the ONE frozen join in `tools/condition_labels.py`
(Addendum E — the join that gets floor-checked here is the join that will label
the corpus at schema_version 6):

  * single session (default): the full report — join rates vs the 98% floor,
    the heuristic-vs-code confusion matrix, condition census by prints and
    premium, package-family premium share.
  * ``--sweep``: every session with a completed capture, terse one-liners, and
    a per-session ledger written to ``<npz-dir>/join_ledger.json``. This ledger
    IS the Addendum E migration gate: the v6 re-roll may start only when every
    corpus session is present here at ``floor_pass: true``. Resumable —
    sessions already in the ledger are skipped; the ledger is rewritten
    atomically after each session.

    python tools/condition_label_join.py --date 2026-07-24 \
        --data-dir ~/dev/help_me_trade/data/databento \
        --npz-dir ~/dev/help_me_trade/data/alpaca_conditions
    python tools/condition_label_join.py --sweep --data-dir ... --npz-dir ...
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.condition_labels import (  # noqa: E402
    JOIN_FLOOR,
    MULTILEG_CODES,
    PACKAGE_CODES,
    capture_complete,
    join_condition_codes,
)
from tools.flow_corpus_rollup import _multileg  # noqa: E402  (pure helper)

PX = 1e9


def _load_session(session: date, data_dir: Path):
    import databento as dbn

    arr = dbn.DBNStore.from_file(
        data_dir / "opra_tcbbo" / f"{session.isoformat()}.dbn.zst"
    ).to_ndarray()
    d_ts = arr["ts_event"].astype(np.int64)
    d_iid = arr["instrument_id"].astype(np.int64)
    d_cents = np.round(arr["price"].astype(np.float64) / PX * 100).astype(np.int64)
    d_size = arr["size"].astype(np.int64)
    return d_ts, d_iid, d_cents, d_size


def _confusion(ml: np.ndarray, conds: np.ndarray, truth_codes) -> tuple[float, float]:
    truth = np.isin(conds, list(truth_codes))
    tp = int((ml & truth).sum())
    fp = int((ml & ~truth).sum())
    fn = int((~ml & truth).sum())
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    return prec, rec


def run(session: date, data_dir: Path, npz_dir: Path) -> None:
    d_ts, d_iid, d_cents, d_size = _load_session(session, data_dir)
    res = join_condition_codes(session, d_ts, d_iid, d_cents, d_size,
                               data_dir=data_dir, npz_dir=npz_dir)
    if res is None:
        print(f"{session}: no completed capture in {npz_dir}")
        return
    cond, st = res
    d_ml = _multileg(d_ts, d_iid)

    print(f"=== {session} whole-market label join ===")
    print(f"  dbn prints (C/P-resolved): {st['dbn_resolved']:,} of {len(d_ts):,}   "
          f"alpaca trades (symbol-resolved): {st['alpaca_resolved']:,} "
          f"of {st['trades_alpaca']:,}")
    print(f"  matched: {st['matched']:,} = {st['join_rate_dbn']:.2%} of dbn, "
          f"{st['join_rate_alpaca']:.2%} of alpaca   "
          f"(Addendum E floor: {JOIN_FLOOR:.0%})")

    matched = cond != ""
    conds = cond[matched]
    ml = d_ml[matched]
    for label, codes in (("package family (Addendum E)", PACKAGE_CODES),
                         ("multi-leg only", MULTILEG_CODES)):
        prec, rec_ = _confusion(ml, conds, codes)
        print(f"  heuristic vs {label:<28} precision {prec:.2%}  recall {rec_:.2%}")

    prem = d_cents[matched].astype(np.float64) / 100.0 * d_size[matched] * 100.0
    tot_p = prem.sum() or 1.0
    census = collections.Counter()
    prem_by = collections.Counter()
    for c in np.unique(conds):
        sel = conds == c
        census[str(c)] = int(sel.sum())
        prem_by[str(c)] = float(prem[sel].sum())
    n_m = st["matched"]
    print(f"  census (matched prints, premium ${tot_p / 1e9:.2f}B):")
    for c, n in census.most_common(16):
        fam = ("PKG" if c in PACKAGE_CODES else "")
        print(f"    c={c!r:6} {n:>10,} ({n / n_m:6.2%})  "
              f"${prem_by[c] / 1e6:>9.1f}M ({prem_by[c] / tot_p:6.2%}) {fam}")
    pkg_prem = sum(v for k, v in prem_by.items() if k in PACKAGE_CODES)
    print(f"  package-family premium share (true codes): {pkg_prem / tot_p:.2%}")
    print(f"  heuristic-flag premium share (matched):    {float(prem[ml].sum()) / tot_p:.2%}")


def sweep(data_dir: Path, npz_dir: Path) -> None:
    ledger_path = npz_dir / "join_ledger.json"
    ledger: dict = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    sessions = sorted(
        date.fromisoformat(p.stem)
        for p in npz_dir.glob("????-??-??.json")
    )
    todo = [s for s in sessions if s.isoformat() not in ledger
            and capture_complete(s, npz_dir)
            and (data_dir / "opra_tcbbo" / f"{s.isoformat()}.dbn.zst").exists()]
    print(f"sweep: {len(sessions)} captured, {len(ledger)} in ledger, "
          f"{len(todo)} to check")
    for s in todo:
        d_ts, d_iid, d_cents, d_size = _load_session(s, data_dir)
        res = join_condition_codes(s, d_ts, d_iid, d_cents, d_size,
                                   data_dir=data_dir, npz_dir=npz_dir)
        if res is None:
            continue
        cond, st = res
        matched = cond != ""
        prec, rec_ = _confusion(_multileg(d_ts, d_iid)[matched], cond[matched],
                                PACKAGE_CODES)
        prem = d_cents[matched].astype(np.float64) / 100.0 * d_size[matched] * 100.0
        pkg = float(prem[np.isin(cond[matched], list(PACKAGE_CODES))].sum())
        entry = {
            "matched": st["matched"],
            "join_rate_dbn": round(st["join_rate_dbn"], 6),
            "join_rate_alpaca": round(st["join_rate_alpaca"], 6),
            "floor_pass": st["join_rate_dbn"] >= JOIN_FLOOR,
            "pkg_premium_share": round(pkg / (float(prem.sum()) or 1.0), 6),
            "heuristic_precision": round(prec, 6),
            "heuristic_recall": round(rec_, 6),
        }
        ledger[s.isoformat()] = entry
        tmp = ledger_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(ledger, indent=1, sort_keys=True))
        tmp.rename(ledger_path)
        flag = "PASS" if entry["floor_pass"] else "** BELOW FLOOR **"
        print(f"  {s}  join {entry['join_rate_dbn']:.2%}/{entry['join_rate_alpaca']:.2%}"
              f"  pkg$ {entry['pkg_premium_share']:6.2%}"
              f"  heur P/R {prec:.1%}/{rec_:.1%}  {flag}")
    fails = sorted(d for d, e in ledger.items() if not e["floor_pass"])
    print(f"ledger: {len(ledger)} sessions, "
          f"{len(ledger) - len(fails)} pass, {len(fails)} below floor"
          + (f" -> {fails}" if fails else ""))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="2026-07-24")
    p.add_argument("--sweep", action="store_true",
                   help="floor-check every completed capture; write join_ledger.json")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--npz-dir", required=True)
    a = p.parse_args()
    if a.sweep:
        sweep(Path(a.data_dir).expanduser(), Path(a.npz_dir).expanduser())
    else:
        run(date.fromisoformat(a.date), Path(a.data_dir).expanduser(),
            Path(a.npz_dir).expanduser())
