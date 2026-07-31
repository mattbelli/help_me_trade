"""Addendum G obligation 3/3b — the registered pipeline's pins, hermetically.

Every fixture here is synthetic: a nine-print mini-session, a hand-built root
classification, and hand-built per-order npz panels. Nothing reads the corpus,
the tape, the OI database or the frozen snapshot, so this module can run before
the one-shot registered emission and say something about it.

What it pins (each is a registered clause that a plausible implementation gets
wrong silently):

  * the fence really excludes a package-coded print and a wide-spread print,
  * the equity universe really excludes an index root and a test root,
  * an unresolvable reuse segment is EXCLUDED, not merged into the ticker's
    later occupant, and its premium is counted after the package mask,
  * the moneyness spot is read at the TERMINAL symbol,
  * the $250k floor gates the EVENT day only — presence counts ANY
    classified-premium day (G.2's single-implementation pin),
  * the judgment mask is the mask that feeds per-draw rates and the across-draw
    SE, not a mask sitting beside `full`,
  * the shuffle is per ORDER and ±1, never an aggregate draw,
  * 3b's tie-break key order, and a degenerate census refusing to select,
  * G.9b's bootstrap assembles circular blocks and reproduces on its seed.

And the operator-mistake guards, which exist because first-run-governs makes a
wrong governing record unrecoverable: a partial cache stops the run (a missing
npz still consumes `default_rng([SEED, d])`), the fenced extract cannot touch
the unfenced cache, and an existing record is never overwritten without --force.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tools.open_close_split as ocs  # noqa: E402  (module handle for monkeypatching)
import tools.order_grain_null as ognull  # noqa: E402
from tools.base_rate_table import (
    JUDGMENT_DATES,
    LEAD_IN_SESSIONS,
    _build,
    _cache_provenance,
    _fire,
    _name_index,
    _trailing_quintiles,
    null_table,
    real_table,
    real_tilt,
    registered_panel,
    registered_run,
    require_cache,
)
from tools.flow_corpus_rollup import PX, SPREAD_MAX_FRAC, UNDEF
from tools.flow_obligation3 import (
    _jsonable,
    _print_arm,
    circular_blocks,
    dilution_precision,
    tilt_dependence,
    write_record,
)
from tools.open_close_split import KEY_ORDER, _report_census, _select_tier
from tools.order_grain_null import (
    FENCED_OUT_DIR,
    OUT_DIR,
    PREMIUM_FLOOR,
    _Joined,
    fenced_orders,
)
from tools.root_classification_build import RootClassification

# --------------------------------------------------------------------------
# a synthetic G.8 snapshot: one equity, one index, one test root, one rename
# --------------------------------------------------------------------------

SNAPSHOT = {
    "schema_version": 1,
    "test_list": {"directory": ["TSTA"], "opra_only": []},
    "index_list": {"index_roots": ["IDXA"], "additions": []},
    "directory_class": {"EQA": "equity", "NEWA": "equity", "TSTA": "equity"},
    "delisted": {},
    "dotted_map": {},
    "adjusted_rule": {"regex": r"[0-9]+$"},
    "successor_map": [{"old": "OLDA", "new": "NEWA", "process_date": "2026-01-15"}],
    "reuse_splits": {},
}
ROOTS = ["EQA", "IDXA", "NEWA", "OLDA", "TSTA"]
SESSION = date(2026, 3, 2)

TCBBO_DTYPE = np.dtype([("ts_event", "i8"), ("instrument_id", "i8"),
                        ("price", "i8"), ("size", "u4"),
                        ("bid_px_00", "i8"), ("ask_px_00", "i8")])


def _classification() -> RootClassification:
    return RootClassification(SNAPSHOT)


def _prints(rows: list[tuple]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """rows: (root_idx, right, ts, iid, price$, size, bid$, ask$) -> arr, und, right."""
    arr = np.zeros(len(rows), dtype=TCBBO_DTYPE)
    und = np.zeros(len(rows), np.int64)
    right = np.zeros(len(rows), np.int8)
    for i, (u, r, ts, iid, px, sz, bid, ask) in enumerate(rows):
        und[i], right[i] = u, r
        arr["ts_event"][i], arr["instrument_id"][i] = ts, iid
        arr["price"][i], arr["size"][i] = int(px * PX), sz
        arr["bid_px_00"][i] = int(bid * PX) if bid is not None else UNDEF
        arr["ask_px_00"][i] = int(ask * PX) if ask is not None else UNDEF
    return arr, und, right


def _joined(arr, und, right, dte: int = 45, strike: float = 100.0):
    n = len(arr)
    return _Joined(arr=arr, und_code=und, uniq_und=np.array(ROOTS),
                   right=right, strike=np.full(n, strike),
                   dte=np.full(n, dte, np.int32), n_prints=n, n_sentinel=0)


# tight quote: mid 1.00, half-spread 0.05 -> (ask-bid)/mid = 0.10, inside 0.50
TIGHT = (0.95, 1.05)
# wide quote: mid 1.00, (ask-bid)/mid = 1.00 -> fails SPREAD_MAX_FRAC
WIDE = (0.50, 1.50)


def _mini_session():
    """Nine prints, one per registered exclusion plus the two that survive."""
    b, a = TIGHT
    wb, wa = WIDE
    rows = [
        (0, 0, 1, 10, 1.02, 10, b, a),      # 0 EQA call BUY, tight        -> keep, bull
        (0, 1, 2, 11, 0.98, 10, b, a),      # 1 EQA put SELL, tight        -> keep, bull
        (0, 0, 3, 12, 1.02, 10, wb, wa),    # 2 EQA wide spread            -> DROP (clause 1)
        (0, 0, 4, 13, 1.02, 10, b, a),      # 3 EQA package-coded print    -> DROP (F.1)
        (0, 0, 5, 14, 1.00, 10, b, a),      # 4 EQA at the mid             -> DROP (unclassified)
        (1, 0, 6, 15, 1.02, 10, b, a),      # 5 IDXA index root            -> DROP (universe)
        (4, 0, 7, 16, 1.02, 10, b, a),      # 6 TSTA test root             -> DROP (universe)
        (3, 0, 8, 17, 0.98, 10, b, a),      # 7 OLDA call SELL             -> keep as NEWA, bear
        (2, 0, 8, 18, 1.02, 10, b, a),      # 8 NEWA call BUY              -> keep, bull
    ]
    arr, und, right = _prints(rows)
    ml = np.zeros(len(rows), np.int64)
    ml[3] = 1
    return arr, und, right, ml


def test_fence_excludes_package_wide_and_non_equity():
    arr, und, right, ml = _mini_session()
    res = fenced_orders(SESSION, _joined(arr, und, right), ml, {}, _classification())

    assert list(res.names) == ["EQA", "NEWA"], "index/test roots must not enter the panel"
    # OLDA resolves onto NEWA, so the two identities carry 2 and 2 orders.
    assert len(res.p_all) == 4
    prem = {n: res.p_all[res.und == i].sum() for i, n in enumerate(res.names)}
    # EQA: only prints 0 and 1 survive -> (1.02 + 0.98) * 10 * 100
    assert prem["EQA"] == pytest.approx(2000.0)
    # NEWA: prints 7 (OLDA) and 8 -> (0.98 + 1.02) * 10 * 100
    assert prem["NEWA"] == pytest.approx(2000.0)
    # bull: EQA both legs (call buy + put sell); NEWA only print 8.
    bull = {n: res.b_all[res.und == i].sum() for i, n in enumerate(res.names)}
    assert bull["EQA"] == pytest.approx(2000.0)
    assert bull["NEWA"] == pytest.approx(1020.0)
    assert real_tilt(np.array([prem["NEWA"]]), np.array([bull["NEWA"]]))[0] == \
        pytest.approx((1020.0 - 980.0) / 2000.0)


def test_wide_gate_is_the_registered_threshold():
    """The dropped wide print is dropped BY the clause-1 gate, not by luck."""
    b, a = WIDE
    assert (a - b) > SPREAD_MAX_FRAC * (a + b) / 2.0
    tb, ta = TIGHT
    assert (ta - tb) <= SPREAD_MAX_FRAC * (ta + tb) / 2.0


def test_package_print_alone_removes_its_order():
    """A package-coded print is masked per PRINT, before orders are re-grouped."""
    arr, und, right, ml = _mini_session()
    rc = _classification()
    with_pkg = fenced_orders(SESSION, _joined(arr, und, right),
                             np.zeros(len(arr), np.int64), {}, rc)
    without = fenced_orders(SESSION, _joined(arr, und, right), ml, {}, rc)
    assert len(with_pkg.p_all) == len(without.p_all) + 1
    assert with_pkg.p_all.sum() - without.p_all.sum() == pytest.approx(1020.0)


def test_unresolvable_reuse_segment_is_excluded_not_merged():
    """G.2: a reuse segment with no chain of its own is EXCLUDED, never merged
    into the ticker's later occupant. ARIS is exactly this shape on the frozen
    snapshot — `resolve()` hands back `ARIS` for BOTH issuers."""
    snap = dict(SNAPSHOT, reuse_splits={"EQA": ["2026-02-19"]})
    rc = RootClassification(snap)
    assert rc.resolve("EQA", "2025-09-01") == rc.resolve("EQA", "2026-03-02") == "EQA"

    b, a = TIGHT
    arr, und, right = _prints([(0, 0, 1, 10, 1.02, 10, b, a)])
    ml = np.zeros(1, np.int64)
    # pre-seam: the vacated issuer's segment — no chain, so excluded outright
    assert fenced_orders(date(2025, 9, 1), _joined(arr, und, right), ml, {}, rc) is None
    # post-seam: the current occupant's own segment still enters normally
    post = fenced_orders(date(2026, 3, 2), _joined(arr, und, right), ml, {}, rc)
    assert list(post.names) == ["EQA"] and post.p_all.sum() == pytest.approx(1020.0)
    # and without the split registered, both segments are ordinary name-days
    plain = fenced_orders(date(2025, 9, 1), _joined(arr, und, right), ml, {},
                          _classification())
    assert list(plain.names) == ["EQA"]


def test_orphan_premium_is_measured_after_the_package_mask():
    """The two provenance counts each sit at their OWN fence step: the package
    mask precedes the universe fence, so an orphan print that was ALSO
    package-coded is not counted twice."""
    snap = dict(SNAPSHOT, reuse_splits={"EQA": ["2026-02-19"]})
    rc = RootClassification(snap)
    b, a = TIGHT
    arr, und, right = _prints([
        (0, 0, 1, 10, 1.02, 10, b, a),      # orphan EQA, survives the package mask
        (0, 0, 2, 11, 1.02, 10, b, a),      # orphan EQA, package-coded
        (2, 0, 3, 12, 1.02, 10, b, a),      # NEWA, package-coded -> masked
        (2, 0, 4, 13, 1.02, 10, b, a),      # NEWA, survives
    ])
    ml = np.array([0, 1, 1, 0], np.int64)
    res = fenced_orders(date(2025, 9, 1), _joined(arr, und, right), ml, {}, rc)
    assert list(res.names) == ["NEWA"]
    assert res.orphan_roots.tolist() == ["EQA"]
    # only the ml == 0 orphan print, not both
    assert res.orphan_premium == pytest.approx(1020.0)
    # and the package mask counts only what would OTHERWISE have survived —
    # the package-coded orphan is not in the universe, so it is not counted here
    assert res.package_masked_prints == 1


def test_spot_is_read_at_the_terminal_symbol():
    """G.2: PRICE/BARS reads are NOT date-scoped — the moneyness spot comes from
    the identity's terminal symbol, not the printed root. Under the printed root
    the bar is absent and the print lands in `no_spot_missing`, which silently
    empties the SHORT_OTM arm."""
    b, a = TIGHT
    arr, und, right = _prints([(3, 0, 1, 10, 1.02, 10, b, a)])   # OLDA -> NEWA
    ml = np.zeros(1, np.int64)
    j = _joined(arr, und, right, dte=10, strike=120.0)           # 20% OTM call

    at_terminal = fenced_orders(date(2025, 9, 1), j, ml,
                                {"NEWA": (100.0, "2025-08-29")}, _classification())
    assert at_terminal.p_so.sum() == pytest.approx(1020.0)

    at_printed_root = fenced_orders(date(2025, 9, 1), j, ml,
                                    {"OLDA": (100.0, "2025-08-29")}, _classification())
    assert at_printed_root.p_all.sum() == pytest.approx(1020.0)
    assert at_printed_root.p_so.sum() == 0.0, "no spot -> unbucketable -> no arm"


def test_all_prints_fenced_out_returns_none():
    b, a = TIGHT
    arr, und, right = _prints([(1, 0, 1, 10, 1.02, 10, b, a)])   # index only
    assert fenced_orders(SESSION, _joined(arr, und, right),
                         np.zeros(1, np.int64), {}, _classification()) is None


# --------------------------------------------------------------------------
# the registered panel and its judgment mask
# --------------------------------------------------------------------------

# 2026 NYSE holidays inside the judgment window: Juneteenth (06-19) and the
# observed Independence Day (07-03, since 07-04 is a Saturday).
HOLIDAYS = {date(2026, 6, 19), date(2026, 7, 3)}


def _fake_sessions(n_before: int = 80) -> list[date]:
    out = []
    d = date(2026, 6, 1)
    while d <= date(2026, 7, 24):
        if d.weekday() < 5 and d not in HOLIDAYS:
            out.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    d = date(2026, 5, 29)
    before = []
    while len(before) < n_before:
        if d.weekday() < 5:
            before.append(d)
        d = date.fromordinal(d.toordinal() - 1)
    return sorted(before) + out


def test_registered_panel_is_exactly_60_lead_in_and_37_dates():
    sessions = _fake_sessions()
    panel, mask = registered_panel(sessions)
    assert int(mask.sum()) == JUDGMENT_DATES == 37
    # EXACTLY 60 sessions precede the anchor — the panel start defines the
    # per-session random stream, so ">= 60" would be a different table.
    assert sum(s < date(2026, 6, 1) for s in panel) == LEAD_IN_SESSIONS
    assert panel[LEAD_IN_SESSIONS] == date(2026, 6, 1)
    # 07-23 IS a panel session (it feeds trailing windows) and is masked OUT.
    assert len(panel) == LEAD_IN_SESSIONS + JUDGMENT_DATES + 1
    assert panel[-1] == date(2026, 7, 24)
    assert date(2026, 7, 23) in panel
    assert not mask[[s == date(2026, 7, 23) for s in panel].index(True)]


def test_registered_panel_refuses_a_short_lead_in():
    with pytest.raises(SystemExit, match="lead-in short"):
        registered_panel(_fake_sessions(n_before=40))


# --------------------------------------------------------------------------
# the floor gates the EVENT day, never presence
# --------------------------------------------------------------------------


def _floor_panel():
    """One name: 20 thin sessions, then one $500k session with a huge tilt."""
    n_days = 21
    prem = np.full((1, n_days), 1_000.0)
    prem[0, -1] = 500_000.0
    tilt = np.zeros((1, n_days))
    tilt[0, :-1] = np.where(np.arange(n_days - 1) % 2 == 0, 0.01, -0.01)
    tilt[0, -1] = 1.0
    return prem, tilt


def test_floor_gates_the_event_day_not_presence():
    prem, tilt = _floor_panel()
    present = prem > 0                       # ANY classified-premium day
    eligible = prem >= PREMIUM_FLOOR         # the $250k EVENT-day floor
    fired, tested = _fire(tilt, present, eligible)
    assert tested[0, -1] and fired[0, -1], "the thin days must still count as presence"

    # The failure mode G.2 names by hand: applying the floor to presence too.
    fired_bad, tested_bad = _fire(tilt, eligible, eligible)
    assert not tested_bad.any() and not fired_bad.any()


# --------------------------------------------------------------------------
# the judgment mask is the OPERATIVE mask
# --------------------------------------------------------------------------


def _tilt_panel(fire_day: int, n_days: int = 25):
    tilt = np.where(np.arange(n_days) % 2 == 0, 0.01, -0.01)[None, :].repeat(1, 0)
    tilt[0, fire_day] = 1.0
    return tilt


def test_judgment_mask_is_the_per_draw_mask():
    n_days = 25
    tilt = _tilt_panel(fire_day=22, n_days=n_days)
    tilts = np.repeat(tilt[None, :, :], 3, axis=0)
    present = np.ones((1, n_days), bool)
    eligible = np.ones((1, n_days), bool)
    quint = np.full((1, n_days), 4, np.int8)
    judgment = np.zeros(n_days, bool)
    judgment[20:22] = True                    # deliberately excludes the firing day
    masks = {"judgment": judgment, "full": np.ones(n_days, bool)}

    on_j = null_table(tilts, present, eligible, quint, masks, "judgment", 3)
    on_f = null_table(tilts, present, eligible, quint, masks, "full", 3)
    assert on_j["f0"][4] == 0.0 and on_j["se"][4] == 0.0
    assert on_f["f0"][4] > 0.0
    # the tallies themselves are identical — only the OPERATIVE mask moved
    assert (on_j["acc"]["full"][0] == on_f["acc"]["full"][0]).all()
    # distinct tested name-days are counted on the operative mask
    assert on_j["distinct_tested"][4] == 2
    assert on_f["distinct_tested"][4] == n_days - 20


def test_distinct_tested_is_the_union_across_draws():
    n_days = 25
    present = np.ones((2, n_days), bool)
    eligible = np.ones((2, n_days), bool)
    quint = np.full((2, n_days), 4, np.int8)
    masks = {"full": np.ones(n_days, bool)}
    # draw 0 tests only name 0 (name 1 has a flat, zero-std history), draw 1 the
    # reverse; the union must be both, never either draw's count.
    tilts = np.zeros((2, 2, n_days))
    tilts[0, 0] = np.where(np.arange(n_days) % 2 == 0, 0.01, -0.01)
    tilts[1, 1] = np.where(np.arange(n_days) % 2 == 0, 0.01, -0.01)
    out = null_table(tilts, present, eligible, quint, masks, "full", 2)
    per_draw = out["per_draw_tested"][:, 4]
    assert per_draw.tolist() == [n_days - 20, n_days - 20]
    assert out["distinct_tested"][4] == 2 * (n_days - 20)


# --------------------------------------------------------------------------
# the shuffle is per ORDER and ±1
# --------------------------------------------------------------------------


def _write_npz(path: Path, day: str, orders: list[tuple[str, float, float]]):
    names = sorted({n for n, *_ in orders})
    ni = {n: i for i, n in enumerate(names)}
    path.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path / f"{day}.npz",
        und=np.array([ni[n] for n, *_ in orders], np.int32),
        p_all=np.array([p for _, p, _ in orders], np.float64),
        p_so=np.zeros(len(orders)),
        b_all=np.array([b for *_, b in orders], np.float64),
        b_so=np.zeros(len(orders)),
        names=np.array(names))


def _panel_files(tmp_path: Path, per_day_orders, n_days: int = 5) -> list[Path]:
    days = [f"2026-06-{i + 1:02d}" for i in range(n_days)]
    for d in days:
        _write_npz(tmp_path, d, per_day_orders)
    return [tmp_path / f"{d}.npz" for d in days]


def test_shuffle_is_per_order_and_plus_minus_one(tmp_path):
    # ONE order per day: an exact per-order flip can only produce tilt = ±1.
    files = _panel_files(tmp_path, [("AAA", 1000.0, 1000.0)])
    names, ni = _name_index(files)
    _P, _Q, _B, T = _build(files, len(names), ni, draws=8)
    vals = np.unique(np.round(T["ALL"], 12))
    assert set(vals.tolist()) <= {-1.0, 1.0}
    assert set(vals.tolist()) == {-1.0, 1.0}, "both signs must appear over 8 draws"


def test_shuffle_is_not_an_aggregate_draw(tmp_path):
    # TWO equal orders on one name-day: per-order flips give exactly {-1, 0, +1};
    # an aggregate normal draw (A2's forbidden approximation) would give a
    # continuum, and a single per-NAME-DAY flip would never give 0.
    files = _panel_files(tmp_path, [("AAA", 500.0, 500.0), ("AAA", 500.0, 0.0)])
    names, ni = _name_index(files)
    _P, _Q, _B, T = _build(files, len(names), ni, draws=12)
    vals = set(np.unique(np.round(T["ALL"], 12)).tolist())
    assert vals == {-1.0, 0.0, 1.0}


def test_real_tilt_comes_off_the_same_orders_as_the_null(tmp_path):
    files = _panel_files(tmp_path, [("AAA", 800.0, 600.0), ("AAA", 200.0, 0.0)])
    names, ni = _name_index(files)
    P, _Q, B, _T = _build(files, len(names), ni, draws=1)
    # bull 600 of 1000 -> tilt = (600 - 400) / 1000
    assert real_tilt(P["ALL"], B["ALL"])[0, 0] == pytest.approx(0.2)


def test_real_table_reports_per_date_series_on_the_operative_mask():
    n_days = 25
    tilt = _tilt_panel(fire_day=22, n_days=n_days)
    present = np.ones((1, n_days), bool)
    eligible = np.ones((1, n_days), bool)
    quint = np.full((1, n_days), 4, np.int8)
    judgment = np.zeros(n_days, bool)
    judgment[20:] = True
    out = real_table(tilt, present, eligible, quint, {"judgment": judgment}, "judgment")
    assert out["per_date_fired"].shape == (5, int(judgment.sum()))
    assert out["fired"][4] == 1
    assert out["per_date_fired"][4].sum() == 1
    assert out["per_date_tested"][4].tolist() == [1] * int(judgment.sum())


# --------------------------------------------------------------------------
# 3b's tie-break and the census guards
# --------------------------------------------------------------------------


def test_census_tie_breaks_toward_the_higher_quintile():
    minima = [0, 20, 20, 5, 0]
    totals = [0, 300, 300, 90, 0]
    assert _select_tier(minima, totals, higher_wins=True) == 2
    # 2.4's own recorded comparator kept the lower tier — unchanged.
    assert _select_tier(minima, totals, higher_wins=False) == 1
    # a strictly better tier still wins outright either way
    assert _select_tier([0, 20, 20, 25, 0], totals, higher_wins=True) == 3


def test_census_tie_break_uses_total_events_before_the_quintile():
    """Key order ADJUDICATED 2026-07-31: max MIN -> max total events -> higher
    quintile. 2.4's registered rule already carried the total-events tie-break,
    G.10 #3b inherits 2.4's form by name, and re-ordering the keys now would be
    the post-hoc selection-rule edit G.10 forbids — so the higher-quintile pin
    applies to RESIDUAL ties only. Q1 wins here on total events despite Q2 tying
    on the first key."""
    minima = [0, 20, 20, 0, 0]
    totals = [0, 400, 300, 0, 0]
    assert _select_tier(minima, totals, higher_wins=True) == 1


def test_degenerate_census_refuses_to_select_a_tier():
    """All-zero minima would hand back Q5 under higher_wins and 'confirm' the
    pre-stated Q5 expectation out of a census that counted nothing."""
    with pytest.raises(SystemExit, match="degenerate census"):
        _select_tier([0] * 5, [0] * 5, higher_wins=True)
    with pytest.raises(SystemExit, match="degenerate census"):
        _select_tier([0] * 5, [10, 0, 0, 0, 0], higher_wins=False)


def test_report_census_records_the_applied_key_order(capsys):
    ev = [(f"N{i}", f"2026-01-{d:02d}", 4, 0.5, 90.0 if d % 2 else 10.0)
          for i in range(3) for d in range(1, 25)]
    out = _report_census(["N0", "N1", "N2"], ["2026-01-01", "2026-01-24"],
                         ev, 0, higher_wins=True, label="census span")
    assert out["key_order"] == KEY_ORDER
    assert out["higher_quintile_breaks_residual_ties"] is True
    assert out["tier"] == 4 and out["f_open_ranked_share"] == 1.0
    assert KEY_ORDER in capsys.readouterr().out


# --------------------------------------------------------------------------
# the missing-session guard (a missing npz shifts every draw)
# --------------------------------------------------------------------------


def test_require_cache_refuses_a_partial_panel(tmp_path):
    (tmp_path / "2026-06-01.npz").write_bytes(b"")
    files = [tmp_path / "2026-06-01.npz", tmp_path / "2026-06-02.npz"]
    with pytest.raises(SystemExit, match="panel sessions missing"):
        require_cache(files, tmp_path)
    require_cache(files[:1], tmp_path)


def test_registered_run_refuses_a_partial_panel(tmp_path):
    days = [date(2026, 4, 1) + timedelta(days=i) for i in range(30)]
    days = [d for d in days if d.weekday() < 5]
    cache = _synthetic_cache(tmp_path, days[:-1])          # last session absent
    mask = np.array([i >= len(days) - 5 for i in range(len(days))])
    with pytest.raises(SystemExit, match="panel sessions missing"):
        registered_run(cache_dir=cache, draws=2, arms=("ALL",), panel=(days, mask))


def test_registered_census_refuses_a_partial_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ocs, "_sessions",
                        lambda _n: [date(2025, 7, 28), date(2025, 7, 29)])
    with pytest.raises(SystemExit, match="panel sessions missing"):
        ocs._registered_events(tmp_path)


# --------------------------------------------------------------------------
# G.9b's bootstrap
# --------------------------------------------------------------------------


def test_circular_blocks_wrap_and_truncate():
    rng = np.random.default_rng(0)
    idx = circular_blocks(rng, g=37, reps=500, block=10)
    assert idx.shape == (500, 37)
    assert idx.min() >= 0 and idx.max() < 37
    # a block starting near the end must wrap rather than be discarded
    wraps = (np.diff(idx[:, :10], axis=1) < 0).any(axis=1)
    assert wraps.any()
    # within a block the positions are consecutive modulo g
    step = (np.diff(idx[:, :10], axis=1) % 37)
    assert (step == 1).all()


def test_dilution_precision_shapes_and_reproducibility():
    rng = np.random.default_rng(7)
    tested_d = rng.integers(40, 60, size=12)
    fired_d = rng.binomial(tested_d, 0.09)
    draw_t = np.full(20, 600)
    draw_f = rng.binomial(draw_t, 0.06)
    kw = {"reps": 4000, "seed": 20260812}

    out = dilution_precision(fired_d, tested_d, draw_f, draw_t, **kw)
    again = dilution_precision(fired_d, tested_d, draw_f, draw_t, **kw)
    assert out == again, "the registered seed must reproduce the record exactly"
    assert out["event_dates"] == 12
    assert out["f"] == pytest.approx(fired_d.sum() / tested_d.sum())
    assert out["f0"] == pytest.approx(np.mean(draw_f / draw_t))
    assert out["mrd_pt"] > 0
    assert out["lcb"] < out["diff"]
    # MRD is the distance from the point estimate down to the 1st percentile
    assert out["mrd_pt"] == pytest.approx((out["diff"] - out["lcb"]) * 100.0)


def test_dilution_precision_drops_dateless_tiers():
    z = np.zeros(5, np.int64)
    assert dilution_precision(z, z, np.zeros(20, np.int64),
                              np.zeros(20, np.int64), reps=100) is None


def test_dilution_precision_sharpens_with_more_dates():
    """More event dates must shrink the resolvable difference — a sanity pin on
    the block bootstrap actually consuming the series it is handed."""
    rng = np.random.default_rng(3)
    kw = {"reps": 4000, "seed": 20260812}
    draw_t = np.full(20, 600)
    draw_f = rng.binomial(draw_t, 0.06)
    tested = np.full(120, 50)
    fired = rng.binomial(tested, 0.08)
    small = dilution_precision(fired[:37], tested[:37], draw_f, draw_t, **kw)
    big = dilution_precision(fired, tested, draw_f, draw_t, **kw)
    assert big["boot_sd_pt"] < small["boot_sd_pt"]
    assert big["mrd_pt"] < small["mrd_pt"]


def test_block_bootstrap_is_degenerate_when_G_does_not_exceed_the_block():
    """A disclosed property of G.9b's assembly, pinned so it cannot surprise
    anyone reading the record: at G <= 10 there is exactly ONE circular block,
    so every replicate is a rotation of the same series and `f` contributes NO
    resampling variance — the tier's precision is then f₀'s alone. The judgment
    window is registered at 37 dates, where 4 blocks are drawn."""
    rng = np.random.default_rng(11)
    tested = np.full(8, 50)
    fired = rng.binomial(tested, 0.08)
    idx = circular_blocks(np.random.default_rng(0), g=8, reps=200, block=10)
    assert idx.shape == (200, 8)
    assert {tuple(sorted(r)) for r in idx.tolist()} == {tuple(range(8))}
    out = dilution_precision(fired, tested, np.full(20, 30), np.full(20, 600),
                             reps=2000, seed=20260812)
    assert out["event_dates"] == 8


# --------------------------------------------------------------------------
# end-to-end smoke: the whole registered chain on a synthetic fenced cache
# --------------------------------------------------------------------------


def _synthetic_cache(tmp_path: Path, days: list[date], n_names: int = 8,
                     seed: int = 5, stamps: bool = False) -> Path:
    """A fenced-shaped cache: per-order p_all/p_so/b_all/b_so plus identities."""
    rng = np.random.default_rng(seed)
    names = np.array([f"N{i:02d}" for i in range(n_names)])
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    for k, d in enumerate(days):
        n_orders = int(rng.integers(3 * n_names, 6 * n_names))
        und = rng.integers(0, n_names, size=n_orders).astype(np.int32)
        # a fat cross-section so trailing-60 quintiles actually separate names
        p = rng.lognormal(mean=12.0, sigma=1.5, size=n_orders) * (1 + und)
        b = p * rng.random(n_orders)
        extra = {}
        if stamps:
            extra = {"orphan_roots": np.array(["ARIS"] if k == 0 else [], dtype="U16"),
                     "orphan_premium": np.float64(1e6 if k == 0 else 0.0),
                     "package_masked_prints": np.int64(10 + k),
                     "ml_source": np.array("codes"),
                     "join_rate": np.float64(0.97 if k == 0 else 0.99),
                     "label_coverage": np.float64(0.95),
                     "bars_max_date": np.array("2026-07-30"),
                     "bars_rows": np.int64(1_234_567)}
        np.savez_compressed(cache / f"{d.isoformat()}.npz", und=und, p_all=p,
                            p_so=p * 0.4, b_all=b, b_so=b * 0.4, names=names,
                            **extra)
    return cache


def test_cache_provenance_travels_with_the_table(tmp_path):
    """Addendum F's >=98% subset is selected on `join_rate`, and a pre-backfill
    extract is only detectable from `bars_max_date` — so both have to reach the
    record rather than living in a log nobody keeps."""
    days = [d for d in (date(2026, 4, 1) + timedelta(days=i) for i in range(30))
            if d.weekday() < 5]
    cache = _synthetic_cache(tmp_path, days, stamps=True)
    prov = _cache_provenance([cache / f"{d.isoformat()}.npz" for d in days])
    assert prov["stamped_sessions"] == len(days)
    assert prov["multileg_sources"] == ["codes"]
    assert prov["sessions_below_join_floor"] == 1
    assert prov["join_rate_min"] == pytest.approx(0.97)
    assert prov["orphan_roots"] == ["ARIS"]
    assert prov["orphan_premium_usd"] == pytest.approx(1e6)
    assert prov["package_masked_prints"] == sum(10 + k for k in range(len(days)))
    assert prov["bars_max_dates"] == ["2026-07-30"]
    # an UNSTAMPED cache is reported as unstamped, never as clean
    plain = _synthetic_cache(tmp_path / "plain", days)
    assert _cache_provenance([plain / f"{d.isoformat()}.npz"
                              for d in days]) == {"stamped_sessions": 0}


def test_registered_chain_end_to_end(tmp_path):
    days = [date(2026, 4, 1) + timedelta(days=i)
            for i in range(45)]
    days = [d for d in days if d.weekday() < 5]
    cache = _synthetic_cache(tmp_path, days)
    mask = np.array([i >= len(days) - 10 for i in range(len(days))])

    res = registered_run(cache_dir=cache, draws=4, arms=("ALL",),
                         panel=(days, mask), day_floor=0.0)
    rec = res["arms"]["ALL"]
    assert rec["null"]["per_draw_fired"].shape == (4, 5)
    assert rec["null"]["f0"].shape == (5,)
    assert rec["real"]["per_date_fired"].shape == (5, int(mask.sum()))
    # the floor is off here, so every tier that has tested name-days has them on
    # BOTH sides — the null and the real rate must live on the same universe.
    assert (rec["real"]["tested"] > 0).any()
    for i in range(5):
        if rec["real"]["tested"][i] == 0:
            continue
        assert rec["null"]["acc"]["judgment"][1][i] > 0

    gate = [dilution_precision(rec["real"]["per_date_fired"][i],
                               rec["real"]["per_date_tested"][i],
                               rec["null"]["per_draw_fired"][:, i],
                               rec["null"]["per_draw_tested"][:, i], reps=2000)
            for i in range(5)]
    assert any(g is not None for g in gate)
    for g in gate:
        assert g is None or np.isfinite(g["mrd_pt"])

    # the printed record and the JSON record are the run's only artefacts, and
    # NaN tiers are expected under the floor — exercise both so a formatting or
    # serialisation fault cannot surface for the first time in the one-shot run.
    _print_arm("ALL", rec, gate)
    blob = json.dumps(_jsonable({"arms": {"ALL": rec}, "gate": gate}))
    assert "NaN" not in blob and "Infinity" not in blob


def test_tilt_dependence_end_to_end(tmp_path):
    days = [date(2025, 9, 1) + timedelta(days=i)
            for i in range(60)]
    days = [d for d in days if d.weekday() < 5]
    cache = _synthetic_cache(tmp_path, days, n_names=6)
    out = tilt_dependence(cache_dir=cache, draws=3, sessions=days)
    assert out["sessions"] == len(days)
    assert out["names_quoted"] == 6
    for side in ("real", "null"):
        for k in ("ar1", "dispersion"):
            assert np.isfinite(out[side][k]["median"])
    # the null is an iid coin flip per order, so its per-name tilt series must
    # carry ~no autocorrelation — that is the confound the record exists to size.
    assert abs(out["null"]["ar1"]["median"]) < 0.35


# --------------------------------------------------------------------------
# CLI guards, the record's overwrite guard, and the identity re-keying
# --------------------------------------------------------------------------


def _run_cli(monkeypatch, argv: list[str]):
    monkeypatch.setattr(sys, "argv", ["order_grain_null.py", *argv])
    ognull.main()


def test_cache_dir_without_fenced_is_refused(monkeypatch):
    with pytest.raises(SystemExit, match="fenced path's cache"):
        _run_cli(monkeypatch, ["--extract", "--cache-dir", "/tmp/x"])


def test_fenced_without_extract_is_refused(monkeypatch):
    with pytest.raises(SystemExit, match="extract-only"):
        _run_cli(monkeypatch, ["--fenced"])


def test_fenced_extract_never_touches_the_unfenced_cache(monkeypatch, tmp_path):
    """The versioned cache is the whole point of G.9c's 'never silently reuse
    unfenced npz' — so the fenced route must not call the v6 extractor at all."""
    assert FENCED_OUT_DIR != OUT_DIR
    seen: list[tuple] = []
    monkeypatch.setattr(ognull, "_sessions", lambda _n: [date(2026, 6, 1)])
    monkeypatch.setattr(ognull, "_extract_fenced",
                        lambda s, out_dir: seen.append(("fenced", s, out_dir)))
    monkeypatch.setattr(ognull, "_extract",
                        lambda s: seen.append(("v6", s)))
    _run_cli(monkeypatch, ["--extract", "--fenced", "--cache-dir", str(tmp_path)])
    assert seen == [("fenced", date(2026, 6, 1), tmp_path)]

    seen.clear()
    _run_cli(monkeypatch, ["--extract"])
    assert seen == [("v6", date(2026, 6, 1))]


