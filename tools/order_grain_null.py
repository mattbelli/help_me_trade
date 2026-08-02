"""Roadmap 1.7 Addendum A2: the EXACT per-order sign shuffle.

Registered in FLOW_ROADMAP §1.7 Addendum A before running. The original 1.7 null
drew each name-day's tilt from Normal(0, 1/sqrt(N_eff)) — variance exact, tails
approximate — and the registration stated up front that a PASS under that null is
provisional until an exact per-order shuffle confirms it on >=20 sessions. This is
that run: every order gets an independent eps = +/-1, no distributional assumption.

Two differences from the A1 machinery, both deliberate:

* **The flip is per ORDER, not per (bucket, order) pair.** The rollup's
  `premium_sq_orders` treats the parts of one sweep that land in different buckets
  as separate observations; here the whole order carries ONE sign. That is
  strictly more conservative (fewer independent decisions) and is exactly the kind
  of structure a normal approximation cannot see.
* **Classified premium only.** The registered rule tilts over buy + sell, so
  unclassified prints are excluded from the base. A1 summed `premium_usd` across
  all sides. To keep the comparison honest this run recomputes BOTH nulls — exact
  shuffle and normal draw — on the same classified-only base and the same
  sessions, so any gap is the null mechanism and not the data.

  python tools/order_grain_null.py --extract   # decode 40 sessions -> per-order npz
  python tools/order_grain_null.py             # run both nulls, compare

**Addendum G §G.9c — the FENCED path (`--fenced`).** The extractor above is v6-fence
semantics: its `good_q` has no §1.25 clause-1 spread gate, it never masks
package-coded prints, and it admits every root. G.9c registers the table of record
on the v7 fence over the EQUITY universe, so `_extract_fenced` re-emits the
per-order panel through `fence_and_classify()` + `multileg_flags()` (the tested v7
functions, imported not copied) and the frozen `root_classification.json`. It
writes to its OWN versioned cache directory, so the unfenced npz can never be
silently reused by a fenced run.

  python tools/order_grain_null.py --extract --fenced --sessions 0
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import NamedTuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.flow_corpus import (  # noqa: E402
    DEFAULT_DB_PATH,
    INDEX_ROOTS,
    bars_before_date,
    connect,
)
from tools.flow_corpus_rollup import (  # noqa: E402
    PX,
    TCBBO_DIR,
    UNDEF,
    _bucket_dte,
    _bucket_mny,
    _load_definitions,
    _order_groups,
    fence_and_classify,
    multileg_flags,
)
from tools.root_classification_build import load as load_classification  # noqa: E402

OUT_DIR = ROOT / "data" / "order_grain_null"
# G.9c: "Extract cache versioned to a NEW directory so the fenced re-emission can
# never silently reuse unfenced npz." Nothing writes to OUT_DIR on this path.
FENCED_OUT_DIR = ROOT / "data" / "order_grain_null_v7_equity"
ROOT_CLASSIFICATION = ROOT / "signals" / "options_flow" / "root_classification.json"
N_SESSIONS = 40           # 20 baseline burn-in + 20 tested days
LOOKBACK = 20
MIN_PRESENT = 15
PREMIUM_FLOOR = 250_000.0
Z_EVENT = 2.0
N_DRAWS = 20
SEED = 20260728
SHORT_DTE_IDX = (0, 1)    # DTE_BUCKETS 0-7, 8-30
OTM_MNY_IDX = (2, 3)      # MNY_BUCKETS otm2_10, otm10p
# tcbbo `size` UNDEF sentinel. v6 is protected only INCIDENTALLY (sentinel prints
# fail the definitions join), so this run guards explicitly and reports the count.
UNDEF_SIZE = 4_294_967_295

logger = logging.getLogger("order_grain_null")


def _sessions(n: int = N_SESSIONS) -> list[date]:
    """The n most recent REAL sessions, newest last (n <= 0 means all of them).

    Market holidays leave an 87-byte tcbbo file (measured: Juneteenth 2026-06-19).
    Counting one as a session would silently cost a tested day — the burn-in eats
    20 of the window, so 40 files with a holiday in them yields 19, not the
    registered 20. Size filter, not a calendar, so it cannot drift out of date.
    """
    days = sorted(p.name.split(".")[0] for p in TCBBO_DIR.glob("*.dbn.zst")
                  if p.stat().st_size > 1_000_000)
    return [date.fromisoformat(d) for d in (days[-n:] if n > 0 else days)]


class _Joined(NamedTuple):
    """One session's prints after the contract join — shared by both extractors."""

    arr: np.ndarray
    und_code: np.ndarray
    uniq_und: np.ndarray
    right: np.ndarray
    strike: np.ndarray
    dte: np.ndarray
    n_prints: int
    n_sentinel: int


