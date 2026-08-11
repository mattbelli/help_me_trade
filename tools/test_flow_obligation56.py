"""Addendum G obligation 5/6 + scoring-run pins, hermetically.

Every fixture is synthetic. What it pins (each a registered clause a
plausible implementation gets wrong silently):

  * `_signed_z` reproduces `order_grain_null._fire`'s fired/tested masks
    EXACTLY (the one-implementation runtime assert has teeth), and its sign
    is the sign of z — tilt minus the trailing-20 mean — never sign(tilt),
  * the baseline excludes fired name-days, excludes exact-zero-z (counted),
    and stays inside the selected tier,
  * a date whose baseline arm has no gradeable member leaves the series and
    is COUNTED; its ungradeable members' premium lands in the census,
  * event observations are signed by sign(z): a positive tilt BELOW its
    trailing mean contributes with a NEGATIVE sign,
  * the verdict checklist: dilution fires on f <= f0 AND on a resolving
    gate whose LCB fails to exclude zero; DEAD notes carry the MDE beside
    the observed effect; UNEVALUABLE claims nothing further,
  * the calendar gate (>= 2026-08-08), first-run-governs stage refusal,
    obligation ordering, and score's refusal while the corpus census is
    skipped.
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

import tools.flow_obligation56 as m  # noqa: E402
from tools.h1_quiet_tape import Panel  # noqa: E402
from tools.order_grain_null import _fire  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    """No test may touch the real governing record, verdict artifact,
    census cache, or obligation-3 record (script-mains lesson)."""
    monkeypatch.setattr(m, "RECORD_PATH", tmp_path / "rec.json")
    monkeypatch.setattr(m, "VERDICT_PATH", tmp_path / "verdict.json")
    monkeypatch.setattr(m, "CENSUS_CACHE", tmp_path / "census")
    monkeypatch.setattr(m, "OB3_RECORD", tmp_path / "ob3.json")

# ------------------------------ _signed_z vs _fire ------------------------------


def _rand_panel(n_names=12, n_days=60, seed=7):
    rng = np.random.default_rng(seed)
    tilt = rng.uniform(-1, 1, (n_names, n_days))
    present = rng.uniform(size=(n_names, n_days)) < 0.9
    eligible = rng.uniform(size=(n_names, n_days)) < 0.7
    return tilt, present, eligible


def test_signed_z_masks_equal_fire_exactly():
    tilt, present, eligible = _rand_panel()
    sign, fired, tested = m._signed_z(tilt, present, eligible)
    ref_fired, ref_tested = _fire(tilt, present, eligible)
    assert (fired == ref_fired).all()
    assert (tested == ref_tested).all()
    assert ((sign != 0) <= tested).all()          # sign only where tested


def test_sign_is_sign_of_z_not_sign_of_tilt():
    # One name, constant positive tilt 0.5 for 20 days, then a SMALLER
    # positive tilt: z < 0 while tilt > 0.
    n_days = 25
    tilt = np.full((1, n_days), 0.5)
    tilt[0, 20:] = 0.1
    # add tiny noise to the lookback so std > 0
    tilt[0, :20] += np.linspace(0, 1e-3, 20)
    present = np.ones((1, n_days), bool)
    eligible = np.ones((1, n_days), bool)
    sign, fired, tested = m._signed_z(tilt, present, eligible)
    assert tested[0, 21]
    assert tilt[0, 21] > 0
    assert sign[0, 21] == -1.0                    # below the trailing mean


# ------------------------------ CellMasks ------------------------------


def test_cellmasks_baseline_tier_and_zero_z(monkeypatch):
    tilt, present, eligible_unused = _rand_panel(n_names=10, n_days=50, seed=3)
    prem = np.abs(np.random.default_rng(4).normal(5e5, 2e5, tilt.shape))
    panel = Panel([date(2025, 7, 28) + timedelta(days=i) for i in range(50)],
                  np.array([f"N{i}" for i in range(10)]),
                  {f"N{i}": i for i in range(10)},
                  prem, tilt, present, present)
    masks = m.CellMasks(panel, tier=4)
    for j in range(25, 50):
        ev, ba, z0 = masks.events(j), masks.baseline(j), masks.baseline_zero_z(j)
        assert not (ev & ba).any()                # disjoint arms
        assert not (ba & ~masks.in_tier[:, j]).any()   # tier-restricted
        assert not (ba & (masks.sign[:, j] == 0)).any()  # zero-z excluded
        assert not (z0 & ba).any()


def test_cellmasks_blocks_on_fire_divergence(monkeypatch):
    tilt, present, _ = _rand_panel(n_names=6, n_days=40, seed=5)
    prem = np.full(tilt.shape, 3e5)
    panel = Panel([date(2025, 7, 28) + timedelta(days=i) for i in range(40)],
                  np.array([f"N{i}" for i in range(6)]),
                  {f"N{i}": i for i in range(6)},
                  prem, tilt, present, present)
    monkeypatch.setattr(m, "_fire",
                        lambda *a: (np.zeros(tilt.shape, bool),
                                    np.zeros(tilt.shape, bool)))
    with pytest.raises(SystemExit, match="one-implementation"):
        m.CellMasks(panel, tier=4)


# ------------------------------ build_cell_window ------------------------------


class FakeMasks:
    """Duck-typed masks giving the test exact control of both arms."""

    def __init__(self, n_names, n_days, tier=4):
        self.tier = tier
        self.sign = np.zeros((n_names, n_days))
        self.fired = np.zeros((n_names, n_days), bool)
        self.quint = np.full((n_names, n_days), 4, np.int8)
        self._ev = np.zeros((n_names, n_days), bool)
        self._ba = np.zeros((n_names, n_days), bool)

    def events(self, j):
        return self._ev[:, j]

    def baseline(self, j):
        return self._ba[:, j]

    def baseline_zero_z(self, j):
        return np.zeros(self._ev.shape[0], bool)


def _flat_bars(dates, level=100.0, last_close=None):
    n = len(dates)
    o = np.full(n, level)
    c = np.full(n, level)
    h = np.full(n, level + 0.5)
    low = np.full(n, level - 0.5)
    if last_close is not None:
        c[-1] = last_close
    return (list(dates), o, h, low, c)


@pytest.fixture
def tiny_world(monkeypatch):
    """Two panel sessions inside the TRAIN window + a 40-day SPY calendar."""
    n_days = 2
    sessions = [date(2025, 8, 25), date(2025, 8, 26)]
    cal = [(date(2025, 7, 28) + timedelta(days=i)).isoformat()
           for i in range(60)]
    # panel sessions must sit on the calendar with >= 15 bars before and
    # 10 after; 2025-08-25 is calendar index 28
    spy_o = np.full(60, 100.0)
    spy_c = np.full(60, 100.0)
    monkeypatch.setattr(m, "spy_calendar", lambda: (cal, spy_o, spy_c))
    monkeypatch.setattr(m, "check_benchmark_continuity", lambda *a: None)

    class RC:
        def occ_form(self, n):
            return None

        def directory_form(self, n):
            return None

    monkeypatch.setattr(m, "classification", lambda: RC())
    panel = Panel(sessions, np.array(["EVT", "BASE", "GONE", "OTHER"]),
                  {"EVT": 0, "BASE": 1, "GONE": 2, "OTHER": 3},
                  np.full((4, n_days), 1e6),
                  np.zeros((4, n_days)),
                  np.ones((4, n_days), bool),
                  np.ones((4, n_days), bool))
    masks = FakeMasks(4, n_days)
    return panel, masks, cal, monkeypatch


def test_series_signed_by_sign_z_and_both_arms_rule(tiny_world):
    panel, masks, cal, monkeypatch = tiny_world
    # date 0: EVT fires with sign -1 (positive-tilt-below-mean shape),
    # BASE in baseline sign +1. date 1: EVT fires, baseline arm EMPTY.
    masks._ev[0, :] = True
    masks.sign[0, :] = -1.0
    masks._ba[1, 0] = True
    masks.sign[1, 0] = 1.0
    # 5% up-move at exactly d+10 (2025-08-25 is calendar index 28, so the
    # 10td close reads index 38); ATR14 == 1 (constant range 1.0)
    evt = _flat_bars(cal, 100.0)
    evt[4][38] = 105.0
    bars = {"EVT": evt, "BASE": _flat_bars(cal, 100.0)}
    monkeypatch.setattr(m, "daily_bars", lambda names: bars)
    res = m.build_cell_window(panel, masks, "train", with_alpha=True)
    # both-arms rule: only date 0 enters the series
    assert res["dates"] == ["2025-08-25"]
    assert res["census"]["dates_dropped_empty_baseline_arm"] == 1
    assert res["census"]["event_dates_gradeable"] == 2
    # EVT: alpha == +5.0 (5% x 100 / ATR 1), signed by sign(z) == -1
    # BASE: alpha 0. diff == -5.0 - 0
    assert res["diffs"][0] == pytest.approx(-5.0, abs=1e-9)


def test_ungradeable_census_counts_premium(tiny_world):
    panel, masks, cal, monkeypatch = tiny_world
    masks._ev[0, 0] = True
    masks.sign[0, 0] = 1.0
    masks._ba[2, 0] = True          # GONE has no bars at all -> case (ii)
    masks.sign[2, 0] = 1.0
    bars = {"EVT": _flat_bars(cal, 100.0)}
    monkeypatch.setattr(m, "daily_bars", lambda names: bars)
    res = m.build_cell_window(panel, masks, "train", with_alpha=True)
    assert res["census"]["ungradeable"]["ii"] == 1
    assert res["census"]["ungradeable_premium_usd"] == pytest.approx(1e6)
    assert res["census"]["ungradeable_roots"] == {"GONE": pytest.approx(1e6)}
    assert res["census"]["dates_dropped_empty_baseline_arm"] == 1


def test_tier_cohort_covers_all_tiers_and_ignores_arm_drops(tiny_world):
    """Review regression: fired name-days OUTSIDE the selected tier must be
    bar-fetched and graded, and the cohort is UNCONDITIONED on the selected
    tier's arms (a baseline-empty date still contributes)."""
    panel, masks, cal, monkeypatch = tiny_world
    # date 0: normal Q5 event + baseline. date 1: baseline arm EMPTY.
    masks._ev[0, :] = True
    masks.fired[0, :] = True
    masks.sign[0, :] = 1.0
    masks._ba[1, 0] = True
    masks.sign[1, 0] = 1.0
    # OTHER: fired in Q2 (quint value 1) on BOTH dates, never in Q5 arms
    masks.fired[3, :] = True
    masks.quint[3, :] = 1
    masks.sign[3, :] = 1.0
    asked: dict = {}
    bars = {"EVT": _flat_bars(cal, 100.0), "BASE": _flat_bars(cal, 100.0),
            "OTHER": _flat_bars(cal, 100.0)}

    def fake_bars(names):
        asked["names"] = set(names)
        return bars

    monkeypatch.setattr(m, "daily_bars", fake_bars)
    res = m.build_cell_window(panel, masks, "train", with_alpha=True)
    assert "OTHER" in asked["names"]              # bars actually fetched
    assert res["tier_cohort_n"]["1"] == 2         # both dates, arm-drop or not
    assert res["tier_cohort_n"]["4"] == 2         # EVT fired both dates too


