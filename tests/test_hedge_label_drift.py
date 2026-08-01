"""Hermetic tests for tools/hedge_label_drift.py (roadmap 3.3 SPEC v2, 2026-08-01).

No network, no `data/` dependency: every fixture is synthetic or a tmp_path.

The three things these tests exist to stop, in priority order:
  1. the SEAL opening by accident (a wrong label file, a skipped gate, an
     out-of-order stage) — every refusal is pinned here;
  2. the verdict table routing to LABEL-EMPTY when the spec says it may not
     (underpowered, a live co-primary anywhere, a residualized-only failure);
  3. v1's false kill — a common drift present in BOTH strata reading as a
     contrast. The label permutation must be uniform-ish there, and it is.

⚠️ Timestamp fixtures use pure INTEGER nanosecond arithmetic: float64 carries 53
mantissa bits and epoch-ns is ~1.8e18, so a float round-trip rounds by ~256ns.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date

import numpy as np
import pytest

import tools.hedge_label_drift as hld
from tools.hedge_label_drift import (
    ALPHA,
    DELTA_MIN,
    FLOOR_HIGH_EVENTS,
    FLOOR_HIGH_UNDERLYINGS,
    FLOOR_NULL_EVENTS,
    GATE_MDE_FLOOR,
    HIGH_THRESHOLD,
    SPLIT_BOUNDARY,
    EventOutcome,
    Harness,
    PermEngine,
    SealError,
    VerdictInputs,
    _block_structure,
    _cut,
    _ecdf_p,
    _point_delta,
    adjustment_factor,
    aligned_excess,
    all_verdict_cells_fail,
    analyse_cut,
    bca_ci,
    block_source_index,
    build_rename_chain,
    conjunction_passes,
    continuity_ratio,
    daily_map,
    earnings_in_window,
    equivalence_ok,
    factor_for,
    horizon_target,
    injection_power,
    jackknife_weights,
    last_trade_at,
    log_ret,
    main,
    mark_at,
    minp_adjusted,
    minute_close_at,
    require_gate1,
    require_unseal,
    resolve_close,
    resolve_forward,
    robust_sd,
    route_verdict,
    session_of,
    sign_flip_p,
    stratified_bootstrap_weights,
    verify_frozen,
    week_id,
)

NS = 1_000_000_000
T0 = 1_800_000_000 * NS


# ===========================================================================
# ★ THE SEAL — the three locks, plus the label-file hash
# ===========================================================================

def _write_frozen(tmp_path, payload=None, monkeypatch=None):
    """Write a synthetic frozen file; optionally re-pin the registered SHA to it
    (no preimage of the real hash exists, so the PASS path has to be pinned)."""
    doc = payload if payload is not None else {"n_events": 0, "events": []}
    raw = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    (tmp_path / hld.FROZEN_NAME).write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    if monkeypatch is not None:
        monkeypatch.setattr(hld, "FROZEN_SHA256", sha)
    return sha


class TestFrozenLabelHash:
    def test_registered_sha_is_the_one_in_the_registration_block(self):
        # changing this constant is an AMENDMENT, never an edit
        assert hld.FROZEN_SHA256 == (
            "104c184e4a20503c8ee6858b2b6efd5078852e07ee5875ab6d9a5aabba089e57")

    def test_missing_frozen_file_refuses(self, tmp_path):
        with pytest.raises(SealError, match="missing"):
            verify_frozen(tmp_path)

    def test_sha_mismatch_aborts(self, tmp_path):
        _write_frozen(tmp_path)          # NOT re-pinned: hashes to something else
        with pytest.raises(SealError, match="SHA MISMATCH"):
            verify_frozen(tmp_path)

    def test_matching_sha_loads(self, tmp_path, monkeypatch):
        _write_frozen(tmp_path, {"n_events": 1, "events": [{"symbol": "X"}]},
                      monkeypatch)
        assert verify_frozen(tmp_path)["n_events"] == 1

    def test_a_single_flipped_byte_is_a_different_study(self, tmp_path, monkeypatch):
        _write_frozen(tmp_path, {"n_events": 1, "events": []}, monkeypatch)
        p = tmp_path / hld.FROZEN_NAME
        p.write_bytes(p.read_bytes().replace(b'"n_events":1', b'"n_events":2'))
        with pytest.raises(SealError, match="SHA MISMATCH"):
            verify_frozen(tmp_path)


class TestStageGating:
    def test_unseal_refuses_without_gate1(self, tmp_path):
        with pytest.raises(SealError, match="run `gate1`"):
            require_gate1(tmp_path)

    def test_unseal_refuses_when_gate1_said_no(self, tmp_path):
        (tmp_path / hld.GATE1_NAME).write_text(json.dumps({"proceed": False}))
        with pytest.raises(SealError, match="ladder"):
            require_gate1(tmp_path)

    def test_unseal_refuses_on_a_missing_proceed_key(self, tmp_path):
        (tmp_path / hld.GATE1_NAME).write_text(json.dumps({"mde": 0.01}))
        with pytest.raises(SealError):
            require_gate1(tmp_path)

    def test_gate1_proceed_true_opens_it(self, tmp_path):
        (tmp_path / hld.GATE1_NAME).write_text(json.dumps({"proceed": True,
                                                           "mde": 0.01}))
        assert require_gate1(tmp_path)["mde"] == 0.01

    def test_analyze_refuses_without_the_unseal_artifact(self, tmp_path):
        with pytest.raises(SealError, match="run `unseal`"):
            require_unseal(tmp_path)

    def test_cli_unseal_exits_1_without_gate1_and_never_touches_the_network(
            self, tmp_path, monkeypatch):
        _write_frozen(tmp_path, monkeypatch=monkeypatch)

        def _boom(*a, **k):
            raise AssertionError("credentials were read before the seal refused")

        monkeypatch.setattr(hld, "alpaca_config", _boom)
        assert main(["unseal", "--out-dir", str(tmp_path)]) == 1

    def test_cli_unseal_exits_1_on_a_bad_label_hash(self, tmp_path, monkeypatch):
        _write_frozen(tmp_path)          # not re-pinned
        (tmp_path / hld.GATE1_NAME).write_text(json.dumps({"proceed": True}))
        monkeypatch.setattr(hld, "alpaca_config",
                            lambda: (_ for _ in ()).throw(AssertionError("too late")))
        assert main(["unseal", "--out-dir", str(tmp_path)]) == 1

    def test_cli_analyze_exits_1_without_unseal(self, tmp_path, monkeypatch):
        _write_frozen(tmp_path, monkeypatch=monkeypatch)
        assert main(["analyze", "--out-dir", str(tmp_path)]) == 1

    def test_cli_gate1_exits_1_on_a_bad_label_hash(self, tmp_path):
        _write_frozen(tmp_path)
        assert main(["gate1", "--out-dir", str(tmp_path)]) == 1


# ===========================================================================
# the registered verdict table
# ===========================================================================

def _vi(**over) -> VerdictInputs:
    """A fully PREDICTIVE set of inputs; each test knocks out one leg."""
    base = dict(
        n_high_events=24, n_high_underlyings=20, n_null_events=70,
        gate2_mde=0.010, coprimary_pass=True, residualized_pass=True,
        delta_point=0.012, ci_lo=0.004, ci_valid=True, sign_agreement=True,
        jackknife_ok=True, null_mean_positive=False,
        all_horizons_all_labellings_fail=False)
    base.update(over)
    return VerdictInputs(**base)


class TestVerdictTable:
    def test_predictive(self):
        assert route_verdict(_vi())["verdict"] == "LABEL-PREDICTIVE"

    def test_graded_when_null_is_itself_significantly_positive(self):
        v = route_verdict(_vi(null_mean_positive=True))
        assert v["verdict"] == "LABEL-GRADED"
        assert "falsified" in v["reason"]

    def test_inverted(self):
        v = route_verdict(_vi(coprimary_pass=False, coprimary_inverted=True,
                              delta_point=-0.02))
        assert v["verdict"] == "LABEL-INVERTED"

    def test_inverted_outranks_the_kill(self):
        v = route_verdict(_vi(coprimary_pass=False, coprimary_inverted=True,
                              all_horizons_all_labellings_fail=True))
        assert v["verdict"] == "LABEL-INVERTED"

    def test_empty_is_the_kill_and_needs_everything(self):
        v = route_verdict(_vi(coprimary_pass=False, residualized_pass=False,
                              delta_point=0.0, ci_lo=-0.01,
                              all_horizons_all_labellings_fail=True))
        assert v["verdict"] == "LABEL-EMPTY"

    def test_ambiguous_is_the_honest_default(self):
        v = route_verdict(_vi(sign_agreement=False))
        assert v["verdict"] == "AMBIGUOUS"

    def test_no_verdict_on_the_event_floor(self):
        v = route_verdict(_vi(n_high_events=FLOOR_HIGH_EVENTS - 1))
        assert v["verdict"] == "NO-VERDICT"
        assert v["floors"]["n_high_events_ok"] is False

    def test_no_verdict_on_the_underlying_floor(self):
        v = route_verdict(_vi(n_high_underlyings=FLOOR_HIGH_UNDERLYINGS - 1))
        assert v["verdict"] == "NO-VERDICT"

    def test_no_verdict_on_the_null_floor(self):
        v = route_verdict(_vi(n_null_events=FLOOR_NULL_EVENTS - 1))
        assert v["verdict"] == "NO-VERDICT"

    def test_no_verdict_when_censoring_drops_more_than_5_percent(self):
        v = route_verdict(_vi(censor_frac_high=0.09))
        assert v["verdict"] == "NO-VERDICT"
        assert v["floors"]["censoring_ok"] is False

    def test_floors_outrank_everything_including_inverted(self):
        v = route_verdict(_vi(n_null_events=1, coprimary_inverted=True))
        assert v["verdict"] == "NO-VERDICT"

    # --- the traps the brief names explicitly -----------------------------

    def test_gate2_failure_blocks_the_kill(self):
        v = route_verdict(_vi(coprimary_pass=False, residualized_pass=False,
                              delta_point=0.0, ci_lo=-0.01, gate2_mde=0.04,
                              all_horizons_all_labellings_fail=True))
        assert v["verdict"] == "NO-VERDICT-UNDERPOWERED"
        assert v["gate2_ok"] is False

    def test_missing_gate2_mde_is_a_failed_gate2(self):
        v = route_verdict(_vi(coprimary_pass=False, residualized_pass=False,
                              delta_point=0.0, ci_lo=-0.01, gate2_mde=None,
                              all_horizons_all_labellings_fail=True))
        assert v["verdict"] == "NO-VERDICT-UNDERPOWERED"

    def test_gate2_at_exactly_the_floor_still_passes(self):
        v = route_verdict(_vi(coprimary_pass=False, residualized_pass=False,
                              delta_point=0.0, ci_lo=-0.01,
                              gate2_mde=GATE_MDE_FLOOR,
                              all_horizons_all_labellings_fail=True))
        assert v["verdict"] == "LABEL-EMPTY"

    def test_a_failed_gate2_does_not_downgrade_a_SIGNIFICANT_result(self):
        # the Gate 2 clause downgrades NON-significant results only; the verdict
        # table's compressed one-liner disagrees and that is FLAGGED, not fixed
        v = route_verdict(_vi(gate2_mde=0.04))
        assert v["verdict"] == "LABEL-PREDICTIVE"

    def test_a_live_coprimary_anywhere_cannot_route_the_kill(self):
        # direction-pass analog: everything else dead, but all_fail is False
        v = route_verdict(_vi(coprimary_pass=False, residualized_pass=False,
                              delta_point=0.0, ci_lo=-0.01,
                              all_horizons_all_labellings_fail=False))
        assert v["verdict"] == "AMBIGUOUS"

    def test_residualized_only_failure_is_ambiguous_with_the_registered_reading(self):
        v = route_verdict(_vi(residualized_pass=False))
        assert v["verdict"] == "AMBIGUOUS"
        assert "concurrent" in v["reason"]

    def test_jackknife_failure_is_ambiguous(self):
        v = route_verdict(_vi(jackknife_ok=False))
        assert v["verdict"] == "AMBIGUOUS"
        assert "calendar week" in v["reason"]

    def test_conflicting_coprimary_signs_are_ambiguous(self):
        v = route_verdict(_vi(coprimary_pass=False,
                              coprimary_conflicting_signs=True))
        assert v["verdict"] == "AMBIGUOUS"
        assert "conflicting" in v["reason"]

    def test_a_point_estimate_under_half_a_percent_cannot_be_predictive(self):
        v = route_verdict(_vi(delta_point=DELTA_MIN - 1e-6))
        assert v["verdict"] == "AMBIGUOUS"

    def test_a_ci_touching_zero_cannot_be_predictive(self):
        v = route_verdict(_vi(ci_lo=0.0))
        assert v["verdict"] == "AMBIGUOUS"

    def test_an_invalid_ci_cannot_be_predictive(self):
        v = route_verdict(_vi(ci_valid=False))
        assert v["verdict"] == "AMBIGUOUS"


class TestAllCellsFail:
    def _cell(self, **k):
        base = {"evaluable": True, "coprimary_pass": False,
                "coprimary_inverted": False}
        base.update(k)
        return base

    def test_all_dead_is_true(self):
        assert all_verdict_cells_fail({f"c{i}": self._cell() for i in range(18)})

    def test_one_live_cell_vetoes_the_kill(self):
        g = {f"c{i}": self._cell() for i in range(18)}
        g["c7"] = self._cell(coprimary_pass=True)
        assert all_verdict_cells_fail(g) is False

    def test_an_inverted_cell_also_vetoes_the_kill(self):
        g = {f"c{i}": self._cell() for i in range(18)}
        g["c3"] = self._cell(coprimary_inverted=True)
        assert all_verdict_cells_fail(g) is False

    def test_unevaluable_cells_are_ignored_not_counted_as_failures(self):
        g = {"a": self._cell(), "b": {"evaluable": False}}
        assert all_verdict_cells_fail(g) is True

    def test_nothing_evaluable_may_never_kill(self):
        assert all_verdict_cells_fail({"a": {"evaluable": False}}) is False


# ===========================================================================
# block permutation — clusters and calendar weeks move TOGETHER
# ===========================================================================

class TestBlockStructure:
    def test_singleton_blocks_round_trip(self):
        b = np.array(["a", "b", "c"])
        eblock, erank, starts, sizes, order = _block_structure(b)
        assert sizes.tolist() == [1, 1, 1]
        assert erank.tolist() == [0, 0, 0]

    def test_ranks_are_within_block(self):
        b = np.array(["w", "w", "w", "x"])
        _, erank, _, sizes, _ = _block_structure(b)
        assert sizes.tolist() == [3, 1]
        assert sorted(erank[:3].tolist()) == [0, 1, 2]

    def test_identity_permutation_is_the_identity_gather(self):
        b = np.array(["w", "w", "x", "y"])
        eblock, erank, starts, sizes, order = _block_structure(b)
        perm = np.arange(3)[None, :]
        src = block_source_index(eblock, erank, starts, sizes, order, perm)
        assert src[0].tolist() == [0, 1, 2, 3]

    def test_a_whole_block_moves_together(self):
        # two 2-event weeks: after a swap every member of a week carries labels
        # from the SAME source week
        b = np.array(["w1", "w1", "w2", "w2"])
        eblock, erank, starts, sizes, order = _block_structure(b)
        perm = np.array([[1, 0]])
        src = block_source_index(eblock, erank, starts, sizes, order, perm)
        assert src[0].tolist() == [2, 3, 0, 1]

    def test_singleton_clusters_give_an_exact_permutation(self):
        b = np.array([f"c{i}" for i in range(30)])
        eblock, erank, starts, sizes, order = _block_structure(b)
        rng = np.random.default_rng(3)
        perm = rng.permuted(np.tile(np.arange(30), (5, 1)), axis=1)
        src = block_source_index(eblock, erank, starts, sizes, order, perm)
        for row in src:
            assert sorted(row.tolist()) == list(range(30))

    def test_uneven_blocks_wrap_rather_than_index_out_of_range(self):
        b = np.array(["big", "big", "big", "small"])
        eblock, erank, starts, sizes, order = _block_structure(b)
        perm = np.array([[1, 0]])
        src = block_source_index(eblock, erank, starts, sizes, order, perm)
        assert set(src[0].tolist()) <= {0, 1, 2, 3}
        # the 3-event block receives the 1-event block's label, wrapped
        assert src[0][:3].tolist() == [3, 3, 3]


class TestPermEngineStructure:
    def _parts(self, n=40, n_high=10, seed=1):
        is_high = np.zeros(n, dtype=bool)
        is_high[:n_high] = True
        rng = np.random.default_rng(seed)
        score = np.where(is_high, 4.0, 0.0) + rng.normal(0, 0.4, n)
        m = rng.normal(0, 0.002, n)
        return is_high, score, m

    def _engine(self, n=40, n_high=10, block=None, n_perm=200, seed=1):
        block = block if block is not None else np.array([f"c{i}" for i in range(n)])
        is_high, score, m = self._parts(n, n_high, seed)
        return PermEngine(block, is_high, score, m, n_perm=n_perm, seed=seed)

    def test_row_zero_is_the_observed_assignment(self):
        e = self._engine()
        y = np.arange(40, dtype=float)
        assert e.delta(y)[0] == pytest.approx(y[:10].mean() - y[10:].mean())

    def test_singleton_clusters_preserve_the_high_count_exactly(self):
        e = self._engine()
        assert e.n_high_observed == 10
        assert e.high_count_preserved_frac == 1.0

    def test_equal_sized_blocks_still_preserve_the_high_count(self):
        # the wrap rule is the identity when every block is the same size, so a
        # regular week grid keeps the count exactly
        e = self._engine(block=np.array([f"w{i // 4}" for i in range(40)]))
        assert e.high_count_preserved_frac == 1.0

    def test_UNEVEN_week_blocks_cost_the_exact_count_and_this_is_disclosed(self):
        # the spec's "preserve the HIGH cluster count" and "blocks move
        # together" cannot both hold when weeks hold different numbers of
        # events; the disclosed consequence is a varying null HIGH count
        block = np.array(["w0"] * 7 + ["w1"] * 3 + ["w2"] * 20 + ["w3"] * 10)
        e = self._engine(block=block)
        assert e.n_high_observed == 10
        assert e.high_count_preserved_frac < 1.0

    def test_spearman_matches_scipy_on_the_observed_row(self):
        from scipy.stats import spearmanr
        e = self._engine()
        _, score, _ = self._parts()
        y = np.random.default_rng(11).normal(size=40)
        assert e.rho(y)[0] == pytest.approx(spearmanr(score, y).statistic, abs=1e-9)

    def test_residualized_coefficient_matches_a_plain_least_squares(self):
        e = self._engine()
        is_high, _, m = self._parts()
        y = np.random.default_rng(5).normal(size=40)
        x = np.column_stack([np.ones(40), m, m * m, is_high.astype(float)])
        coef, *_ = np.linalg.lstsq(x, y, rcond=None)
        assert e.residualized(y)[0] == pytest.approx(coef[3], rel=1e-6)


# ===========================================================================
# the label-permutation null — the v1 false kill is the headline case
# ===========================================================================

class TestLabelPermutation:
    def _setup(self, n=120, n_high=30, seed=0):
        rng = np.random.default_rng(seed)
        cluster = np.array([f"c{i}" for i in range(n)])
        is_high = np.zeros(n, dtype=bool)
        is_high[rng.choice(n, n_high, replace=False)] = True
        score = np.where(is_high, 4.0, 0.0) + rng.normal(0, 0.3, n)
        m = rng.normal(0, 0.002, n)
        return cluster, is_high, score, m, rng

    def test_a_real_high_effect_reaches_a_small_p(self):
        cluster, is_high, score, m, rng = self._setup()
        e = PermEngine(cluster, is_high, score, m, n_perm=2_000, seed=2)
        y = rng.normal(0, 0.02, len(is_high)) + 0.03 * is_high
        out = e.coprimary(y)
        assert out["adjusted_p"] < ALPHA
        assert out["observed"]["delta"] > 0 and out["observed"]["spearman"] > 0

    def test_v1_FALSE_KILL_a_common_drift_in_BOTH_strata_is_not_a_contrast(self):
        # ★ the scenario that made v1's sign-flip null test the wrong thing: a
        # market-wide drift μ raises HIGH and NULL identically. A contrast test
        # must be uniform-ish here; anything else manufactures a verdict.
        ps = []
        for seed in range(24):
            cluster, is_high, score, m, rng = self._setup(seed=seed)
            e = PermEngine(cluster, is_high, score, m, n_perm=600, seed=seed + 100)
            y = rng.normal(0, 0.02, len(is_high)) + 0.05   # μ in BOTH strata
            ps.append(e.coprimary(y)["adjusted_p"])
        ps = np.array(ps)
        assert (ps < 0.05).mean() <= 0.25            # not a rejection machine
        assert 0.2 < float(np.median(ps)) < 0.8      # uniform-ish

    def test_the_permutation_is_one_sided_on_the_thesis_direction(self):
        cluster, is_high, score, m, rng = self._setup()
        e = PermEngine(cluster, is_high, score, m, n_perm=2_000, seed=4)
        y = rng.normal(0, 0.02, len(is_high)) - 0.03 * is_high     # WRONG way
        out = e.coprimary(y)
        assert out["adjusted_p"] > ALPHA
        assert out["inverted_adjusted_p"] < ALPHA

    def test_a_pure_m_effect_fails_the_residualized_leg_while_the_raw_one_passes(self):
        # the registered reading "label proxies the concurrent order-flow move"
        rng = np.random.default_rng(9)
        n = 140
        cluster = np.array([f"c{i}" for i in range(n)])
        is_high = np.zeros(n, dtype=bool)
        is_high[:35] = True
        m = np.where(is_high, 0.004, 0.0) + rng.normal(0, 0.0004, n)
        score = np.where(is_high, 4.0, 0.0) + rng.normal(0, 0.2, n)
        y = 4.0 * m + rng.normal(0, 0.004, n)        # y is a function of m ALONE
        e = PermEngine(cluster, is_high, score, m, n_perm=2_000, seed=6)
        out = e.coprimary(y)
        assert out["adjusted_p"] < ALPHA                       # the raw contrast fires
        assert out["residualized"]["permutation_p"] > ALPHA    # the control does not

    def test_a_degenerate_outcome_yields_nan_not_a_verdict(self):
        cluster, is_high, score, m, _ = self._setup(n=40, n_high=10)
        e = PermEngine(cluster, is_high, score, m, n_perm=200, seed=1)
        out = e.coprimary(np.zeros(40))
        assert math.isnan(out["adjusted_p"]) or out["adjusted_p"] > ALPHA


class TestWeekBlockPermutation:
    def test_events_in_one_calendar_week_move_together(self):
        # four weeks × 5 events; under every draw a week's five events all carry
        # labels sourced from ONE week
        weeks = np.array([f"w{i // 5}" for i in range(20)])
        is_high = np.array([True] * 5 + [False] * 15)
        score = np.where(is_high, 4.0, 0.0)
        m = np.zeros(20)
        e = PermEngine(weeks, is_high, score, m, n_perm=50, seed=3)
        # the HIGH mask under each draw is readable off the delta operator
        hi = e._w_delta > 0
        for row in hi:
            for w in range(4):
                block = row[w * 5:(w + 1) * 5]
                assert block.all() or not block.any()

    def test_a_week_level_drift_does_not_manufacture_a_contrast(self):
        rng = np.random.default_rng(21)
        n_weeks, per = 30, 4
        weeks = np.array([f"w{i // per}" for i in range(n_weeks * per)])
        n = n_weeks * per
        is_high = np.zeros(n, dtype=bool)
        is_high[rng.choice(n, n // 4, replace=False)] = True
        score = np.where(is_high, 4.0, 0.0) + rng.normal(0, 0.3, n)
        drift = np.repeat(rng.normal(0, 0.03, n_weeks), per)   # week-level shock
        y = drift + rng.normal(0, 0.005, n)
        e = PermEngine(weeks, is_high, score, np.zeros(n), n_perm=1_000, seed=5)
        assert e.coprimary(y)["adjusted_p"] > ALPHA


# ===========================================================================
# minP / max-statistic FWER
# ===========================================================================

class TestMinP:
    def test_ecdf_counts_ties_as_at_least(self):
        p = _ecdf_p(np.array([1.0, 1.0, 0.0, -1.0]))
        assert p[0] == pytest.approx(0.5)
        assert p[3] == pytest.approx(1.0)

    def test_adjusted_p_is_never_below_the_smaller_raw_p(self):
        rng = np.random.default_rng(0)
        a = np.concatenate([[3.0], rng.normal(size=2_000)])
        b = np.concatenate([[0.1], rng.normal(size=2_000)])
        out = minp_adjusted({"a": a, "b": b})
        assert out["adjusted_p"] >= min(out["raw_p"].values()) - 1e-12

    def test_two_co_primaries_cost_more_than_one(self):
        rng = np.random.default_rng(1)
        a = np.concatenate([[2.6], rng.normal(size=4_000)])
        b = np.concatenate([[-0.2], rng.normal(size=4_000)])
        single = minp_adjusted({"a": a})
        both = minp_adjusted({"a": a, "b": b})
        assert both["adjusted_p"] > single["adjusted_p"]

    def test_observed_values_are_reported_from_row_zero(self):
        out = minp_adjusted({"a": np.array([5.0, 1.0, 2.0])})
        assert out["observed"]["a"] == 5.0

    def test_all_nan_draws_give_nan_not_a_pass(self):
        out = minp_adjusted({"a": np.full(10, np.nan)})
        assert math.isnan(out["adjusted_p"])


# ===========================================================================
# the STRATIFIED cluster bootstrap
# ===========================================================================

class TestStratifiedBootstrap:
    def test_per_stratum_cluster_counts_are_preserved_in_every_draw(self):
        n_high, n_null = 6, 14
        cluster = np.array([f"c{i}" for i in range(n_high + n_null)])
        is_high = np.array([True] * n_high + [False] * n_null)
        w = stratified_bootstrap_weights(cluster, is_high, n_boot=200, seed=1)
        for row in w:
            # singleton clusters: weight × cluster count = an integer multiplicity
            hi = row[:n_high] * n_high
            lo = -row[n_high:] * n_null
            assert np.allclose(hi, np.round(hi))
            assert np.allclose(lo, np.round(lo))
            assert np.round(hi).sum() == n_high      # exactly n_high draws
            assert np.round(lo).sum() == n_null

    def test_high_weights_are_positive_and_null_weights_negative(self):
        cluster = np.array([f"c{i}" for i in range(10)])
        is_high = np.array([True] * 4 + [False] * 6)
        w = stratified_bootstrap_weights(cluster, is_high, n_boot=50, seed=2)
        assert (w[:, :4] >= 0).all() and (w[:, 4:] <= 0).all()

    def test_the_strata_are_never_mixed(self):
        # a HIGH cluster can never be drawn into the NULL arm
        cluster = np.array([f"c{i}" for i in range(8)])
        is_high = np.array([True] * 3 + [False] * 5)
        w = stratified_bootstrap_weights(cluster, is_high, n_boot=100, seed=3)
        assert (w[:, :3] > 0).any(axis=1).all()
        assert (w[:, 3:] < 0).any(axis=1).all()

    def test_multi_event_clusters_move_together(self):
        cluster = np.array(["A", "A", "B", "C", "D"])
        is_high = np.array([True, True, True, False, False])
        w = stratified_bootstrap_weights(cluster, is_high, n_boot=50, seed=4)
        assert np.allclose(w[:, 0], w[:, 1])     # the pair always shares a count

    def test_the_bootstrap_recovers_the_observed_contrast_on_average(self):
        rng = np.random.default_rng(6)
        n = 80
        cluster = np.array([f"c{i}" for i in range(n)])
        is_high = np.zeros(n, dtype=bool)
        is_high[:20] = True
        y = rng.normal(0, 0.01, n) + 0.02 * is_high
        w = stratified_bootstrap_weights(cluster, is_high, n_boot=800, seed=7)
        obs = y[is_high].mean() - y[~is_high].mean()
        assert float(np.mean(w @ y)) == pytest.approx(obs, abs=0.004)


class TestBcaCi:
    def test_a_clean_positive_effect_has_a_lower_bound_above_zero(self):
        rng = np.random.default_rng(8)
        boot = rng.normal(0.02, 0.004, 2_000)
        jack = rng.normal(0.02, 0.001, 60)
        ci = bca_ci(0.02, boot, jack)
        assert ci["valid"] and ci["lo"] > 0

    def test_more_than_one_percent_nonfinite_draws_invalidates_the_ci(self):
        boot = np.concatenate([np.full(50, np.nan), np.random.default_rng(1)
                               .normal(0.02, 0.004, 950)])
        ci = bca_ci(0.02, boot, np.zeros(10))
        assert ci["valid"] is False and ci["method"] == "invalid"

    def test_a_hair_under_one_percent_nonfinite_still_reports(self):
        boot = np.concatenate([np.full(5, np.nan), np.random.default_rng(1)
                               .normal(0.02, 0.004, 995)])
        assert bca_ci(0.02, boot, np.zeros(10))["valid"] is True

    def test_a_degenerate_bootstrap_falls_back_to_percentile(self):
        boot = np.full(500, 0.01)
        ci = bca_ci(0.01, boot, np.full(10, 0.01))
        assert ci["method"] == "percentile"


class TestEquivalence:
    def test_a_tight_ci_inside_the_band_is_equivalent(self):
        assert equivalence_ok({"valid": True, "lo": -0.001, "hi": 0.002}) is True

    def test_a_ci_straddling_the_band_is_not(self):
        assert equivalence_ok({"valid": True, "lo": -0.001, "hi": 0.009}) is False

    def test_an_invalid_ci_is_never_equivalence(self):
        assert equivalence_ok({"valid": False, "lo": 0.0, "hi": 0.0}) is False

    def test_the_band_edge_is_exclusive(self):
        assert equivalence_ok({"valid": True, "lo": -0.005, "hi": 0.004}) is False


class TestSignFlip:
    def test_a_positive_mean_over_independent_clusters_is_significant(self):
        rng = np.random.default_rng(2)
        y = rng.normal(0.02, 0.01, 120)
        out = sign_flip_p(y, np.array([f"c{i}" for i in range(120)]), n_perm=4_000)
        assert out["p"] < 0.05 and out["mean"] > 0

    def test_noise_around_zero_is_not(self):
        rng = np.random.default_rng(3)
        y = rng.normal(0.0, 0.01, 120)
        out = sign_flip_p(y, np.array([f"c{i}" for i in range(120)]), n_perm=4_000)
        assert out["p"] > 0.05

    def test_flips_are_per_cluster_so_one_loud_name_cannot_carry_it(self):
        y = np.concatenate([np.full(30, 0.02), np.zeros(30)])
        cluster = np.array(["loud"] * 30 + [f"c{i}" for i in range(30)])
        out = sign_flip_p(y, cluster, n_perm=4_000)
        assert out["p"] > 0.05


# ===========================================================================
# the injection harness — Gate 1's rule, end to end
# ===========================================================================

def _harness(n=110, n_high=27, effect_free_seed=0, n_perm=800):
    rng = np.random.default_rng(effect_free_seed)
    cluster = np.array([f"c{i}" for i in range(n)])
    is_high = np.zeros(n, dtype=bool)
    is_high[rng.choice(n, n_high, replace=False)] = True
    score = np.where(is_high, 4.0, 0.0) + rng.normal(0, 0.3, n)
    m = rng.normal(0, 0.002, n)
    bank = [rng.normal(0, 1.0, 12) for _ in range(n)]
    return Harness(
        engine=PermEngine(cluster, is_high, score, m, n_perm=n_perm, seed=1),
        boot_w=stratified_bootstrap_weights(cluster, is_high, n_boot=400, seed=1),
        jack_w=jackknife_weights(cluster, is_high),
        is_high=is_high.astype(float), bank=bank, block=cluster,
        sigma=np.full(n, 0.02),
    )


class TestInjectionHarness:
    def test_a_planted_two_percent_contrast_is_detected(self):
        h = _harness()
        rng = np.random.default_rng(4)
        hits = 0
        for _ in range(20):
            y = np.array([b[rng.integers(len(b))] for b in h.bank]) * h.sigma
            hits += conjunction_passes(h, y + 0.02 * h.is_high)
        assert hits >= 16      # ≥ 80% at δ = 2% on this dispersion

    def test_no_injection_is_not_detected(self):
        h = _harness()
        rng = np.random.default_rng(5)
        hits = 0
        for _ in range(40):
            y = np.array([b[rng.integers(len(b))] for b in h.bank]) * h.sigma
            hits += conjunction_passes(h, y)
        assert hits <= 2       # the operative α is well under 5%

    def test_the_conjunction_needs_more_than_the_p_leg(self):
        # v1's harness scored only the p-leg; a tiny-but-clean effect clears the
        # permutation and still fails the registered 0.5% point floor
        h = _harness()
        y = 0.002 * h.is_high
        assert conjunction_passes(h, y) is False

    def test_power_rises_monotonically_across_the_registered_grid(self):
        h = _harness(n_perm=400)
        out = injection_power(h, deltas=(0.0025, 0.02), n_rep=15, seed=2)
        p = list(out["power_by_delta"].values())
        assert p[1] >= p[0]
        assert out["grid"] == [0.0025, 0.02]

    def test_mde_is_the_smallest_delta_reaching_the_power_target(self):
        h = _harness(n_perm=400)
        out = injection_power(h, deltas=(0.0025, 0.03), n_rep=15, seed=3)
        assert out["mde"] in (None, 0.03)
        assert out["power_target"] == 0.80

    def test_an_unreachable_grid_reports_no_mde_rather_than_a_number(self):
        h = _harness(n_perm=400)
        out = injection_power(h, deltas=(0.0001,), n_rep=10, seed=4)
        assert out["mde"] is None

    def test_the_leg_diagnostic_reports_every_registered_leg(self):
        out = hld.leg_pass_rates(_harness(n_perm=400), 0.02, n_rep=6, seed=1)
        assert set(out) >= {"minp_coprimary", "coprimary_signs", "m_residualized",
                            "point_delta_half_percent",
                            "bootstrap_ci_above_zero", "conjunction"}

    def test_no_leg_can_pass_less_often_than_the_whole_conjunction(self):
        out = hld.leg_pass_rates(_harness(n_perm=400), 0.02, n_rep=10, seed=2)
        legs = [v for k, v in out.items()
                if k not in ("conjunction", "delta", "n_rep")]
        assert out["conjunction"] <= min(legs)


# ===========================================================================
# price space, marks, the continuity tripwire
# ===========================================================================

class TestAdjustmentSpace:
    def test_the_factor_is_all_over_raw(self):
        assert adjustment_factor(99.0, 100.0) == pytest.approx(0.99)

    def test_a_zero_or_missing_raw_close_is_nan_not_a_division(self):
        assert math.isnan(adjustment_factor(99.0, 0.0))
        assert math.isnan(adjustment_factor(float("nan"), 100.0))

    def test_y_is_INVARIANT_to_a_known_dividend_factor(self):
        # ★ the measured blocker: raw quote marks against `adjustment=all` bars
        # mixed price spaces for 244–656 bps on neutral names. With f_i applied
        # the outcome must not move when the whole adjusted series is rescaled.
        raw_bars = {"2026-01-05": 100.0, "2026-01-06": 102.0}
        k = 0.9825                                   # a dividend factor
        all_bars = {d: v * k for d, v in raw_bars.items()}
        m0_raw = 99.5
        f = factor_for("2026-01-05", all_bars, raw_bars)
        assert f == pytest.approx(k)
        y = aligned_excess(m0_raw * f, all_bars["2026-01-06"], 1.0, 1.0, 1.0)
        y_unadjusted = aligned_excess(m0_raw, raw_bars["2026-01-06"], 1.0, 1.0, 1.0)
        assert y == pytest.approx(y_unadjusted, abs=1e-12)

    def test_a_split_factor_is_carried_the_same_way(self):
        raw_bars = {"2026-01-05": 400.0, "2026-01-06": 408.0}
        all_bars = {d: v / 4.0 for d, v in raw_bars.items()}   # a 4:1 split later
        f = factor_for("2026-01-05", all_bars, raw_bars)
        y = aligned_excess(399.0 * f, all_bars["2026-01-06"], 1.0, 1.0, 1.0)
        assert y == pytest.approx(math.log(408.0 / 399.0), abs=1e-12)

    def test_the_tripwire_is_quiet_on_a_clean_join(self):
        assert continuity_ratio(101.0, 100.0) < hld.CONTINUITY_TRIPWIRE

    def test_the_tripwire_FIRES_on_a_mismatched_join(self):
        # a wrong symbol after a rename, or a factor from the wrong day
        assert continuity_ratio(100.0, 55.0) >= hld.CONTINUITY_TRIPWIRE

    def test_the_tripwire_is_nan_safe(self):
        assert math.isnan(continuity_ratio(float("nan"), 100.0))


class TestMarks:
    def _window(self, quote_offsets_s, trade_offsets_s):
        q = {"t_ns": np.array([T0 + int(o * NS) for o in quote_offsets_s],
                              dtype=np.int64),
             "bid": np.full(len(quote_offsets_s), 99.0),
             "ask": np.full(len(quote_offsets_s), 101.0)}
        t = {"t_ns": np.array([T0 + int(o * NS) for o in trade_offsets_s],
                              dtype=np.int64),
             "price": np.array([50.0 + i for i in range(len(trade_offsets_s))])}
        return q, t

    def test_a_fresh_quote_gives_the_midpoint(self):
        q, t = self._window([-5, 0, 5], [-1])
        mk = mark_at(q, t, T0 + 6 * NS)
        assert mk.price == pytest.approx(100.0)
        assert mk.source == "mid" and mk.stale is False

    def test_no_quote_update_within_60s_falls_back_to_the_last_trade(self):
        q, t = self._window([-300], [-100, -70])
        mk = mark_at(q, t, T0)
        assert mk.source == "last_trade" and mk.stale is True
        assert mk.price == pytest.approx(51.0)

    def test_exactly_60s_of_quote_age_is_still_the_midpoint(self):
        q, t = self._window([-60], [-100])
        assert mark_at(q, t, T0).source == "mid"

    def test_a_crossed_quote_is_not_a_midpoint(self):
        q = {"t_ns": np.array([T0], dtype=np.int64),
             "bid": np.array([101.0]), "ask": np.array([99.0])}
        t = {"t_ns": np.array([T0], dtype=np.int64), "price": np.array([100.0])}
        assert mark_at(q, t, T0 + NS).source == "last_trade"

    def test_no_quote_and_no_trade_is_missing_not_a_guess(self):
        q = {"t_ns": np.array([], dtype=np.int64), "bid": np.array([]),
             "ask": np.array([])}
        t = {"t_ns": np.array([], dtype=np.int64), "price": np.array([])}
        assert mark_at(q, t, T0).source == "missing"

    def test_last_trade_is_at_or_before_the_mark_never_after(self):
        t = {"t_ns": np.array([T0 - NS, T0 + NS], dtype=np.int64),
             "price": np.array([10.0, 20.0])}
        assert last_trade_at(t, T0) == 10.0

    def test_last_trade_before_the_window_opens_is_nan(self):
        t = {"t_ns": np.array([T0 + NS], dtype=np.int64), "price": np.array([20.0])}
        assert math.isnan(last_trade_at(t, T0))


class TestReturns:
    def test_returns_are_computed_in_LOG_space(self):
        assert log_ret(100.0, 110.0) == pytest.approx(math.log(1.1))

    def test_a_non_positive_price_is_nan_not_an_exception(self):
        assert math.isnan(log_ret(0.0, 10.0))
        assert math.isnan(log_ret(10.0, -1.0))

    def test_alignment_flips_the_sign_for_a_short_side_bet(self):
        up = aligned_excess(100.0, 102.0, 400.0, 400.0, +1.0)
        down = aligned_excess(100.0, 102.0, 400.0, 400.0, -1.0)
        assert up == pytest.approx(-down)

    def test_spy_is_subtracted_at_identical_marks(self):
        # a pure market move leaves nothing behind
        y = aligned_excess(100.0, 102.0, 400.0, 408.0, 1.0)
        assert y == pytest.approx(0.0, abs=1e-12)

    def test_the_raw_leg_keeps_the_market_move(self):
        y = aligned_excess(100.0, 102.0, 400.0, 408.0, 1.0, beta=0.0)
        assert y == pytest.approx(math.log(1.02))

    def test_a_beta_adjusted_leg_scales_the_market_leg(self):
        y = aligned_excess(100.0, 102.0, 400.0, 408.0, 1.0, beta=0.5)
        assert y == pytest.approx(math.log(1.02) - 0.5 * math.log(1.02))


class TestRobustSd:
    def test_mad_scaling_recovers_a_normal_sigma(self):
        x = np.random.default_rng(0).normal(0, 0.02, 20_000)
        assert robust_sd(x) == pytest.approx(0.02, rel=0.05)

    def test_one_wild_outlier_does_not_move_it(self):
        x = np.concatenate([np.random.default_rng(1).normal(0, 0.02, 200), [10.0]])
        assert robust_sd(x) < 0.05

    def test_a_degenerate_sample_is_nan_not_zero(self):
        assert math.isnan(robust_sd([1.0]))

    def test_all_identical_values_are_nan_rather_than_a_zero_divisor(self):
        assert math.isnan(robust_sd(np.full(50, 3.0)))


# ===========================================================================
# horizons, the sealed-split boundary, censoring, the earnings fence
# ===========================================================================

SESSIONS = [d.isoformat() for d in (
    date(2026, 4, 27), date(2026, 4, 28), date(2026, 4, 29), date(2026, 4, 30),
    date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6),
    date(2026, 5, 7), date(2026, 5, 8))]


class TestHorizons:
    def test_h2_is_the_next_session_close(self):
        assert horizon_target(SESSIONS, "2026-04-27", 1) == ("2026-04-28", False)

    def test_h3_is_five_sessions_out(self):
        assert horizon_target(SESSIONS, "2026-04-27", 5) == ("2026-05-04", False)

    def test_a_window_crossing_the_sealed_split_TRUNCATES_and_flags(self):
        target, trunc = horizon_target(SESSIONS, "2026-05-04", 5)
        assert target == SPLIT_BOUNDARY.isoformat() and trunc is True

    def test_the_boundary_session_itself_is_in_bounds(self):
        assert horizon_target(SESSIONS, "2026-05-04", 1) == ("2026-05-05", False)

    def test_running_off_the_end_of_the_calendar_also_flags(self):
        _, trunc = horizon_target(SESSIONS, "2026-05-07", 21)
        assert trunc is True

    def test_an_unknown_anchor_yields_no_target(self):
        assert horizon_target(SESSIONS, "2026-04-26", 1) == (None, False)


class TestCensoringKeepRule:
    CLOSES = {"2026-04-27": 10.0, "2026-04-28": 11.0, "2026-04-30": 12.0}

    def test_a_present_close_is_used_as_is(self):
        assert resolve_close(SESSIONS, self.CLOSES, "2026-04-28") == (
            11.0, "2026-04-28", "ok")

    def test_a_HALT_takes_the_first_post_resumption_price(self):
        px, used, flag = resolve_close(SESSIONS, self.CLOSES, "2026-04-29")
        assert (px, used, flag) == (12.0, "2026-04-30", "resumption")

    def test_a_DELISTING_takes_the_last_price_that_exists(self):
        px, used, flag = resolve_close(SESSIONS, self.CLOSES, "2026-05-04")
        assert (px, used, flag) == (12.0, "2026-04-30", "last_price")

    def test_only_a_total_absence_of_price_DROPS_the_event(self):
        px, used, flag = resolve_close(SESSIONS, {}, "2026-04-28")
        assert flag == "none" and math.isnan(px) and used == ""

    def test_the_forward_walk_never_crosses_the_sealed_split(self):
        closes = {"2026-04-27": 10.0, "2026-05-08": 99.0}
        px, used, flag = resolve_close(SESSIONS, closes, "2026-05-04")
        assert flag == "last_price" and used == "2026-04-27"

    def test_a_nan_close_is_treated_as_absent(self):
        px, used, flag = resolve_close(
            SESSIONS, {"2026-04-28": float("nan"), "2026-04-30": 12.0}, "2026-04-28")
        assert flag == "resumption" and px == 12.0


class TestEarningsFence:
    def test_a_release_inside_the_forward_window_straddles(self):
        assert earnings_in_window(["2026-04-29"], "2026-04-27", "2026-04-30") is True

    def test_the_event_session_itself_is_not_in_the_forward_window(self):
        assert earnings_in_window(["2026-04-27"], "2026-04-27", "2026-04-30") is False

    def test_the_horizon_end_IS_inclusive(self):
        assert earnings_in_window(["2026-04-30"], "2026-04-27", "2026-04-30") is True

    def test_a_release_beyond_the_window_does_not_fence(self):
        assert earnings_in_window(["2026-05-08"], "2026-04-27", "2026-04-30") is False

    def test_no_known_dates_never_fences(self):
        assert earnings_in_window([], "2026-04-27", "2026-05-30") is False


# ===========================================================================
# date-scoped rename resolution
# ===========================================================================

class TestRenames:
    CHAIN = build_rename_chain([
        {"old_symbol": "P", "new_symbol": "PN", "process_date": "2026-02-10"},
        {"old_symbol": "PN", "new_symbol": "PNX", "process_date": "2026-06-01"},
        {"old_symbol": "GOLD", "new_symbol": "GOLDA", "process_date": "2025-09-01"},
    ])

    def test_a_hop_before_the_event_is_not_replayed(self):
        assert resolve_forward("P", "2026-03-01", "2026-05-01", self.CHAIN) == "P"

    def test_a_hop_inside_the_window_is_followed(self):
        assert resolve_forward("P", "2026-01-01", "2026-05-01", self.CHAIN) == "PN"

    def test_the_chain_is_followed_transitively(self):
        assert resolve_forward("P", "2026-01-01", "2026-07-01", self.CHAIN) == "PNX"

    def test_an_unknown_symbol_is_returned_unchanged(self):
        assert resolve_forward("AAPL", "2026-01-01", "2026-07-01", self.CHAIN) == "AAPL"

    def test_resolution_is_DATE_SCOPED_not_a_global_dict(self):
        # symbol reuse is real; a global map would rename GOLD in every era
        assert resolve_forward("GOLD", "2025-10-01", "2026-05-01", self.CHAIN) == "GOLD"
        assert resolve_forward("GOLD", "2025-08-01", "2026-05-01",
                               self.CHAIN) == "GOLDA"

    def test_a_symbol_returning_to_itself_terminates_rather_than_spinning(self):
        # the monotone process_date cursor is what stops A→B→A from looping
        chain = build_rename_chain([
            {"old_symbol": "A", "new_symbol": "B", "process_date": "2026-01-01"},
            {"old_symbol": "B", "new_symbol": "A", "process_date": "2026-01-02"},
        ])
        assert resolve_forward("A", "2025-01-01", "2027-01-01", chain) == "A"

    def test_an_absurdly_long_chain_raises_rather_than_running_away(self):
        chain = build_rename_chain([
            {"old_symbol": f"S{i}", "new_symbol": f"S{i + 1}",
             "process_date": f"2026-01-{i + 1:02d}"} for i in range(40)])
        with pytest.raises(RuntimeError, match="hops"):
            resolve_forward("S0", "2025-01-01", "2027-01-01", chain)

    def test_records_without_a_date_are_dropped(self):
        assert build_rename_chain([{"old_symbol": "A", "new_symbol": "B"}]) == {}


# ===========================================================================
# bar plumbing
# ===========================================================================

class TestBarHelpers:
    def test_daily_bars_key_on_their_own_session_date(self):
        out = daily_map([{"t": "2026-04-27T04:00:00Z", "c": 10.0},
                         {"t": "2026-04-28T04:00:00Z", "c": 11.0}])
        assert out == {"2026-04-27": 10.0, "2026-04-28": 11.0}

    def test_minute_close_takes_the_last_bar_at_or_before_the_mark(self):
        bars = [{"t": "2026-04-27T14:30:00Z", "c": 1.0},
                {"t": "2026-04-27T14:31:00Z", "c": 2.0}]
        ts = int(np.datetime64("2026-04-27T14:31:30", "ns").astype(np.int64))
        assert minute_close_at(bars, ts) == 2.0

    def test_a_mark_before_the_first_bar_is_nan(self):
        bars = [{"t": "2026-04-27T14:30:00Z", "c": 1.0}]
        ts = int(np.datetime64("2026-04-27T14:00:00", "ns").astype(np.int64))
        assert math.isnan(minute_close_at(bars, ts))

    def test_a_stale_bar_beyond_the_tolerance_is_nan(self):
        bars = [{"t": "2026-04-27T14:30:00Z", "c": 1.0}]
        ts = int(np.datetime64("2026-04-27T15:30:00", "ns").astype(np.int64))
        assert math.isnan(minute_close_at(bars, ts))

    def test_no_bars_is_nan(self):
        assert math.isnan(minute_close_at([], 0))


class TestClockHelpers:
    def test_session_is_read_in_ET_not_the_host_clock(self):
        # 2026-04-27 20:30 UTC = 16:30 ET, still the 27th
        ts = int(np.datetime64("2026-04-27T20:30:00", "ns").astype(np.int64))
        assert session_of(ts) == "2026-04-27"

    def test_a_late_utc_stamp_does_not_roll_the_session_forward(self):
        ts = int(np.datetime64("2026-04-27T23:30:00", "ns").astype(np.int64))
        assert session_of(ts) == "2026-04-27"

    def test_week_id_groups_a_trading_week(self):
        assert week_id("2026-04-27") == week_id("2026-04-30")

    def test_week_id_separates_adjacent_weeks(self):
        assert week_id("2026-04-24") != week_id("2026-04-27")


# ===========================================================================
# the analysis cut — strata, ANTI, marks, legs
# ===========================================================================

def _outcome(symbol, session, score, y_h2, m=0.0, horizon="h2", mark="M0",
             censor="ok", earnings_free=True, scores=None):
    return EventOutcome(
        symbol=symbol, session=session, cluster=f"{symbol}|{session}",
        week=week_id(session), h_sign=1.0,
        scores=scores or {"score": score, "score_trf_incl": score,
                          "score_tick": score},
        m=m, y={f"{mark}|{horizon}|primary": y_h2},
        censor={horizon: censor}, truncated={horizon: False},
        earnings_free={horizon: earnings_free}, drops=[])


class TestCut:
    def test_anti_is_never_pooled_into_the_contrast(self):
        outs = [_outcome("A", "2026-01-05", 5.0, 0.01),
                _outcome("B", "2026-01-06", 0.0, 0.00),
                _outcome("C", "2026-01-07", -5.0, -0.05)]
        cut = _cut(outs, "score", "M0", "h2")
        assert len(cut["y"]) == 2 and "C" not in cut["symbol"].tolist()

    def test_the_high_threshold_boundary_is_inclusive(self):
        outs = [_outcome("A", "2026-01-05", HIGH_THRESHOLD, 0.01),
                _outcome("B", "2026-01-06", HIGH_THRESHOLD - 1e-9, 0.0)]
        cut = _cut(outs, "score", "M0", "h2")
        assert cut["is_high"].tolist() == [True, False]

    def test_earnings_straddlers_are_out_of_the_primary_contrast(self):
        outs = [_outcome("A", "2026-01-05", 5.0, 0.01, earnings_free=False),
                _outcome("B", "2026-01-06", 0.0, 0.0)]
        assert len(_cut(outs, "score", "M0", "h2")["y"]) == 1
        assert len(_cut(outs, "score", "M0", "h2",
                        earnings_free_only=False)["y"]) == 2

    def test_dropped_events_are_excluded(self):
        outs = [_outcome("A", "2026-01-05", 5.0, 0.01, censor="none"),
                _outcome("B", "2026-01-06", 0.0, 0.0)]
        assert len(_cut(outs, "score", "M0", "h2")["y"]) == 1

    def test_a_nan_outcome_is_excluded(self):
        outs = [_outcome("A", "2026-01-05", 5.0, float("nan")),
                _outcome("B", "2026-01-06", 0.0, 0.0)]
        assert len(_cut(outs, "score", "M0", "h2")["y"]) == 1

    def test_a_different_labelling_can_move_an_event_between_strata(self):
        outs = [_outcome("A", "2026-01-05", 0.0, 0.01,
                         scores={"score": 0.0, "score_trf_incl": 4.0,
                                 "score_tick": 0.0}),
                _outcome("B", "2026-01-06", 0.0, 0.0)]
        assert _cut(outs, "score", "M0", "h2")["is_high"].sum() == 0
        assert _cut(outs, "score_trf_incl", "M0", "h2")["is_high"].sum() == 1

    def test_the_mark_and_horizon_select_different_columns(self):
        o = _outcome("A", "2026-01-05", 5.0, 0.01)
        o.y["M0_fast|h2|primary"] = 0.02
        cut_slow = _cut([o, _outcome("B", "2026-01-06", 0.0, 0.0)], "score", "M0", "h2")
        assert cut_slow["y"][0] == 0.01

    def test_point_delta_is_the_stratum_mean_difference(self):
        outs = [_outcome("A", "2026-01-05", 5.0, 0.02),
                _outcome("B", "2026-01-06", 5.0, 0.04),
                _outcome("C", "2026-01-07", 0.0, 0.01)]
        assert _point_delta(_cut(outs, "score", "M0", "h2")) == pytest.approx(0.02)

    def test_point_delta_is_nan_when_a_stratum_is_empty(self):
        outs = [_outcome("A", "2026-01-05", 5.0, 0.02)]
        assert math.isnan(_point_delta(_cut(outs, "score", "M0", "h2")))


class TestAnalyseCut:
    def _outs(self, n=60, effect=0.03, seed=0):
        rng = np.random.default_rng(seed)
        outs = []
        for i in range(n):
            high = i % 4 == 0
            outs.append(_outcome(
                f"S{i}", f"2026-0{1 + i % 3}-{1 + i % 27:02d}",
                4.0 if high else 0.0,
                float(rng.normal(0, 0.01) + (effect if high else 0.0))))
        return outs

    def test_a_thin_cut_is_declared_unevaluable_rather_than_tested(self):
        res = analyse_cut(_cut(self._outs(n=6), "score", "M0", "h2"), "h2", 200, 1)
        assert res["evaluable"] is False

    def test_a_real_effect_passes_the_coprimary(self):
        res = analyse_cut(_cut(self._outs(), "score", "M0", "h2"), "h2", 2_000, 1)
        assert res["evaluable"] and res["coprimary_pass"] is True
        assert res["delta_hat"] > 0

    def test_no_effect_does_not(self):
        res = analyse_cut(_cut(self._outs(effect=0.0, seed=3), "score", "M0", "h2"),
                          "h2", 2_000, 1)
        assert res["coprimary_pass"] is False
        assert res["coprimary_inverted"] is False

    def test_h2_uses_calendar_week_blocks_and_h1_uses_clusters(self):
        cut = _cut(self._outs(), "score", "M0", "h2")
        assert analyse_cut(cut, "h2", 200, 1)["block"] == "calendar_week"
        assert analyse_cut(cut, "h1", 200, 1)["block"] == "cluster"

    def test_an_inverted_effect_is_flagged_as_inverted(self):
        res = analyse_cut(_cut(self._outs(effect=-0.03), "score", "M0", "h2"),
                          "h2", 2_000, 1)
        assert res["coprimary_inverted"] is True
        assert res["coprimary_pass"] is False


class TestJackknifeWeeks:
    def _outs(self, broad=0.02, loud=0.0, seed=0):
        """72 events over 12 calendar weeks — the week-block null needs enough
        BLOCKS to reach α at all (3 weeks give 3! = 6 draws, min p 0.14)."""
        from datetime import timedelta
        rng = np.random.default_rng(seed)
        outs = []
        for i in range(72):
            high = i % 4 == 0
            session = (date(2026, 1, 5)
                       + timedelta(days=7 * (i // 6) + (i % 6) % 5)).isoformat()
            bump = loud if (i // 6 == 3 and high) else 0.0
            outs.append(_outcome(f"S{i}", session, 4.0 if high else 0.0,
                                 float(rng.normal(0, 0.008) + broad * high + bump)))
        return outs

    def test_a_broad_effect_survives_leaving_any_one_week_out(self):
        cut = _cut(self._outs(), "score", "M0", "h2")
        out = hld.jackknife_weeks(cut, "h2", 1_000, 1)
        assert out["survives"] is True
        assert len(out["per_week"]) >= 10

    def test_a_week_that_holds_the_whole_HIGH_stratum_fails_the_jackknife(self):
        # the extreme form of "one week carries it": removing that week leaves no
        # HIGH stratum at all, the cut stops being evaluable, and a non-finite p
        # must count as a FAILURE rather than silently surviving
        from datetime import timedelta
        rng = np.random.default_rng(4)
        outs = []
        for i in range(72):
            high = i // 6 == 3
            session = (date(2026, 1, 5)
                       + timedelta(days=7 * (i // 6) + (i % 6) % 5)).isoformat()
            outs.append(_outcome(f"S{i}", session, 4.0 if high else 0.0,
                                 float(rng.normal(0, 0.008) + 0.05 * high)))
        cut = _cut(outs, "score", "M0", "h2")
        out = hld.jackknife_weeks(cut, "h2", 500, 1)
        assert out["survives"] is False
        assert not math.isfinite(out["max_p"]) or out["max_p"] >= ALPHA

    def test_a_single_week_can_never_drive_a_result_past_alpha_anyway(self):
        # a property of the registered week-block null worth knowing: with W
        # blocks the smallest reachable p from ONE block is ~1/W, so at 12 weeks
        # a one-week-driven contrast cannot clear α = 0.01 in the first place
        cut = _cut(self._outs(broad=0.0, loud=0.30, seed=2), "score", "M0", "h2")
        assert analyse_cut(cut, "h2", 1_000, 1)["coprimary_pass"] is False


# ===========================================================================
# `analyze` end to end, on a synthetic on-disk study
# ===========================================================================

def _build_study(tmp_path, monkeypatch, n_events=100, n_high=25, effect=0.03,
                 seed=0, dividend_factor=0.98):
    """A complete miniature 3.3 study on disk: snapshots, ledger, unseal
    artifacts, both adjustment spaces. Nothing here touches the network."""
    from tools.hedge_footprint import save_npz, session_midnight_ns

    rng = np.random.default_rng(seed)
    out = tmp_path
    udir = out / "unseal"
    from datetime import timedelta
    sessions = [date(2026, 1, 5) + timedelta(days=i) for i in range(60)]
    sessions = [d for d in sessions if d.weekday() < 5]
    sess_iso = [d.isoformat() for d in sessions]

    def _bars(prices):
        return [{"t": f"{s}T05:00:00Z", "c": float(p)}
                for s, p in zip(sess_iso, prices, strict=True)]

    spy_raw = 400.0 * np.exp(np.cumsum(rng.normal(0, 0.004, len(sessions))))
    hld.save_json(udir / "daily" / "SPY_raw.json", _bars(spy_raw))
    hld.save_json(udir / "daily" / "SPY_all.json",
                  _bars(spy_raw * dividend_factor))

    def _minute_bars(day, level, n=400, start_s=9 * 3600 + 30 * 60):
        base = session_midnight_ns(date.fromisoformat(day)) + start_s * NS
        px = level * np.exp(np.cumsum(rng.normal(0, 0.0004, n)))
        return [{"t": str(np.datetime64(base + i * 60 * NS, "ns")) + "Z",
                 "c": float(px[i])} for i in range(n)]

    for s in sess_iso:
        hld.save_json(udir / "spy_minute" / f"{s}.json",
                      _minute_bars(s, 400.0 * dividend_factor))

    events, ledger_events, unseal_events = [], [], []
    for i in range(n_events):
        sym = f"S{i:03d}"
        session = sess_iso[5 + i % 20]
        ts = session_midnight_ns(date.fromisoformat(session)) + 11 * 3600 * NS
        high = i < n_high
        level = 50.0 + i % 7
        drift = effect if high else 0.0
        prices = level * np.exp(np.cumsum(
            rng.normal(0, 0.01, len(sessions))))
        # plant the contrast in the sessions AFTER this event only
        idx = sess_iso.index(session)
        prices[idx + 1:] *= math.exp(drift)
        hld.save_json(udir / "daily" / f"{sym}_raw.json", _bars(prices))
        hld.save_json(udir / "daily" / f"{sym}_all.json",
                      _bars(prices * dividend_factor))

        control_ts = []
        for back in (3, 2):
            cs = sess_iso[idx - back]
            control_ts.append(session_midnight_ns(date.fromisoformat(cs))
                              + 11 * 3600 * NS)
        for mark_ts in [ts, *control_ts]:
            day = hld.session_of(mark_ts)
            spot = prices[sess_iso.index(day)]
            qt = np.arange(mark_ts - 300 * NS, mark_ts + 301 * NS, 10 * NS,
                           dtype=np.int64)
            save_npz(out / "snap" / sym / f"{mark_ts}_quotes.npz",
                     t_ns=qt, bid=np.full(len(qt), spot * 0.999),
                     ask=np.full(len(qt), spot * 1.001),
                     tape=np.full(len(qt), "A", dtype="U1"))
            save_npz(out / "snap" / sym / f"{mark_ts}_trades.npz",
                     t_ns=qt, price=np.full(len(qt), spot),
                     size=np.full(len(qt), 100, dtype=np.int64),
                     exchange=np.full(len(qt), "P", dtype="U2"),
                     cond=np.full(len(qt), "@", dtype="U8"),
                     tape=np.full(len(qt), "A", dtype="U1"))
            hld.save_json(udir / "minute" / sym / f"{mark_ts}.json",
                          _minute_bars(day, spot * dividend_factor))

        score = float(4.0 + rng.normal(0, 0.3)) if high else float(rng.normal(0, 0.8))
        events.append({
            "symbol": sym, "session": session, "ts_ns": int(ts),
            "cluster": f"{sym}|{session}", "score": score,
            "score_trf_incl": score, "score_tick": score,
            "m_bps": float(rng.normal(0, 5.0)), "h_sign": 1.0,
            "stratum": "HIGH" if score >= HIGH_THRESHOLD else "NULL",
            "earnings_dates_45d": [], "expiry_date": sess_iso[-1],
        })
        ledger_events.append({"symbol": sym, "ts_ns": int(ts),
                              "control_ts_ns": [int(t) for t in control_ts]})
        unseal_events.append({"symbol": sym, "ts_ns": int(ts),
                              "fetch_symbol": sym, "session": session})

    doc = {"n_events": len(events), "events": events}
    raw = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    (out / hld.FROZEN_NAME).write_bytes(raw)
    monkeypatch.setattr(hld, "FROZEN_SHA256", hashlib.sha256(raw).hexdigest())
    hld.save_json(out / "fetch_ledger_equity.json", {"events": ledger_events})
    hld.save_json(out / hld.UNSEAL_NAME, {"events": unseal_events})
    return out


class TestUnsealPlumbing:
    def test_cached_writes_once_and_never_refetches(self, tmp_path):
        calls = []

        def _produce():
            calls.append(1)
            return [{"t": "2026-01-05T05:00:00Z", "c": 1.0}]

        p = tmp_path / "x.json"
        assert hld._cached(p, _produce) == hld._cached(p, _produce)
        assert len(calls) == 1

    def _mini(self, tmp_path, monkeypatch, all_close, raw_close, mark_px=100.0):
        from tools.hedge_footprint import save_npz
        udir = tmp_path / "unseal"
        hld.save_json(udir / "daily" / "AAA_all.json",
                      [{"t": "2026-01-05T05:00:00Z", "c": all_close},
                       {"t": "2026-01-06T05:00:00Z", "c": all_close * 1.01}])
        hld.save_json(udir / "daily" / "AAA_raw.json",
                      [{"t": "2026-01-05T05:00:00Z", "c": raw_close},
                       {"t": "2026-01-06T05:00:00Z", "c": raw_close * 1.01}])
        ts = T0
        qt = np.arange(ts, ts + 400 * NS, 10 * NS, dtype=np.int64)
        save_npz(tmp_path / "snap" / "AAA" / f"{ts}_quotes.npz",
                 t_ns=qt, bid=np.full(len(qt), mark_px * 0.999),
                 ask=np.full(len(qt), mark_px * 1.001))
        save_npz(tmp_path / "snap" / "AAA" / f"{ts}_trades.npz",
                 t_ns=qt, price=np.full(len(qt), mark_px))
        return udir, {"symbol": "AAA", "session": "2026-01-05", "ts_ns": ts}

    def test_a_clean_join_does_not_trip(self, tmp_path, monkeypatch):
        udir, ev = self._mini(tmp_path, monkeypatch, 98.0, 100.0, mark_px=100.0)
        out = hld._continuity_check(tmp_path, udir, ev, "AAA")
        assert out["tripped"] is False
        assert out["factor"] == pytest.approx(0.98)

    def test_a_mismatched_join_TRIPS_and_halts_the_run(self, tmp_path, monkeypatch):
        # the mark and the bars are not the same instrument — a wrong snapshot,
        # a wrong window, or a rename the bar side followed and the mark did not
        udir, ev = self._mini(tmp_path, monkeypatch, 98.0, 100.0, mark_px=55.0)
        out = hld._continuity_check(tmp_path, udir, ev, "AAA")
        assert out["tripped"] is True
        assert out["continuity"] >= hld.CONTINUITY_TRIPWIRE

    def test_MEASURED_the_literal_tripwire_is_BLIND_to_the_adjustment_join(
            self, tmp_path, monkeypatch):
        # ★ the flagged SPEC AMBIGUITY, as a measurement rather than a claim:
        # |log(close_all / (M0_raw · f_i))| ≡ |log(close_raw / M0_raw)|, so
        # rescaling the entire `all` space — the very error the tripwire was
        # written to catch — cannot move it. Recorded for the orchestrator; the
        # module does NOT silently substitute the next-session reading.
        a = hld._continuity_check(
            tmp_path / "a", *self._mini(tmp_path / "a", monkeypatch, 98.0, 100.0),
            "AAA")
        b = hld._continuity_check(
            tmp_path / "b", *self._mini(tmp_path / "b", monkeypatch, 12.0, 100.0),
            "AAA")
        assert a["factor"] != b["factor"]
        assert a["continuity"] == pytest.approx(b["continuity"])

    def test_the_next_session_ratio_is_disclosed_without_halting(
            self, tmp_path, monkeypatch):
        udir, ev = self._mini(tmp_path, monkeypatch, 98.0, 100.0, mark_px=100.0)
        out = hld._continuity_check(tmp_path, udir, ev, "AAA")
        assert math.isfinite(out["continuity_next_session"])
        assert out["continuity_next_session"] != out["continuity"]

    def test_a_missing_window_is_reported_not_raised(self, tmp_path, monkeypatch):
        udir, ev = self._mini(tmp_path, monkeypatch, 98.0, 100.0)
        out = hld._continuity_check(tmp_path, udir, {**ev, "symbol": "ZZZ"}, "AAA")
        assert out["tripped"] is False and "no window" in out["note"]


class TestAnalyzeEndToEnd:
    def test_a_planted_contrast_runs_the_whole_pipeline(self, tmp_path, monkeypatch):
        out = _build_study(tmp_path, monkeypatch)
        res = hld.stage_analyze(out, n_perm=400, n_boot=200, n_perm_jack=200,
                                n_rep=4, seed=1)
        assert (out / hld.RESULTS_NAME).exists()
        assert res["verdict"] in {
            "LABEL-PREDICTIVE", "LABEL-GRADED", "LABEL-INVERTED", "LABEL-EMPTY",
            "AMBIGUOUS", "NO-VERDICT", "NO-VERDICT-UNDERPOWERED"}
        # 3 horizons × 3 labellings × 2 marks
        assert len(res["verdict_grid"]) == 18
        assert res["carrier_mark"] in ("M0", "M0_fast")
        assert res["primary"]["M0"]["evaluable"] is True

    def test_the_planted_HIGH_drift_is_recovered_with_the_right_sign(
            self, tmp_path, monkeypatch):
        out = _build_study(tmp_path, monkeypatch, effect=0.05, seed=3)
        res = hld.stage_analyze(out, n_perm=400, n_boot=200, n_perm_jack=200,
                                n_rep=4, seed=1)
        assert res["primary"]["M0"]["delta_hat"] > 0.01
        assert res["primary"]["M0"]["observed"]["spearman"] > 0

    def test_no_planted_effect_leaves_the_contrast_near_zero(
            self, tmp_path, monkeypatch):
        out = _build_study(tmp_path, monkeypatch, effect=0.0, seed=5)
        res = hld.stage_analyze(out, n_perm=400, n_boot=200, n_perm_jack=200,
                                n_rep=4, seed=1)
        assert abs(res["primary"]["M0"]["delta_hat"]) < 0.02
        assert res["verdict"] != "LABEL-PREDICTIVE"

    def test_the_result_carries_the_floors_the_censoring_and_the_ambiguities(
            self, tmp_path, monkeypatch):
        out = _build_study(tmp_path, monkeypatch)
        res = hld.stage_analyze(out, n_perm=200, n_boot=100, n_perm_jack=100,
                                n_rep=3, seed=1)
        assert set(res["floors"]) >= {"n_high_events_ok", "n_null_events_ok",
                                      "censoring_ok"}
        assert res["censoring"]["HIGH"] == 0.0
        assert res["spec_ambiguities"] and any(
            "Gate 2" in a for a in res["spec_ambiguities"])
        assert "gate2" in res and "power_by_delta" in res["gate2"]

    def test_the_artifact_is_json_serialisable_with_no_numpy_leakage(
            self, tmp_path, monkeypatch):
        out = _build_study(tmp_path, monkeypatch)
        hld.stage_analyze(out, n_perm=200, n_boot=100, n_perm_jack=100, n_rep=3,
                          seed=1)
        doc = json.loads((out / hld.RESULTS_NAME).read_text())
        assert isinstance(doc["primary"]["M0"]["delta_hat"], float)
        assert isinstance(doc["all_horizons_all_labellings_fail"], bool)

    def test_analyze_still_refuses_when_the_label_hash_moves(
            self, tmp_path, monkeypatch):
        out = _build_study(tmp_path, monkeypatch)
        monkeypatch.setattr(hld, "FROZEN_SHA256", "00" * 32)
        with pytest.raises(SealError, match="SHA MISMATCH"):
            hld.stage_analyze(out, n_perm=100, n_boot=50, n_perm_jack=50, n_rep=2,
                              seed=1)

    def test_the_dividend_factor_does_not_move_the_contrast(
            self, tmp_path, monkeypatch):
        # ★ the adjustment-space invariance, end to end: rescaling the whole
        # `all` space must not move y by a basis point
        a = hld.stage_analyze(
            _build_study(tmp_path / "a", monkeypatch, seed=11,
                         dividend_factor=1.0),
            n_perm=200, n_boot=100, n_perm_jack=100, n_rep=2, seed=1)
        b = hld.stage_analyze(
            _build_study(tmp_path / "b", monkeypatch, seed=11,
                         dividend_factor=0.87),
            n_perm=200, n_boot=100, n_perm_jack=100, n_rep=2, seed=1)
        assert a["primary"]["M0"]["delta_hat"] == pytest.approx(
            b["primary"]["M0"]["delta_hat"], abs=1e-9)