def _join_session(session: date) -> _Joined | None:
    """Decode one session and join its prints to contract definitions.

    Lifted verbatim out of `_extract` so the fenced path shares ONE join rather
    than hand-copying it: the fence itself already comes from the rollup's tested
    functions, and this is the only other thing the two paths have in common.
    """
    defs = _load_definitions(session)
    if defs is None:
        logger.warning("%s: no definitions — skipped", session)
        return None
    d_ids, d_und, uniq_und, d_right, d_strike, d_dte = defs

    import databento as dbn

    arr = dbn.DBNStore.from_file(TCBBO_DIR / f"{session.isoformat()}.dbn.zst").to_ndarray()
    n_prints = len(arr)
    if not n_prints:
        return None
    n_sentinel = int((arr["size"] == UNDEF_SIZE).sum())
    arr = arr[arr["size"] != UNDEF_SIZE]

    pid = arr["instrument_id"].astype(np.int64)
    idx = np.searchsorted(d_ids, pid)
    np.clip(idx, 0, len(d_ids) - 1, out=idx)
    hit = d_ids[idx] == pid
    arr, idx = arr[hit], idx[hit]
    if not len(arr):
        logger.warning("%s: all prints failed the definitions join", session)
        return None
    return _Joined(arr, d_und[idx], uniq_und, d_right[idx], d_strike[idx], d_dte[idx],
                   n_prints, n_sentinel)


def _extract(session: date) -> None:
    """Decode one session to per-ORDER premium: (underlying, p_all, p_short_otm)."""
    out = OUT_DIR / f"{session.isoformat()}.npz"
    if out.exists():
        return
    j = _join_session(session)
    if j is None:
        return
    arr, uniq_und = j.arr, j.uniq_und
    und_code, right, strike, dte = j.und_code, j.right, j.strike, j.dte
    n_prints, n_sentinel = j.n_prints, j.n_sentinel

    price = arr["price"].astype(np.float64)
    size = arr["size"].astype(np.float64)
    bid = arr["bid_px_00"].astype(np.float64)
    ask = arr["ask_px_00"].astype(np.float64)
    good_q = ((arr["bid_px_00"] != UNDEF) & (arr["ask_px_00"] != UNDEF)
              & (bid > 0) & (ask > 0) & (ask > bid))
    mid = np.where(good_q, (bid + ask) / 2.0, np.nan)
    # The registered rule tilts over buy + sell only; price == mid is unclassified
    # and contributes to neither, so it is out of the null's premium base too.
    classified = good_q & (price != mid)
    premium = price / PX * size * 100.0

    closes = bars_before_date(session.isoformat())
    spot_by_code = np.array([closes.get(str(s), (np.nan, ""))[0] for s in uniq_und],
                            dtype=np.float64)
    spot = spot_by_code[und_code]
    is_index = np.array([str(s) in INDEX_ROOTS for s in uniq_und])[und_code]
    dte_b = _bucket_dte(dte)
    mny_b = _bucket_mny(strike, spot, right, is_index)
    short_otm = np.isin(dte_b, SHORT_DTE_IDX) & np.isin(mny_b, OTM_MNY_IDX)

    gid, n_groups = _order_groups(arr["ts_event"].astype(np.int64),
                                  arr["instrument_id"].astype(np.int64))
    p_all = np.bincount(gid[classified], weights=premium[classified], minlength=n_groups)
    m_so = classified & short_otm
    p_so = np.bincount(gid[m_so], weights=premium[m_so], minlength=n_groups)
    # An instrument maps to exactly one underlying, so a group does too.
    und_of_g = np.zeros(n_groups, dtype=np.int64)
    und_of_g[gid] = und_code

    keep = p_all > 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out, und=und_of_g[keep].astype(np.int32), p_all=p_all[keep],
        p_so=p_so[keep], names=np.array([str(s) for s in uniq_und]),
    )
    logger.info("%s: %s prints -> %s orders (%s sentinel-size dropped)",
                session, f"{n_prints:,}", f"{int(keep.sum()):,}", f"{n_sentinel:,}")


