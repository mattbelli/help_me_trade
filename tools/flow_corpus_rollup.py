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

Directional intensity is written twice: `u_premium` exactly as pre-registered, and
`u_premium_clipped` with u bounded to [-1, 1]. Raw u is a ratio over the
half-spread and is unbounded, so 0.40% of prints carry 171.92% of the raw sum.
With net premium (implied by the `side` key) that is three weightings of ONE
signed axis, not three independent votes — `sign(u)` equals the midpoint side by
construction, since `half > 0` under `good_q`. Their agreement therefore tests
only whether a bucket's direction survives the choice of weighting.

It does NOT test the sign itself, which is why `multileg` is a grain (Addendum D):
a spread leg is priced off the package, not its own NBBO, and dropping those
prints flips the 2025-07-28 session sign of all three measures at once.

Two counting rules ride alongside, both schema_version 4:
  * `orders` — distinct (ts_event, instrument_id) groups. One sweep is one
    observation however many venues it crossed; `orders`, never `prints`, is the
    N for a z-score.
  * `no_spot_index` vs `no_spot_missing` — a cash-settled index has no spot by
    construction; an ordinary name without one is a fault. Pooling them buried a
    0.07%-of-premium alarm inside a 38.75%-of-premium bucket.

  python tools/flow_corpus_rollup.py --start 2026-07-23 --end 2026-07-24
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.flow_corpus import (  # noqa: E402
    DTE_BUCKETS,
    INDEX_ROOTS,
    MNY_BUCKETS,
    SCHEMA_VERSION,
    SIDES,
    bars_before_date,
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
    # Imported inside the reader, not at module scope: databento is not in
    # requirements.txt (dbn_stream.py does the same at its use site), and the pure
    # bucketing/grouping helpers below must stay importable for tests without it.
    import databento as dbn

    path = DEFS_DIR / f"{session.isoformat()}.dbn.zst"
    if not path.exists():
        return None
    arr = dbn.DBNStore.from_file(path).to_ndarray()

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


def _multileg(ts_event: np.ndarray, iid: np.ndarray) -> np.ndarray:
    """True where a print shares its ts_event with a print on a DIFFERENT
    instrument — the multi-leg signature.

    A spread leg is priced by the firm allocating the package, so it need not sit
    inside that leg's own NBBO and its midpoint side is unreliable. Because all
    three registered measures share one per-print sign, that unreliability is not
    confined to the intensity measures: on 2025-07-28 excluding these prints flips
    the session sign of net_premium too.

    Sorting by (ts_event, instrument_id) makes a group multi-leg iff its first and
    last instrument_id differ — no per-group Python loop over 6.9M prints.

    Requiring a *different* instrument is what separates a package from a sweep:
    same instrument at the same nanosecond is one order crossing several venues
    (44.5% of the 2-print groups), which is not contamination.
    """
    order = np.lexsort((iid, ts_event))
    ts_s, iid_s = ts_event[order], iid[order]
    starts = np.flatnonzero(np.r_[True, ts_s[1:] != ts_s[:-1]])
    ends = np.r_[starts[1:], len(ts_s)] - 1
    mixed = np.repeat(iid_s[starts] != iid_s[ends], ends - starts + 1)
    out = np.empty(len(ts_event), dtype=bool)
    out[order] = mixed
    return out


def _order_groups(ts_event: np.ndarray, iid: np.ndarray) -> tuple[np.ndarray, int]:
    """Group id per print, where a group is one (ts_event, instrument_id).

    Same instrument at the same nanosecond is ONE order crossing several venues,
    so N prints are one observation. Distinct from `_multileg`, which keys on a
    *different* instrument at the same nanosecond: same key, opposite predicate,
    different meaning. Returns (group_id_per_print, n_groups).
    """
    order = np.lexsort((iid, ts_event))
    ts_s, iid_s = ts_event[order], iid[order]
    new = np.r_[True, (ts_s[1:] != ts_s[:-1]) | (iid_s[1:] != iid_s[:-1])]
    gid = np.empty(len(ts_event), dtype=np.int64)
    gid[order] = np.cumsum(new) - 1
    return gid, int(new.sum())


def _orders_per_bucket(inv: np.ndarray, gid: np.ndarray, n_groups: int,
                       n_buckets: int) -> np.ndarray:
    """Distinct groups contributing to each rollup bucket.

    Counts unique (bucket, group) pairs rather than assuming a group lands wholly
    in one bucket. Usually it does — a group is one instrument, so it shares an
    underlying, right, DTE and moneyness — but `side` is per print, and 1.371% of
    multi-print groups have legs on both sides. Those straddle two buckets and
    must be counted in each, or the sell half of every mixed sweep vanishes.
    """
    pair = np.unique(inv.astype(np.int64) * n_groups + gid)
    return np.bincount(pair // n_groups, minlength=n_buckets)


def _bucket_dte(dte: np.ndarray) -> np.ndarray:
    b = np.full(dte.shape, 3, dtype=np.int8)          # 90+
    b[dte <= 90] = 2                                   # 31-90
    b[dte <= 30] = 1                                   # 8-30
    b[dte <= 7] = 0                                    # 0-7
    return b


def _bucket_mny(
    strike: np.ndarray,
    spot: np.ndarray,
    right: np.ndarray,
    is_index: np.ndarray,
) -> np.ndarray:
    """0=itm 1=atm 2=otm2_10 3=otm10p 4=no_spot_index 5=no_spot_missing.

    OTM is direction-aware: a call is OTM above spot, a put below it — |K/S-1|
    alone would put both on the same side.

    The two no-spot arms split what schema_version 3 pooled under 'unknown'. A
    cash-settled index has no share to price against, so it is unbucketable; an
    ordinary name with no bar is a fault. `is_index` only decides which arm a
    print falls into when the spot is genuinely absent — an index root that DOES
    have a bar is bucketed normally, so this can never suppress real moneyness.
    """
    out = np.where(is_index, 4, 5).astype(np.int8)
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

    import databento as dbn

    arr = dbn.DBNStore.from_file(tpath).to_ndarray()
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

    ml = _multileg(arr["ts_event"].astype(np.int64),
                   arr["instrument_id"].astype(np.int64)).astype(np.int64)

    # --- moneyness against the PRIOR session's close -------------------------
    # Addendum B registers the prior close, and using this session's own close was
    # a look-ahead: it bucketed moneyness against the move that had just happened,
    # so every OTM label carried the day's realized return. Fixed 2026-07-27.
    closes = bars_before_date(session.isoformat(), **kw)
    spot_by_code = np.array(
        [closes.get(str(s), (np.nan, ""))[0] for s in uniq_und], dtype=np.float64
    )
    # How old each spot is, in calendar days. A live name reads 1-3 (weekends);
    # a delisted or long-halted one can read weeks, which is a real bucketing
    # caveat rather than an error -- counted so it can never pass silently.
    spot_age = np.array(
        [(session - date.fromisoformat(d)).days if d else -1
         for _, (_, d) in ((s, closes.get(str(s), (np.nan, ""))) for s in uniq_und)],
        dtype=np.int32,
    )
    spot = spot_by_code[und_code]
    stale_spot = int((np.isfinite(spot_by_code) & (spot_age > 5))[und_code].sum())

    idx_by_code = np.array([str(s) in INDEX_ROOTS for s in uniq_und])
    is_index = idx_by_code[und_code]

    dte_b = _bucket_dte(dte)
    mny_b = _bucket_mny(strike, spot, right, is_index)

    # Only the *missing* arm is a fault worth alarming on; index roots are
    # unbucketable by construction. Premium travels with the count because that
    # is what makes it an alarm: a missed index root moves this from ~$19M to
    # billions in a single session, while a delisted small cap barely moves it.
    missing = mny_b == 5
    no_spot = int(missing.sum())
    no_spot_index = int((mny_b == 4).sum())
    no_spot_premium = float(premium[missing].sum())

    # --- aggregate ----------------------------------------------------------
    key = (((((und_code.astype(np.int64) * 2 + right) * 4 + dte_b) * 6 + mny_b) * 3
            + side) * 3 + strict) * 2 + ml
    uniq, inv = np.unique(key, return_inverse=True)
    n = len(uniq)
    prints_ = np.bincount(inv, minlength=n)
    gid, n_groups = _order_groups(arr["ts_event"].astype(np.int64),
                                  arr["instrument_id"].astype(np.int64))
    orders = _orders_per_bucket(inv, gid, n_groups, n)
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
    k, s_ml = np.divmod(k, 2)
    k, s_strict = np.divmod(k, 3)
    k, s_side = np.divmod(k, 3)
    k, s_mny = np.divmod(k, 6)
    k, s_dte = np.divmod(k, 4)
    s_und, s_right = np.divmod(k, 2)

    ds = session.isoformat()
    delete_session_rows(ds, **kw)
    rows = [
        (ds, str(uniq_und[s_und[i]]), "P" if s_right[i] else "C",
         DTE_BUCKETS[s_dte[i]], MNY_BUCKETS[s_mny[i]], SIDES[s_side[i]],
         SIDES[s_strict[i]], int(prints_[i]), int(orders[i]), int(contracts[i]),
         float(prem[i]), float(prem_sq[i]), float(prem_max[i]),
         float(u_prem[i]), float(u_prem_clip[i]), int(s_ml[i]), SCHEMA_VERSION)
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
    return {"prints": n_prints, "joined": int(len(arr)), "orders": n_groups,
            "rows": len(rows),
            "unresolved": unresolved, "stale_spot": stale_spot,
            "no_spot": no_spot, "no_spot_index": no_spot_index,
            "no_spot_premium": no_spot_premium,
            "underlyings": int(len(np.unique(und_code)))}


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
                # no_spot_$ is the alarm: index roots are excluded from it, so a
                # root missing from INDEX_ROOTS shows up as billions instead of
                # the ~$19M/session that ordinary delistings and ticker-format
                # breaks account for.
                # prints is what the file held; joined is what survived the
                # contract join and is the only count `orders` is comparable to.
                logger.info(
                    "%s prints=%s joined=%s orders=%s rows=%s unds=%s "
                    "unresolved=%s no_spot=%s no_spot_$=%.1fM idx_no_spot=%s "
                    "stale_spot=%s %.0fs",
                    d, f"{res['prints']:,}", f"{res.get('joined', 0):,}",
                    f"{res.get('orders', 0):,}", f"{res['rows']:,}",
                    res.get("underlyings"), f"{res.get('unresolved', 0):,}",
                    f"{res.get('no_spot', 0):,}",
                    res.get("no_spot_premium", 0.0) / 1e6,
                    f"{res.get('no_spot_index', 0):,}",
                    f"{res.get('stale_spot', 0):,}",
                    (datetime.now(UTC) - t0).total_seconds(),
                )
        d += timedelta(days=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
