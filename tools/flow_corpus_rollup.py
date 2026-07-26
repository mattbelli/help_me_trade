"""Roll one session of whole-market OPRA tcbbo into the Tier-3 flow corpus.

Streams nothing into pandas: the session is ~11.2M prints and serv runs the live
collectors, so this stays on numpy structured arrays (measured 0.89 GB array,
1.87 GB peak RSS, 2s load) rather than risking an OOM on the box that holds the
OPRA socket.

Classification follows `FLOW_BASELINE_PREREG_2026-07-26.md` + Addendum A:
  PRIMARY   midpoint rule   price vs (bid+ask)/2      → 87.8% of prints
  SECONDARY strict rule     price >= ask / <= bid     → 61.9% of prints
Both are written on every row so neither can be chosen after seeing results.
(Rates measured post-join on 2025-07-28 by tools/flow_classifier_audit.py; the
10.1% that print exactly at the mid stay unsigned rather than being guessed.)

Directional intensity is written twice for the same reason: `u_premium` exactly as
pre-registered, and `u_premium_clipped` with u bounded to [-1, 1]. Raw u is a ratio
over the half-spread and is unbounded, so 0.40% of prints carry 171.92% of the raw
sum. Agreement between the two is the directional read; disagreement means abstain.
Net premium (buy minus sell, already implied by the `side` key) is the robust
third leg and needs no new column.

  python tools/flow_corpus_rollup.py --start 2026-07-23 --end 2026-07-24
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import databento as db
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.flow_corpus import (  # noqa: E402
    DTE_BUCKETS,
    MNY_BUCKETS,
    SCHEMA_VERSION,
    SIDES,
    bars_for_date,
    delete_session_rows,
    ingested_dates,
    init_db,
    record_session,
    upsert_rollup,
    upsert_spot,
)

UNDEF = 9223372036854775807
PX = 1e9  # DBN fixed-point scale
ROOT = Path(__file__).resolve().parent.parent
TCBBO_DIR = ROOT / "data" / "databento" / "opra_tcbbo"
DEFS_DIR = ROOT / "data" / "databento" / "opra_defs"

logger = logging.getLogger("flow_corpus_rollup")


def _load_definitions(session: date) -> tuple[np.ndarray, ...] | None:
    """instrument_id → (underlying, right, strike, dte), as sorted parallel arrays
    so the print-side join is a vectorized searchsorted rather than 11M dict hits."""
    path = DEFS_DIR / f"{session.isoformat()}.dbn.zst"
    if not path.exists():
        return None
    arr = db.DBNStore.from_file(path).to_ndarray()

    cls = arr["instrument_class"]
    keep = (cls == b"C") | (cls == b"P")
    arr = arr[keep]
    if not len(arr):
        return None

    ids = arr["instrument_id"].astype(np.int64)
    order = np.argsort(ids, kind="stable")
    ids = ids[order]
    arr = arr[order]

    unds = np.char.strip(arr["underlying"].astype("U16"))
    uniq_und, und_code = np.unique(unds, return_inverse=True)
    right = (arr["instrument_class"] == b"P").astype(np.int8)  # 0=C 1=P
    strike = arr["strike_price"].astype(np.float64) / PX

    # expiration is ns since epoch; DTE against the session's own date.
    exp_ns = arr["expiration"].astype(np.int64)
    exp_days = exp_ns // 86_400_000_000_000
    sess_days = (session - date(1970, 1, 1)).days
    dte = (exp_days - sess_days).astype(np.int32)

    return ids, und_code, uniq_und, right, strike, dte


def _bucket_dte(dte: np.ndarray) -> np.ndarray:
    b = np.full(dte.shape, 3, dtype=np.int8)          # 90+
    b[dte <= 90] = 2                                   # 31-90
    b[dte <= 30] = 1                                   # 8-30
    b[dte <= 7] = 0                                    # 0-7
    return b


def _bucket_mny(strike: np.ndarray, spot: np.ndarray, right: np.ndarray) -> np.ndarray:
    """0=itm 1=atm 2=otm2_10 3=otm10p 4=unknown. OTM is direction-aware: a call is
    OTM above spot, a put below it — |K/S-1| alone would put both on the same side."""
    out = np.full(strike.shape, 4, dtype=np.int8)
    ok = np.isfinite(spot) & (spot > 0)
    m = np.zeros(strike.shape, dtype=np.float64)
    np.divide(strike, spot, out=m, where=ok)
    m -= 1.0
    am = np.abs(m)
    otm = np.where(right == 0, m > 0, m < 0)           # call: K>S; put: K<S
    out[ok] = 0                                        # itm until proven otherwise
    out[ok & (am < 0.02)] = 1                          # atm band straddles both
    out[ok & otm & (am >= 0.02) & (am < 0.10)] = 2
    out[ok & otm & (am >= 0.10)] = 3
    return out


def rollup_session(session: date, db_path: Path | None = None) -> dict | None:
    kw = {} if db_path is None else {"db_path": db_path}
    tpath = TCBBO_DIR / f"{session.isoformat()}.dbn.zst"
    if not tpath.exists():
        logger.warning("%s: no tcbbo file", session)
        return None

    defs = _load_definitions(session)
    if defs is None:
        logger.warning("%s: no definitions — cannot attribute prints", session)
        return None
    d_ids, d_und, uniq_und, d_right, d_strike, d_dte = defs

    arr = db.DBNStore.from_file(tpath).to_ndarray()
    n_prints = len(arr)
    if not n_prints:
        record_session(session.isoformat(), prints=0, rows=0, underlyings=0,
                       unresolved_prints=0, no_spot_prints=0,
                       ingested_at=datetime.now(UTC).isoformat(), **kw)
        return {"prints": 0, "rows": 0}

    # --- join prints to contracts ------------------------------------------
    pid = arr["instrument_id"].astype(np.int64)
    idx = np.searchsorted(d_ids, pid)
    np.clip(idx, 0, len(d_ids) - 1, out=idx)
    hit = d_ids[idx] == pid
    unresolved = int((~hit).sum())

    arr = arr[hit]
    idx = idx[hit]
    und_code = d_und[idx]
    right = d_right[idx]
    strike = d_strike[idx]
    dte = d_dte[idx]

    # --- classify -----------------------------------------------------------
    price = arr["price"].astype(np.float64)
    size = arr["size"].astype(np.float64)
    bid = arr["bid_px_00"].astype(np.float64)
    ask = arr["ask_px_00"].astype(np.float64)

    good_q = (arr["bid_px_00"] != UNDEF) & (arr["ask_px_00"] != UNDEF) \
        & (bid > 0) & (ask > 0) & (ask > bid)
    mid = np.where(good_q, (bid + ask) / 2.0, np.nan)
    half = np.where(good_q, (ask - bid) / 2.0, np.nan)

    with np.errstate(invalid="ignore", divide="ignore"):
        u = (price - mid) / half
    u = np.nan_to_num(u, nan=0.0, posinf=0.0, neginf=0.0)

    side = np.full(len(arr), 2, dtype=np.int8)              # unclassified
    side[good_q & (price > mid)] = 0                        # buy
    side[good_q & (price < mid)] = 1                        # sell

    strict = np.full(len(arr), 2, dtype=np.int8)
    strict[good_q & (price >= ask)] = 0
    strict[good_q & (price <= bid)] = 1

    premium = price / PX * size * 100.0

    # --- moneyness against the session close --------------------------------
    closes = bars_for_date(session.isoformat(), **kw)
    spot_by_code = np.array(
        [closes.get(str(s), np.nan) for s in uniq_und], dtype=np.float64
    )
    spot = spot_by_code[und_code]
    no_spot = int((~np.isfinite(spot)).sum())

    dte_b = _bucket_dte(dte)
    mny_b = _bucket_mny(strike, spot, right)

    # --- aggregate ----------------------------------------------------------
    key = ((((und_code.astype(np.int64) * 2 + right) * 4 + dte_b) * 5 + mny_b) * 3
           + side) * 3 + strict
    uniq, inv = np.unique(key, return_inverse=True)
    n = len(uniq)
    prints_ = np.bincount(inv, minlength=n)
    contracts = np.bincount(inv, weights=size, minlength=n)
    prem = np.bincount(inv, weights=premium, minlength=n)
    prem_sq = np.bincount(inv, weights=premium * premium, minlength=n)
    u_prem = np.bincount(inv, weights=u * premium, minlength=n)
    # Pre-registered u_prem above, unchanged. Clipped counterpart below: u is a
    # ratio over the half-spread, so 0.40% of prints with |u|>2 carry 171.92% of
    # the raw sum. Bounding at the touch says direction saturates where the quote
    # does. Both are stored so the registered test still runs as registered and
    # the robustness check is visible rather than baked in.
    u_prem_clip = np.bincount(inv, weights=np.clip(u, -1.0, 1.0) * premium,
                              minlength=n)
    prem_max = np.zeros(n)
    np.maximum.at(prem_max, inv, premium)

    k = uniq
    k, s_strict = np.divmod(k, 3)
    k, s_side = np.divmod(k, 3)
    k, s_mny = np.divmod(k, 5)
    k, s_dte = np.divmod(k, 4)
    s_und, s_right = np.divmod(k, 2)

    ds = session.isoformat()
    delete_session_rows(ds, **kw)
    rows = [
        (ds, str(uniq_und[s_und[i]]), "P" if s_right[i] else "C",
         DTE_BUCKETS[s_dte[i]], MNY_BUCKETS[s_mny[i]], SIDES[s_side[i]],
         SIDES[s_strict[i]], int(prints_[i]), int(contracts[i]),
         float(prem[i]), float(prem_sq[i]), float(prem_max[i]),
         float(u_prem[i]), float(u_prem_clip[i]), SCHEMA_VERSION)
        for i in range(n)
    ]
    upsert_rollup(rows, **kw)
    upsert_spot(
        [(ds, str(uniq_und[i]), float(spot_by_code[i]), "alpaca_split_close")
         for i in range(len(uniq_und)) if np.isfinite(spot_by_code[i])],
        **kw,
    )
    record_session(ds, prints=n_prints, rows=len(rows),
                   underlyings=int(len(np.unique(und_code))),
                   unresolved_prints=unresolved, no_spot_prints=no_spot,
                   ingested_at=datetime.now(UTC).isoformat(), **kw)
    return {"prints": n_prints, "rows": len(rows), "unresolved": unresolved,
            "no_spot": no_spot, "underlyings": int(len(np.unique(und_code)))}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True, help="exclusive")
    p.add_argument("--force", action="store_true", help="re-roll already-ingested days")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    init_db()
    done = ingested_dates()
    d, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    while d < end:
        if d.weekday() < 5 and (args.force or d.isoformat() not in done):
            t0 = datetime.now(UTC)
            res = rollup_session(d)
            if res:
                logger.info(
                    "%s prints=%s rows=%s unds=%s unresolved=%s no_spot=%s %.0fs",
                    d, f"{res['prints']:,}", f"{res['rows']:,}",
                    res.get("underlyings"), f"{res.get('unresolved', 0):,}",
                    f"{res.get('no_spot', 0):,}",
                    (datetime.now(UTC) - t0).total_seconds(),
                )
        d += timedelta(days=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
