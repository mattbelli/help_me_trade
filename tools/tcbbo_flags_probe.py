"""Does OPRA tcbbo carry a condition code that marks complex (multi-leg) trades?

Addendum D turns on this. The multi-leg contaminant — a spread leg priced by the
firm allocating it, which need not sit inside that leg's own NBBO — is currently
detected by a *heuristic*: the print shares a `ts_event` with a print on a
different instrument. If DBN's `flags` byte carries the OPRA condition directly,
the flag becomes ground truth and the heuristic can be retired.

Measures, on one session:
  A  the flags values that actually occur, by share of prints
  B  flags x multi-leg-signature  — does any bit track it?
  C  flags x |u|>2                — does any bit track the contaminated tail?

Chunked at 2M records: serv holds the live OPRA socket and the backfill, so this
must not be the process that OOMs the box.

  python tools/tcbbo_flags_probe.py --date 2025-07-28
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from pathlib import Path

import databento as db
import numpy as np

UNDEF = 9223372036854775807
ROOT = Path(__file__).resolve().parent.parent
TCBBO_DIR = ROOT / "data" / "databento" / "opra_tcbbo"
CHUNK = 2_000_000


def _multileg(ts_event: np.ndarray, iid: np.ndarray) -> np.ndarray:
    """True where this print shares its ts_event with a print on a DIFFERENT
    instrument. Sorting by (ts_event, instrument_id) makes a group multi-leg iff
    its first and last instrument_id differ."""
    order = np.lexsort((iid, ts_event))
    ts_s, iid_s = ts_event[order], iid[order]
    # group boundaries on ts_event
    starts = np.flatnonzero(np.r_[True, ts_s[1:] != ts_s[:-1]])
    ends = np.r_[starts[1:], len(ts_s)] - 1
    mixed = iid_s[starts] != iid_s[ends]          # differing ids inside the group
    out_s = np.repeat(mixed, ends - starts + 1)
    out = np.empty(len(ts_event), dtype=bool)
    out[order] = out_s
    return out


def probe(session: date) -> None:
    path = TCBBO_DIR / f"{session.isoformat()}.dbn.zst"
    if not path.exists():
        raise SystemExit(f"no tcbbo file for {session}")

    flag_counts: Counter[int] = Counter()
    ml_by_flag: Counter[int] = Counter()
    tail_by_flag: Counter[int] = Counter()
    total = n_ml = n_tail = 0

    store = db.DBNStore.from_file(path)
    for arr in store.to_ndarray(count=CHUNK):
        flags = arr["flags"].astype(np.int64)
        iid = arr["instrument_id"].astype(np.int64)
        tse = arr["ts_event"].astype(np.int64)

        bid = arr["bid_px_00"].astype(np.float64)
        ask = arr["ask_px_00"].astype(np.float64)
        price = arr["price"].astype(np.float64)
        good = (arr["bid_px_00"] != UNDEF) & (arr["ask_px_00"] != UNDEF) \
            & (bid > 0) & (ask > 0) & (ask > bid)
        with np.errstate(invalid="ignore", divide="ignore"):
            u = (price - (bid + ask) / 2.0) / ((ask - bid) / 2.0)
        u = np.where(good, u, 0.0)
        tail = np.abs(u) > 2.0

        ml = _multileg(tse, iid)

        total += len(arr)
        n_ml += int(ml.sum())
        n_tail += int(tail.sum())
        for f in np.unique(flags):
            m = flags == f
            flag_counts[int(f)] += int(m.sum())
            ml_by_flag[int(f)] += int((m & ml).sum())
            tail_by_flag[int(f)] += int((m & tail).sum())

    print(f"\n{session}  {total:,} prints")
    print(f"  multi-leg signature : {n_ml:,}  ({n_ml/total:.2%})")
    print(f"  |u| > 2             : {n_tail:,}  ({n_tail/total:.2%})\n")
    print(f"{'flags':>8} {'prints':>14} {'share':>8} "
          f"{'P(multileg|f)':>14} {'P(|u|>2|f)':>12}")
    for f, n in flag_counts.most_common():
        print(f"{f:>8} {n:>14,} {n/total:>7.2%} "
              f"{ml_by_flag[f]/n:>13.2%} {tail_by_flag[f]/n:>11.2%}")
    print(
        "\nA flag is ground truth for multi-leg only if P(multileg|f) is ~1 for "
        "some f and ~0 for the rest. Anything else and the ts_event heuristic "
        "stands, and Addendum D must register it as a heuristic."
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="2025-07-28")
    a = p.parse_args()
    probe(date.fromisoformat(a.date))