def test_gap_vector_is_differences_not_positions(tiny_world):
    panel, masks, cal, monkeypatch = tiny_world
    masks._ev[0, :] = True
    masks.sign[0, :] = 1.0
    masks._ba[1, :] = True
    masks.sign[1, :] = 1.0
    bars = {"EVT": _flat_bars(cal, 100.0), "BASE": _flat_bars(cal, 100.0)}
    monkeypatch.setattr(m, "daily_bars", lambda names: bars)
    res = m.build_cell_window(panel, masks, "train", with_alpha=False)
    assert res["gaps"].tolist() == [28, 29]       # calendar positions
    assert res["gap_vector"] == [1]               # G.4's registered sense


def test_require_backfill_blocks_on_missing_sentinel(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "bars.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE equity_bars (symbol TEXT, date TEXT)")
    for s in m.BACKFILL_SENTINELS[:-1]:           # everything except SPY
        con.execute("INSERT INTO equity_bars VALUES (?, '2026-01-02')", (s,))
    con.commit()
    con.close()
    monkeypatch.setattr(m, "DEFAULT_DB_PATH", db)
    with pytest.raises(SystemExit, match="backfill absent"):
        m._require_backfill()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO equity_bars VALUES ('SPY', '2026-01-02')")
    con.commit()
    con.close()
    evidence = m._require_backfill()
    assert evidence["SPY"]["rows"] == 1