# ---------------------------------------------------------------------------
# Addendum G §G.9c — the fenced, equity-universe extractor
# ---------------------------------------------------------------------------


class FencedOrders(NamedTuple):
    """One session's surviving ORDERS on the registered pipeline.

    `b_all` / `b_so` are the BULL half of `p_all` / `p_so` (call-buy + put-sell,
    prereg:68-72). Carrying them here is what puts `f` and `f₀` on literally one
    implementation (G.2's single-implementation pin): the real tilt is
    `(2·b − p)/p` on the very arrays the ±1 shuffle flips, so neither side can
    drift onto a different universe, fence, or premium base.
    """

    names: np.ndarray    # resolved identities, sorted — the panel's row labels
    und: np.ndarray      # int32 index into `names`, one per surviving order
    p_all: np.ndarray    # classified premium per order, ALL arm
    p_so: np.ndarray     # classified premium per order, SHORT_OTM arm
    b_all: np.ndarray    # bull half of p_all
    b_so: np.ndarray     # bull half of p_so
    # provenance, persisted so the record is auditable without re-decoding the
    # tape. Both counts are measured at their OWN fence step, not one step off:
    # the package mask precedes the universe fence, so `package_masked_prints`
    # counts prints that would otherwise have SURVIVED, and `orphan_premium`
    # is measured on the POST-package-mask base.
    orphan_roots: np.ndarray
    orphan_premium: float
    package_masked_prints: int


