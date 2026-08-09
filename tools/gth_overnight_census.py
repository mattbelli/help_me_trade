#!/usr/bin/env python
"""gth_overnight_census.py — Phase A census of overnight (Cboe GTH) index-option
activity from the Alpaca condition-capture archive (`data/alpaca_conditions/*.npz`).

Context (memory `databento-opra-no-gth`, BUILD_LOG 2026-08-09): Databento OPRA
carries no GTH session, so the npz archive — full-day Alpaca REST capture — is
the ONLY overnight history we hold. Its window starts 08:00 UTC, so the slice
censused here is 04:00–09:15 ET (Europe morning + US premarket, incl. the
08:30 ET macro-release window); the 20:15 ET–04:00 ET Asia/evening slice is
not archived (backfillable from the same endpoint if ever wanted).

Descriptive only — activity, never direction (no overnight quotes exist on any
lane we hold) and never an edge claim. Writes one JSON row per session-night to
data/research/gth_census/nightly.json; prints summary tables to stdout.
"""

from __future__ import annotations

import collections
import json
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
NPZ_DIR = ROOT / "data" / "alpaca_conditions"
OUT_DIR = ROOT / "data" / "research" / "gth_census"
ET = ZoneInfo("America/New_York")

WINDOW_START = time(4, 0)    # npz capture floor (08:00 UTC = 4:00 EDT)
WINDOW_END = time(9, 15)     # GTH close
MACRO_START = time(8, 30)    # 8:30 ET release window → GTH close
TOP_PRINTS = 25


def _ns(session: date, t: time) -> int:
    return int(datetime.combine(session, t, tzinfo=ET).timestamp() * 1e9)


def _parse_occ(symbols: np.ndarray, session: date):
    """Vectorized-enough OCC decode over the (small) unique-symbol table."""
    roots, is_put, is_0dte = [], [], []
    for s in symbols:
        s = str(s)
        roots.append(s[:-15].rstrip("0123456789") if len(s) > 15 else s)
        is_put.append(len(s) > 8 and s[-9] == "P")
        exp = s[-15:-9]
        is_0dte.append(exp == session.strftime("%y%m%d"))
    return np.array(roots), np.array(is_put), np.array(is_0dte)


def census_session(path: Path) -> dict | None:
    session = date.fromisoformat(path.stem)
    d = np.load(path)
    t = d["t_ns"]
    lo, hi = _ns(session, WINDOW_START), _ns(session, WINDOW_END)
    on = (t >= lo) & (t < hi)
    if not on.any():
        return {"session": session.isoformat(), "prints": 0}
    si = d["sym_idx"][on]
    px = d["price_cents"][on].astype(np.float64) / 100.0
    sz = d["size"][on].astype(np.float64)
    cond = d["cond"][on]
    notional = px * sz * 100.0
    roots, is_put, is_0dte = _parse_occ(d["symbols"], session)
    r, put, zd = roots[si], is_put[si], is_0dte[si]

    by_root = {}
    for root in np.unique(r):
        sel = r == root
        by_root[str(root)] = {
            "prints": int(sel.sum()),
            "contracts": int(sz[sel].sum()),
            "notional": round(float(notional[sel].sum()), 2),
        }
    # macro window = 08:30 ET → close
    macro = t[on] >= _ns(session, MACRO_START)
    # RTH code mix from the same file settles whether a code is
    # session-specific or generic (the capital-I question)
    rth = (t >= _ns(session, time(9, 30))) & (t < _ns(session, time(16, 0)))
    rth_cond = d["cond"][rth]

    top = np.argsort(notional)[::-1][:TOP_PRINTS]
    syms = d["symbols"]
    return {
        "session": session.isoformat(),
        "prints": int(on.sum()),
        "contracts": int(sz.sum()),
        "notional": round(float(notional.sum()), 2),
        "put_share_notional": round(float(notional[put].sum() / notional.sum()), 4),
        "zero_dte_share_notional": round(float(notional[zd].sum() / notional.sum()), 4),
        "macro_window_share": round(float(notional[macro].sum() / notional.sum()), 4),
        "size_p50": float(np.percentile(sz, 50)),
        "size_p99": float(np.percentile(sz, 99)),
        "size_max": float(sz.max()),
        "notional_p99": round(float(np.percentile(notional, 99)), 2),
        "by_root": by_root,
        "codes": dict(collections.Counter(cond.tolist()).most_common(8)),
        "rth_codes": dict(collections.Counter(rth_cond.tolist()).most_common(8)),
        "rth_prints": int(rth.sum()),
        "top_prints": [
            {"sym": str(syms[si[i]]), "size": int(sz[i]),
             "price": round(float(px[i]), 2),
             "notional": round(float(notional[i]), 0),
             "cond": str(cond[i]),
             "et": datetime.fromtimestamp(t[on][i] / 1e9, ET).strftime("%H:%M:%S")}
            for i in top
        ],
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    paths = sorted(NPZ_DIR.glob("????-??-??.npz"))
    for i, p in enumerate(paths):
        try:
            row = census_session(p)
        except Exception as e:  # a corrupt night must not kill the sweep
            row = {"session": p.stem, "error": str(e)}
        rows.append(row)
        if (i + 1) % 20 == 0 or i + 1 == len(paths):
            print(f"[{i + 1}/{len(paths)}] {p.stem}", flush=True)
            (OUT_DIR / "nightly.json").write_text(json.dumps(rows, indent=1))
    (OUT_DIR / "nightly.json").write_text(json.dumps(rows, indent=1))

    ok = [r for r in rows if r.get("prints", 0) > 0]
    print(f"\nsessions censused: {len(ok)}/{len(rows)}")
    tot = np.array([r["notional"] for r in ok])
    print(f"nightly notional (4:00-9:15 ET): median ${np.median(tot) / 1e6:.1f}M "
          f"p90 ${np.percentile(tot, 90) / 1e6:.1f}M max ${tot.max() / 1e6:.1f}M")


if __name__ == "__main__":
    main()