def test_stale_census_cache_is_not_trusted(tmp_path, monkeypatch):
    (tmp_path / "census").mkdir()
    stale = tmp_path / "census" / "2026-06-01.json"
    stale.write_text(json.dumps({"cache_version": 0, "date": "2026-06-01"}))

    def boom(session):
        raise RuntimeError("recompute attempted")

    monkeypatch.setattr(m, "_join_session", boom)
    with pytest.raises(RuntimeError, match="recompute attempted"):
        m._census_session("2026-06-01", rc=None)


def test_verdict_artifact_carries_mde_and_effect(tmp_path):
    rec = {"registration": "test"}
    out = {"cell": "(ALL, Q5, 10td)", "verdict": m.WORDING_DEAD_DILUTION,
           "verdict_note": "f ... beside MDE 0.31 ATR", "mde_atr": 0.31,
           "observed": {"diff_series_mean": 0.12}}
    m._write_verdict_artifact(rec, out)
    art = json.loads(m.VERDICT_PATH.read_text())
    assert art["mde_atr"] == 0.31
    assert art["observed_effect_atr"] == 0.12
    assert art["verdict_note"].endswith("0.31 ATR")


# ------------------------------ verdict ------------------------------

GATE_OK = {"resolves_1pt": True, "lcb": 0.004, "mrd_pt": 0.46}