def fenced_orders(session: date, j: _Joined, ml: np.ndarray, closes: dict,
                  rc) -> FencedOrders | None:
    """The registered per-print fence, then re-group the survivors into orders.

    Order of operations is G.2's, verbatim: v7 quote fence + clause-1 spread gate
    (`fence_and_classify`), per-print package exclusion (F.1), the registered
    EQUITY universe with the G.8 mapping applied, and only THEN `_order_groups`.
    Re-grouping last is the point — a package leg or a wide leg must not be able
    to carry its order's premium in through the back door.

    Identity follows G.2's two directions: FLOW identity is date-scoped, so the
    row label is `rc.resolve(root, session)` and a rename's two segments merge
    into ONE panel row; PRICE/BARS reads are not, so the spot that buckets
    moneyness is read at that same terminal symbol rather than the printed root.
    """
    ds = session.isoformat()
    arr, uniq_und = j.arr, j.uniq_und
    und_code, right, strike, dte = j.und_code, j.right, j.strike, j.dte

    roots = [str(s) for s in uniq_und]
    ident_raw = [rc.resolve(r, ds) for r in roots]
    # G.2's reuse-split rule: a segment whose OWN chain does not carry it
    # forward is UNRESOLVABLE, and is EXCLUDED rather than merged into the
    # ticker's later occupant (ARIS is exactly this shape). Since the fence's
    # 2026-08-02 regeneration `resolve()` implements the clause itself and
    # returns None for such a segment — provably the same predicate this
    # function used to compute locally (terminal == root AND a boundary after
    # `ds` ⟺ None). The row label falls back to the printed root so the panel
    # shape is unchanged; the counterfactual class for the orphan-casualty
    # count reads the root at pull date (the same directory row the old
    # date-scoped classify returned before the guard existed).
    orphan = np.array([t is None for t in ident_raw], bool)
    ident = np.array([r if t is None else t
                      for r, t in zip(roots, ident_raw)])
    is_equity = np.array([rc.classify(r, ds if t is not None else None)
                          == "equity"
                          for r, t in zip(roots, ident_raw)], bool)
    in_universe = is_equity & ~orphan
    names, ident_code = np.unique(ident, return_inverse=True)

    spot_by_ident = np.array([closes.get(str(s), (np.nan, ""))[0] for s in names],
                             dtype=np.float64)
    spot = spot_by_ident[ident_code][und_code]
    is_index = np.array([str(s) in INDEX_ROOTS for s in names])[ident_code][und_code]
    dte_b = _bucket_dte(dte)
    mny_b = _bucket_mny(strike, spot, right, is_index)
    short_otm = np.isin(dte_b, SHORT_DTE_IDX) & np.isin(mny_b, OTM_MNY_IDX)

    fx = fence_and_classify(arr)
    # side 0 buy / 1 sell are the only classified labels; 2 unclassified (no
    # usable quote or price == mid) and 3 wide_spread fall out here, which is
    # exactly the clause-1 gate the v6 extractor never had.
    classified = fx.side <= 1
    keep = classified & (ml == 0) & in_universe[und_code]
    if not keep.any():
        logger.warning("%s: no prints survived the registered fence", session)
        return None

    gid, n_groups = _order_groups(arr["ts_event"].astype(np.int64)[keep],
                                  arr["instrument_id"].astype(np.int64)[keep])
    prem = fx.premium[keep]
    # bull = call_buy + put_sell (prereg:68-72). right 0=C 1=P, side 0=buy 1=sell,
    # so the bull legs are exactly the ones where the two codes agree.
    bull = (right[keep] == fx.side[keep]).astype(np.float64)
    so = short_otm[keep]
    p_all = np.bincount(gid, weights=prem, minlength=n_groups)
    b_all = np.bincount(gid, weights=prem * bull, minlength=n_groups)
    p_so = np.bincount(gid[so], weights=prem[so], minlength=n_groups)
    b_so = np.bincount(gid[so], weights=(prem * bull)[so], minlength=n_groups)

    # An instrument maps to exactly one underlying, so a group does too.
    und_of_g = np.zeros(n_groups, dtype=np.int64)
    und_of_g[gid] = ident_code[und_code[keep]]

    # Excluded, and counted with premium — G.2 routes orphaned segments to the
    # ungradeable census rather than letting them vanish silently. Measured on
    # the post-package-mask base, the fence step they are actually cut at.
    # `is_equity & orphan` and not `orphan` alone: a segment that would have been
    # excluded by the universe fence anyway is not an orphan casualty.
    om = (is_equity & orphan)[und_code] & classified & (ml == 0)
    orphan_roots = sorted({roots[i] for i in np.flatnonzero(is_equity & orphan)})
    orphan_premium = float(fx.premium[om].sum())
    # what F.1's per-print package mask removed from the otherwise-surviving set
    masked = int((classified & (ml != 0) & in_universe[und_code]).sum())
    if orphan_roots:
        logger.info("%s: %s orphaned reuse-segment prints excluded ($%.1fM) — %s",
                    session, f"{int(om.sum()):,}", orphan_premium / 1e6,
                    ", ".join(orphan_roots))

    m = p_all > 0
    used, und = np.unique(und_of_g[m], return_inverse=True)
    return FencedOrders(names[used], und.astype(np.int32), p_all[m], p_so[m],
                        b_all[m], b_so[m], np.array(orphan_roots, dtype="U16"),
                        orphan_premium, masked)


_RC = None
_BARS_VINTAGE: tuple[str, int] | None = None


def classification():
    """The frozen G.8 snapshot, read once per process. Read-only import."""
    global _RC
    if _RC is None:
        _RC = load_classification(ROOT_CLASSIFICATION)
    return _RC


def bars_vintage() -> tuple[str, int]:
    """`(max equity_bars date, row count)` — read once, stamped on every npz.

    Obligation 2's backfill is what makes the moneyness spot reachable at the
    terminal symbol, so an extract taken BEFORE it is a different measurement
    wearing the same filename. Stamping the vintage per session makes a
    pre-backfill (or half-backfilled, resumed) cache detectable rather than
    something a reader has to take on trust.
    """
    global _BARS_VINTAGE
    if _BARS_VINTAGE is None:
        with connect(DEFAULT_DB_PATH) as conn:
            row = conn.execute("SELECT MAX(date), COUNT(*) FROM equity_bars").fetchone()
        _BARS_VINTAGE = (str(row[0]), int(row[1]))
    return _BARS_VINTAGE


