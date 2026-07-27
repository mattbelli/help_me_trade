"""Is the multi-leg sign flip a whole-corpus problem, or an index/ETF artifact?

Roadmap measurement 1.8(b). Addendum D registered that excluding multi-leg prints
FLIPS the session sign of all three directional measures — a genuine alarm, but
measured on the pooled tape. This splits the arm three ways: everything, single
names only, and index roots + ETFs.

Re-run 2026-07-27 with roadmap 1.2's curated INDEX_ROOTS in place of the old
"no equity bar => index" test (2025-07-28, IN-SAMPLE, one session):
    ALL            13.0% of prints / 33.3% of premium multi-leg -> flips 3/3
    SINGLE NAMES   10.9% / 19.5%                                -> flips 0/3
    INDEX + ETFs   16.5% / 48.5%                                -> flips 3/3
The conclusion is unchanged from the first run; only the handful of bar-less
equities (BRKB, FI, PARA) moved arms, worth ~$35M of $26.31B.
i.e. the contamination that flips the sign lives in index/ETF spread structures
(SPX condors, ETF collars), not in the single-name flow the 3-45d swing thesis
targets. Also: single names were net SHORT while index/ETF was net LONG on the
same session, so pooling them conflates two opposite signals.

⚠️ One session, in-sample. Re-run across the corpus before this is load-bearing.
⚠️ ETF_SET below is hand-built and incomplete; it covers the high-option-volume
names, so treat "single names" as "single names plus a tail of small ETFs".

    python tools/multileg_arm_probe.py --date 2025-07-28
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import databento as db
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.flow_corpus import INDEX_ROOTS  # noqa: E402
from tools.flow_corpus_rollup import (  # noqa: E402
    TCBBO_DIR,
    _load_definitions,
    _multileg,
)

UNDEF = 9223372036854775807
PX = 1e9

ETF_SET = {
    "SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "HYG", "EEM", "EFA",
    "XLF", "XLE", "XLK", "XLU", "XLV", "XLI", "XLP", "XLY", "XLB", "XLRE",
    "XLC", "SMH", "SOXL", "SOXS", "TQQQ", "SQQQ", "UVXY", "VXX", "SVXY",
    "ARKK", "FXI", "KWEB", "EWZ", "TNA", "TZA", "LABU", "JETS", "XBI",
    "IBIT", "GDX", "USO", "UNG", "VOO", "IVV", "SPXL", "SPXS", "UPRO",
    "SDS", "QLD", "PSQ",
}


def probe(session: date) -> None:
    tpath = TCBBO_DIR / f"{session.isoformat()}.dbn.zst"
    if not tpath.exists():
        raise SystemExit(f"no tcbbo file for {session}")
    defs = _load_definitions(session)
    if defs is None:
        raise SystemExit(f"no definitions for {session}")
    d_ids, d_und, uniq_und, *_ = defs

    arr = db.DBNStore.from_file(tpath).to_ndarray()
    pid = arr["instrument_id"].astype(np.int64)
    idx = np.clip(np.searchsorted(d_ids, pid), 0, len(d_ids) - 1)
    hit = d_ids[idx] == pid
    arr, und = arr[hit], d_und[idx[hit]]

    names = np.array([str(x) for x in uniq_und])
    # Was "no equity bar => index root", which is the exact conflation roadmap 1.2
    # removed: BRKB (ticker format), FI (renamed) and PARA (acquired) have no bar
    # and are ordinary equities. Classify off the curated root list instead.
    single = np.array(
        [n not in INDEX_ROOTS and n not in ETF_SET for n in names]
    )[und]

    ml = _multileg(arr["ts_event"].astype(np.int64),
                   arr["instrument_id"].astype(np.int64))
    bid = arr["bid_px_00"].astype(np.float64)
    ask = arr["ask_px_00"].astype(np.float64)
    price = arr["price"].astype(np.float64)
    good = (arr["bid_px_00"] != UNDEF) & (arr["ask_px_00"] != UNDEF) \
        & (bid > 0) & (ask > 0) & (ask > bid)
    mid, half = (bid + ask) / 2.0, (ask - bid) / 2.0
    with np.errstate(invalid="ignore", divide="ignore"):
        u = (price - mid) / half
    u = np.nan_to_num(np.where(good, u, np.nan), nan=0.0, posinf=0.0, neginf=0.0)
    prem = price / PX * arr["size"].astype(np.float64) * 100.0
    sgn = np.where(good & (price > mid), 1.0,
                   np.where(good & (price < mid), -1.0, 0.0))

    measures = (
        ("net", lambda m: (sgn[m] * prem[m]).sum()),
        ("clip", lambda m: (np.clip(u[m], -1, 1) * prem[m]).sum()),
        ("raw", lambda m: (u[m] * prem[m]).sum()),
    )

    def report(label: str, mask: np.ndarray) -> int:
        clean = mask & ~ml
        print(f"\n{label:<22} premium ${prem[mask].sum() / 1e9:>6.2f}B   "
              f"multileg {ml[mask].mean():>5.1%} of prints, "
              f"{prem[mask & ml].sum() / prem[mask].sum():>5.1%} of premium")
        flips = 0
        for nm, fn in measures:
            a, c = fn(mask), fn(clean)
            f = (a > 0) != (c > 0)
            flips += f
            print(f"     {nm:>4}: all {a / 1e6:>+9.1f}M   ex-multileg "
                  f"{c / 1e6:>+9.1f}M   {'*** FLIPS ***' if f else 'same sign'}")
        return flips

    print(f"=== {session} — post-join, {len(arr):,} prints ===")
    report("ALL (as Addendum D)", np.ones(len(arr), dtype=bool))
    flips = report("SINGLE NAMES only", single)
    report("index roots + ETFs", ~single)
    print(f"\nSingle-name arm flips {flips}/3 measures. "
          f"{'Contamination is NOT single-name.' if flips == 0 else 'Contamination reaches single names.'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="2025-07-28")
    probe(date.fromisoformat(p.parse_args().date))