def test_verdict_dead_dilution_on_f_below_f0():
    v, note = m.decide_verdict(True, 0.0789, 0.0876, GATE_OK,
                               t=3.0, cutoff=2.3, mean=0.12, mde=0.31)
    assert v.startswith("DEAD (dilution)")
    assert "0.31" in note and "+0.1200" in note      # MDE beside effect


def test_verdict_dead_dilution_on_failing_lcb():
    gate = {"resolves_1pt": True, "lcb": -0.002, "mrd_pt": 0.46}
    v, _ = m.decide_verdict(True, 0.09, 0.0876, gate,
                            t=3.0, cutoff=2.3, mean=0.1, mde=0.3)
    assert v.startswith("DEAD (dilution)")


def test_verdict_alive_and_dead_test():
    gate = {"resolves_1pt": True, "lcb": 0.004, "mrd_pt": 0.46}
    v, note = m.decide_verdict(True, 0.10, 0.08, gate,
                               t=2.5, cutoff=2.3, mean=0.2, mde=0.3)
    assert v == m.WORDING_ALIVE and note is None
    v, note = m.decide_verdict(True, 0.10, 0.08, gate,
                               t=1.1, cutoff=2.3, mean=0.05, mde=0.3)
    assert v.startswith("DEAD (test)")
    assert "0.3" in note


def test_verdict_unevaluable():
    v, note = m.decide_verdict(False, 0.10, 0.08, GATE_OK,
                               t=9.9, cutoff=2.3, mean=1.0, mde=0.1)
    assert v.startswith("UNEVALUABLE")


# ------------------------------ gates and guards ------------------------------


def test_calendar_gate():
    with pytest.raises(SystemExit, match="refuses to run before"):
        m._gate("obligation5", today=date(2026, 8, 7))
    m._gate("obligation5", today=date(2026, 8, 8))    # passes


def test_first_run_governs():
    with pytest.raises(SystemExit, match="first run governs"):
        m._require_stage_absent({"stages": {"obligation5": {}}}, "obligation5")


def test_obligation6_requires_5(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "RECORD_PATH", tmp_path / "rec.json")
    with pytest.raises(SystemExit, match="obligation 5 precedes"):
        m.stage_obligation6()


def test_score_refuses_absent_census(tmp_path, monkeypatch):
    rec = {"registration": "test", "stages": {
        "obligation5": {"corpus_census": "not a dict"},
        "obligation6": {}}}
    m.RECORD_PATH.write_text(json.dumps(rec))
    with pytest.raises(SystemExit, match="corpus census is absent"):
        m.stage_score()