def _extract_fenced(session: date, out_dir: Path = FENCED_OUT_DIR) -> None:
    """G.9c's re-emission of one session, into the versioned cache."""
    out = out_dir / f"{session.isoformat()}.npz"
    if out.exists():
        return
    j = _join_session(session)
    if j is None:
        return
    # multileg_flags wants the RAW DBN fixed-point price and the integer size —
    # handing it dollars silently degrades the flag to the ns-collision heuristic.
    ml, _cond, ml_source, join_rate, coverage = multileg_flags(
        session, j.arr["ts_event"].astype(np.int64),
        j.arr["instrument_id"].astype(np.int64),
        j.arr["price"].astype(np.float64), j.arr["size"].astype(np.int64))
    res = fenced_orders(session, j, ml, bars_before_date(session.isoformat()),
                        classification())
    if res is None:
        return
    bars_max, bars_rows = bars_vintage()
    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out, und=res.und, p_all=res.p_all, p_so=res.p_so, b_all=res.b_all,
        b_so=res.b_so, names=res.names,
        # provenance. `ml_source`/`join_rate` carry Addendum F's >=98% reporting
        # gate per session, which is what the robustness subset is selected on.
        orphan_roots=res.orphan_roots,
        orphan_premium=np.float64(res.orphan_premium),
        package_masked_prints=np.int64(res.package_masked_prints),
        ml_source=np.array(ml_source), join_rate=np.float64(
            join_rate if join_rate is not None else np.nan),
        label_coverage=np.float64(coverage if coverage is not None else np.nan),
        bars_max_date=np.array(bars_max), bars_rows=np.int64(bars_rows))
    logger.info("%s: %s prints -> %s equity orders / %s identities "
                "(%s package prints masked, multileg=%s join=%s, bars<=%s)",
                session, f"{j.n_prints:,}", f"{len(res.p_all):,}",
                f"{len(res.names):,}", f"{res.package_masked_prints:,}", ml_source,
                f"{join_rate:.4f}" if join_rate is not None else "-", bars_max)


def _load_panel(sessions: list[date]) -> tuple:
    """Per-order premiums -> ragged per (name, day) lists, plus the day totals."""
    # Two passes on purpose. Each npz stores a small per-session ticker list plus
    # an int code per order, so the global name set comes from the LISTS alone —
    # expanding codes to ticker strings for all 40 days at once is ~9 GB and OOMs.
    files = [OUT_DIR / f"{s.isoformat()}.npz" for s in sessions]
    all_names: set[str] = set()
    for f in files:
        if f.exists():
            with np.load(f, allow_pickle=False) as z:
                all_names.update(z["names"].tolist())
    names = np.array(sorted(all_names))
    ni = {n: i for i, n in enumerate(names.tolist())}
    n_names, n_days = len(names), len(sessions)

    rows, groups = [], []
    for f in files:
        if not f.exists():
            rows.append(None)
            groups.append(None)
            continue
        with np.load(f, allow_pickle=False) as z:
            # local ticker code -> global row, applied as one vectorised gather
            lut = np.array([ni[n] for n in z["names"].tolist()], dtype=np.int32)
            rows.append(lut[z["und"]])
            # float32 per order: 380M orders would be 6 GB at float64. Sums still
            # accumulate in float64 (bincount's output dtype), and a 1e-7 relative
            # error on one order's premium cannot move a tilt ratio.
            groups.append((z["p_all"].astype(np.float32),
                           z["p_so"].astype(np.float32)))
    return names, rows, groups, n_names, n_days


def _fire(tilt, present, eligible):
    """The registered rule, verbatim, on a (names x days) tilt panel."""
    n_names, n_days = tilt.shape
    fired = np.zeros(tilt.shape, bool)
    tested = np.zeros(tilt.shape, bool)
    vals = np.where(present, tilt, 0.0)
    cnt = present.astype(np.float64)
    cs = np.cumsum(np.c_[np.zeros(n_names), vals], 1)
    cq = np.cumsum(np.c_[np.zeros(n_names), vals ** 2], 1)
    ck = np.cumsum(np.c_[np.zeros(n_names), cnt], 1)
    for d in range(LOOKBACK, n_days):
        lo, hi = d - LOOKBACK, d
        s, s2, k = cs[:, hi] - cs[:, lo], cq[:, hi] - cq[:, lo], ck[:, hi] - ck[:, lo]
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = s / np.maximum(k, 1)
            std = np.sqrt(np.maximum(s2 / np.maximum(k, 1) - mean ** 2, 0.0))
            z = (tilt[:, d] - mean) / std
        ok = (k >= MIN_PRESENT) & eligible[:, d] & present[:, d] & np.isfinite(z) & (std > 0)
        tested[:, d] = ok
        fired[:, d] = ok & (np.abs(z) >= Z_EVENT)
    return fired, tested