def test_record_refuses_to_overwrite_without_force(tmp_path):
    out = tmp_path / "flow_obligation3_record.json"
    write_record({"a": 1}, out)
    assert json.loads(out.read_text()) == {"a": 1}
    with pytest.raises(SystemExit, match="first-run-governs"):
        write_record({"a": 2}, out)
    write_record({"a": 2}, out, force=True)
    assert json.loads(out.read_text()) == {"a": 2}


def _oi_db(path: Path, rows: list[tuple]) -> Path:
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE oi_reconcile_rollup (session TEXT, underlying TEXT, "
                "volume INTEGER, vol_weighted_f_open REAL, dte_bucket TEXT, right TEXT)")
    con.executemany("INSERT INTO oi_reconcile_rollup VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return path


def test_f_open_pct_rekeys_onto_resolved_identities(tmp_path, monkeypatch):
    """The OI table stores the PRINTED root. Without re-keying, every renamed
    name-day reads as unmatched against an identity-keyed panel; and a seam
    session must rank the identity ONCE, not twice."""
    db = _oi_db(tmp_path / "oi.db", [
        ("2026-01-15", "OLDA", 600, 0.9, "30-89", "C"),
        ("2026-01-15", "NEWA", 600, 0.5, "30-89", "C"),
        ("2026-01-15", "EQA", 4000, 0.1, "30-89", "C"),
    ])
    monkeypatch.setattr(ocs, "OI_DB", db)
    rc = _classification()

    raw = ocs._f_open_pct()
    assert ("2026-01-15", "OLDA") not in raw, "600 < the 1,000-contract floor"

    keyed = ocs._f_open_pct(resolve=ocs._cached_resolver(rc))
    # OLDA + NEWA aggregate into ONE identity that now clears the floor
    assert ("2026-01-15", "NEWA") in keyed
    assert ("2026-01-15", "OLDA") not in keyed
    assert len([k for k in keyed if k[0] == "2026-01-15"]) == 2
    assert keyed[("2026-01-15", "NEWA")] > keyed[("2026-01-15", "EQA")]


def test_cached_resolver_matches_the_uncached_one():
    rc = _classification()
    cached = ocs._cached_resolver(rc)
    for root in ROOTS:
        for ds in ("2025-09-01", "2026-01-15", "2026-06-30"):
            assert cached(root, ds) == rc.resolve(root, ds)


def test_cached_resolver_calls_through_for_split_roots():
    """A root WITH a reuse-split boundary is date-VARYING, so it must never be
    served from the by-root cache."""
    rc = RootClassification(dict(SNAPSHOT, reuse_splits={"OLDA": ["2026-01-15"]}))
    cached = ocs._cached_resolver(rc)
    assert cached("OLDA", "2025-09-01") == rc.resolve("OLDA", "2025-09-01")
    assert cached("OLDA", "2026-06-30") == rc.resolve("OLDA", "2026-06-30")
    assert rc.split_boundaries("OLDA") == ["2026-01-15"]
    assert rc.split_boundaries("EQA") == []


def test_trailing_quintiles_retain_short_lookback_dates():
    """G.4: dates whose trailing-60 lookback is short are RETAINED, not dropped."""
    prem = np.tile(np.arange(1, 11, dtype=float)[:, None], (1, 30)) * 1e5
    quint = _trailing_quintiles(prem)
    assert (quint[:, 0] == -1).all(), "day 0 has no trailing window at all"
    assert (quint[:, 1] >= 0).all(), "day 1 has one day of history and is kept"