def _report(label, fired_q, tested_q):
    print(f"\n=== {label} ===")
    print("  quintile          tested       fired     rate")
    rates = []
    for i in range(5):
        r = fired_q[i] / tested_q[i] if tested_q[i] else float("nan")
        rates.append(r)
        name = ["Q1 least liquid", "Q2", "Q3", "Q4", "Q5 most liquid"][i]
        print(f"  {name:<16} {tested_q[i]:>10,} {fired_q[i]:>10,}  {r:>7.3%}")
    pooled = fired_q.sum() / max(tested_q.sum(), 1)
    print(f"  {'pooled':<16} {tested_q.sum():>10,} {fired_q.sum():>10,}  {pooled:>7.3%}")
    ratio = rates[0] / rates[4] if rates[4] else float("nan")
    print(f"  Q1/Q5 = {ratio:.2f}x")
    return pooled, ratio


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", action="store_true")
    # A2 ran on the 40-session default and that is left untouched so its result stays
    # reproducible. 1.7 Addendum B extracts the full year through the same code path.
    ap.add_argument("--sessions", type=int, default=N_SESSIONS,
                    help="how many recent real sessions; <=0 means all on disk")
    # Must match the A1-corrected eligibility. Left at the registered $250k, A2
    # reproduces exactly the pathology A1 diagnosed: Q1 gets ~20 tested name-days
    # and SHORT_OTM's gets zero, so the Q1/Q5 leg compares nothing to nothing.
    ap.add_argument("--day-floor", type=float, default=0.0,
                    help="per-day total premium floor; 0 = the A1 no-floor universe")
    # G.9c's fenced re-emission. Extract-only: the A2 normal-vs-exact comparison
    # below is a CLOSED criterion and is not re-run on a new universe.
    ap.add_argument("--fenced", action="store_true",
                    help="G.9c: v7 fence + equity universe -> the versioned cache")
    ap.add_argument("--cache-dir", type=Path, default=None,
                    help="override the FENCED cache directory (dry runs)")
    args = ap.parse_args()
    if args.cache_dir and not args.fenced:
        raise SystemExit("--cache-dir is the fenced path's cache; the v6-fence "
                         "extract writes to its own frozen directory")
    if args.fenced and not args.extract:
        raise SystemExit("--fenced is an extract-only path (G.9c); the A2 "
                         "comparison below is closed and is not re-run")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sessions = _sessions(args.sessions)
    logger.info("sessions: %s .. %s (%d)", sessions[0], sessions[-1], len(sessions))

    if args.extract:
        for s in sessions:
            if args.fenced:
                _extract_fenced(s, args.cache_dir or FENCED_OUT_DIR)
            else:
                _extract(s)
        return

    names, rows, groups, n_names, n_days = _load_panel(sessions)
    logger.info("panel: %d names x %d sessions", n_names, n_days)

    # Day totals and N_eff are FIXED market structure — identical for both nulls.
    P_ALL = np.zeros((n_names, n_days))
    P_SO = np.zeros((n_names, n_days))
    Q_ALL = np.zeros((n_names, n_days))
    Q_SO = np.zeros((n_names, n_days))
    # bincount, not np.add.at: the exact null does ~7.4e9 scatter-adds and add.at
    # is ~30x slower, which is the difference between minutes and days.
    for d in range(n_days):
        if rows[d] is None:
            continue
        p_all, p_so = (groups[d][0].astype(np.float64), groups[d][1].astype(np.float64))
        P_ALL[:, d] = np.bincount(rows[d], weights=p_all, minlength=n_names)
        P_SO[:, d] = np.bincount(rows[d], weights=p_so, minlength=n_names)
        Q_ALL[:, d] = np.bincount(rows[d], weights=p_all ** 2, minlength=n_names)
        Q_SO[:, d] = np.bincount(rows[d], weights=p_so ** 2, minlength=n_names)

    med = np.array([np.median(r[r > 0]) if (r > 0).any() else 0.0 for r in P_ALL])
    live = med > 0
    quint = np.full(n_names, -1, np.int64)
    quint[live] = np.digitize(med[live], np.quantile(med[live], [0.2, 0.4, 0.6, 0.8]))
    eligible = P_ALL >= args.day_floor if args.day_floor else P_ALL > 0

    sig, pres = {}, {}
    for arm, P, Q in (("ALL", P_ALL, Q_ALL), ("SHORT_OTM", P_SO, Q_SO)):
        with np.errstate(invalid="ignore", divide="ignore"):
            n_eff = np.where(Q > 0, P ** 2 / Q, np.nan)
        s = np.where(np.isfinite(n_eff) & (n_eff > 0), 1 / np.sqrt(n_eff), np.nan)
        sig[arm], pres[arm] = s, (P > 0) & np.isfinite(s)

    counts = {(a, m): [np.zeros(5, np.int64), np.zeros(5, np.int64)]
              for a in ("ALL", "SHORT_OTM") for m in ("exact", "normal")}

    def _tally(arm, mech, tilt):
        f, t = _fire(tilt, pres[arm], eligible)
        fq, tq = counts[(arm, mech)]
        for i in range(5):
            m = quint == i
            fq[i] += int(f[m].sum())
            tq[i] += int(t[m].sum())

    # EXACT: one coin flip per order, shared by both arms — a sweep is one
    # decision, so its short-OTM legs cannot get a different sign from its others.
    rng = np.random.default_rng(SEED)
    for draw in range(N_DRAWS):
        t_all = np.zeros((n_names, n_days))
        t_so = np.zeros((n_names, n_days))
        for d in range(n_days):
            if rows[d] is None:
                continue
            p_all, p_so = groups[d]
            eps = (rng.integers(0, 2, size=len(p_all), dtype=np.int8) * 2 - 1
                   ).astype(np.float64)
            num_a = np.bincount(rows[d], weights=eps * p_all, minlength=n_names)
            num_s = np.bincount(rows[d], weights=eps * p_so, minlength=n_names)
            with np.errstate(invalid="ignore", divide="ignore"):
                t_all[:, d] = np.where(P_ALL[:, d] > 0, num_a / P_ALL[:, d], 0.0)
                t_so[:, d] = np.where(P_SO[:, d] > 0, num_s / P_SO[:, d], 0.0)
        _tally("ALL", "exact", t_all)
        _tally("SHORT_OTM", "exact", t_so)
        logger.info("exact draw %d/%d", draw + 1, N_DRAWS)

    # NORMAL: the original 1.7 mechanism, on the SAME sessions and premium base.
    rng = np.random.default_rng(SEED)
    for _ in range(N_DRAWS):
        for arm in ("ALL", "SHORT_OTM"):
            s = sig[arm]
            _tally(arm, "normal",
                   rng.standard_normal(s.shape) * np.nan_to_num(s, nan=0.0))

    out = {k: _report(f"arm {k[0]} — {k[1]} null", *v) for k, v in counts.items()}

    print("\n=== A2 registered comparison (bar: |Δpooled| ≤ 2.0pt, |ΔQ1/Q5| ≤ 0.5x) ===")
    for arm in ("ALL", "SHORT_OTM"):
        pe, re_ = out[(arm, "exact")]
        pn, rn = out[(arm, "normal")]
        dp, dr = abs(pe - pn) * 100, abs(re_ - rn)
        ok = "PASS" if (dp <= 2.0 and dr <= 0.5) else "FAIL"
        print(f"  {arm:<10} pooled {pe:.3%} vs {pn:.3%} (Δ{dp:.2f}pt)   "
              f"Q1/Q5 {re_:.2f}x vs {rn:.2f}x (Δ{dr:.2f}x)   -> {ok}")


if __name__ == "__main__":
    main()
