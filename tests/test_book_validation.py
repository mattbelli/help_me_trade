"""CI guard for the combined-book validation pipeline (research/book_validation.py).

Three tiers:
  1. Synthetic math tests — run everywhere; catch sign/formula errors in the
     stats, CAPM, bootstrap, and NAV-weighting code with hand-checkable inputs.
  2. Golden-fixture tests — run everywhere; recompute the headline numbers
     (Sharpe ~1.42, Jensen alpha, PBO=0.000) from the committed monthly NAV
     CSV. This is the sign-error guard the board card asked for: the numbers
     the armed book is trusted on are re-derived on every CI run.
  3. Local integration — full DB->NAV pipeline vs the fixture; skipped where
     the research DBs don't exist (serv CI), run on the PC.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.bars import DEFAULT_BARS_DB_PATH
from research.book_validation import (
    DEFAULT_FUND_DB_PATH,
    DEFAULT_REGIME_DB_PATH,
    FIXTURE_PATH,
    W_DIP,
    W_MOM,
    W_VIX,
    bootstrap_sharpe_ci,
    build_monthly_nav,
    capm_regression,
    compute_stats,
    overfit_gates,
)

# Headline numbers the armed book is trusted on (EDGE_HUNT_FINDINGS, Tier-1
# hardening). The fixture was written 2026-07-16 from the same pipeline; a
# fixture refresh after a data update may move them slightly — tolerances allow
# data drift but not sign/formula errors.
EXPECTED_SHARPE = 1.418
EXPECTED_CAGR = 0.189
EXPECTED_MAXDD = -0.165
EXPECTED_ALPHA_ANNUAL = 0.064
EXPECTED_BETA = 0.756


# ── Tier 1: synthetic math ────────────────────────────────────────────────────


def test_compute_stats_known_series() -> None:
    """A constant +1%/month book has Sharpe undefined (0 vol) -> use a 2-state series."""
    # Alternating +2% / 0%: mean 1%/mo, sd 1.00503...% (ddof=1) -> Sharpe analytic.
    n = 48
    book = np.array([0.02 if i % 2 == 0 else 0.0 for i in range(n)])
    spy = np.full(n, 0.005)
    spy[0] = 0.0049  # avoid zero variance
    df = pd.DataFrame({"month": [f"2020-{i:02d}" for i in range(1, 13)] * 4, "book_ret": book, "spy_ret": spy})
    s = compute_stats(df, "synthetic")
    assert s is not None
    mean_m = book.mean()
    sd_m = book.std(ddof=1)
    expected_sharpe = (mean_m * 12) / (sd_m * np.sqrt(12))
    assert s["book_sharpe"] == pytest.approx(expected_sharpe, rel=1e-9)
    expected_cagr = np.prod(1 + book) ** (12 / n) - 1
    assert s["book_cagr"] == pytest.approx(expected_cagr, rel=1e-9)
    # book never declines two months in a row from a peak by construction -> maxDD 0
    assert s["book_maxdd"] == 0.0


def test_compute_stats_maxdd_sign() -> None:
    """A single -50% month from the peak must show as exactly -50% (negative)."""
    rets = [0.10, -0.50] + [0.0] * 22
    df = pd.DataFrame({"month": [f"2021-{i%12+1:02d}" for i in range(24)], "book_ret": rets, "spy_ret": [0.01] * 24})
    s = compute_stats(df, "dd")
    assert s is not None
    assert s["book_maxdd"] == pytest.approx(-0.50, abs=1e-12)


def test_capm_recovers_planted_alpha_beta() -> None:
    """y = alpha + beta*x exactly -> regression must recover both to machine precision."""
    rng = np.random.default_rng(7)
    x = rng.normal(0.008, 0.04, 240)
    rf = np.full(240, 0.002)
    alpha_true, beta_true = 0.004, 0.75
    book = rf + alpha_true + beta_true * x
    spy = rf + x
    res = capm_regression(book, spy, rf, "planted")
    assert res["alpha_monthly"] == pytest.approx(alpha_true, abs=1e-12)
    assert res["beta"] == pytest.approx(beta_true, abs=1e-12)
    assert res["alpha_annual"] == pytest.approx((1 + alpha_true) ** 12 - 1, abs=1e-12)
    assert res["r2"] == pytest.approx(1.0, abs=1e-9)


def test_capm_alpha_sign_flips_with_underperformance() -> None:
    rng = np.random.default_rng(11)
    x = rng.normal(0.008, 0.04, 240)
    rf = np.zeros(240)
    book = -0.003 + 1.0 * x
    res = capm_regression(book, x, rf, "neg")
    assert res["alpha_monthly"] < 0


def test_build_monthly_nav_weights_and_idle_cash() -> None:
    """One month with all three sleeves active + one with idle tacticals."""
    month_ends = ["2020-01-31", "2020-02-28", "2020-03-31"]
    spy_dates = month_ends
    spy_opens = [100.0, 101.0, 102.0]
    mom_dates = ["2020-01-31", "2020-02-28"]
    mom_rets = [0.02, 0.01]
    spy_monthly = [0.01, 0.005]
    dip_df = pd.DataFrame(
        [{"symbol": "AAA", "signal_date": "2020-01-15", "return_pct": 3.0, "spy_ret": 1.0}]
    )
    vix_eps = [{"signal_date": "2020-01-10", "entry_date": "2020-01-11", "hold_days": 5,
                "strategy_ret": 0.05, "entry_month": "2020-01"}]
    monthly = build_monthly_nav(
        mom_dates, mom_rets, spy_monthly, dip_df, vix_eps, month_ends, spy_dates, spy_opens
    )
    assert len(monthly) == 2
    jan = monthly.iloc[0]
    # book = W_MOM*2% + W_DIP*3% + W_VIX*(5% - 10bps)
    expected_jan = W_MOM * 0.02 + W_DIP * 0.03 + W_VIX * (0.05 - 0.001)
    assert jan["book_ret"] == pytest.approx(expected_jan, abs=1e-12)
    feb = monthly.iloc[1]
    # tactical sleeves idle -> earn exactly 0, book = W_MOM * mom only
    assert feb["dip_ret"] == 0.0
    assert feb["vix_ret"] == 0.0
    assert feb["book_ret"] == pytest.approx(W_MOM * 0.01, abs=1e-12)


def test_bootstrap_sharpe_ci_brackets_observed() -> None:
    rng = np.random.default_rng(3)
    rets = rng.normal(0.01, 0.03, 180)
    bs = bootstrap_sharpe_ci(rets, spy_sharpe_benchmark=0.5)
    assert bs["ci_lo"] < bs["observed_sharpe"] < bs["ci_hi"]


# ── Tier 2: the committed golden fixture (the actual headline guard) ──────────


@pytest.fixture(scope="module")
def fixture_df() -> pd.DataFrame:
    if not FIXTURE_PATH.exists():
        pytest.skip(f"golden fixture missing: {FIXTURE_PATH}")
    return pd.read_csv(FIXTURE_PATH, dtype={"month": str})


def test_fixture_headline_sharpe_and_cagr(fixture_df: pd.DataFrame) -> None:
    s = compute_stats(fixture_df, "fixture")
    assert s is not None
    assert s["book_sharpe"] == pytest.approx(EXPECTED_SHARPE, abs=0.02)
    assert s["book_cagr"] == pytest.approx(EXPECTED_CAGR, abs=0.005)
    assert s["book_maxdd"] == pytest.approx(EXPECTED_MAXDD, abs=0.005)
    # the deployability claims: beats SPY, shallower drawdown
    assert s["book_sharpe"] > s["spy_sharpe"]
    assert s["book_maxdd"] > s["spy_maxdd"]


def test_fixture_jensen_alpha(fixture_df: pd.DataFrame) -> None:
    clean = fixture_df.dropna(subset=["book_ret", "spy_ret"])
    res = capm_regression(
        clean["book_ret"].values, clean["spy_ret"].values, clean["rf"].values, "fixture"
    )
    assert res["alpha_annual"] == pytest.approx(EXPECTED_ALPHA_ANNUAL, abs=0.01)
    assert res["beta"] == pytest.approx(EXPECTED_BETA, abs=0.05)
    assert res["alpha_annual"] > 0
    if res["t_stat"] is not None:
        assert res["t_stat"] > 2.0


def test_fixture_pbo_and_dsr(fixture_df: pd.DataFrame) -> None:
    clean = fixture_df.dropna(subset=["book_ret", "spy_ret"])
    gates = overfit_gates(clean, n_trials_grid=(40, 76))
    assert gates["pbo"] <= 0.05  # documented PBO=0.000
    for res in gates["dsr_by_trials"].values():
        assert res["dsr"] > 0.95


def test_fixture_robustness_cut_still_beats_spy(fixture_df: pd.DataFrame) -> None:
    robust = fixture_df[~fixture_df["month"].str[:4].isin({"2020", "2026"})].copy()
    s = compute_stats(robust, "ex-2020+2026")
    assert s is not None
    assert s["book_sharpe"] > s["spy_sharpe"]


def test_fixture_metrics_cross_check_empyrical(fixture_df: pd.DataFrame) -> None:
    """Independent-implementation guard: our Sharpe/CAGR/maxDD/alpha/beta must match
    empyrical-reloaded's on the same fixture. Catches the class of error CI can't
    otherwise see — our own math agreeing with itself. (Sortino is deliberately not
    cross-checked: empyrical uses full-sample downside deviation, we use the std of
    negative months — different definitions, both fine, not comparable.)"""
    ep = pytest.importorskip("empyrical")

    clean = fixture_df.dropna(subset=["book_ret", "spy_ret"])
    book = pd.Series(clean["book_ret"].values)
    spy = pd.Series(clean["spy_ret"].values)
    rf = clean["rf"].values

    ours = compute_stats(clean, "cross-check")
    assert ours is not None
    capm = capm_regression(clean["book_ret"].values, clean["spy_ret"].values, rf, "cross-check")

    assert ours["book_sharpe"] == pytest.approx(ep.sharpe_ratio(book, period="monthly"), rel=1e-9)
    assert ours["book_cagr"] == pytest.approx(ep.annual_return(book, period="monthly"), rel=1e-9)
    assert ours["book_maxdd"] == pytest.approx(ep.max_drawdown(book), rel=1e-9)

    # CAPM on excess returns (rf varies monthly, so subtract it and pass risk_free=0)
    ep_alpha, ep_beta = ep.alpha_beta(book - rf, spy - rf, risk_free=0.0, period="monthly")
    assert capm["alpha_annual"] == pytest.approx(ep_alpha, rel=1e-9)
    assert capm["beta"] == pytest.approx(ep_beta, rel=1e-9)


# ── Tier 3: local integration (PC only — needs the research DBs) ──────────────


@pytest.mark.skipif(
    not (DEFAULT_BARS_DB_PATH.exists() and DEFAULT_REGIME_DB_PATH.exists()
         and DEFAULT_FUND_DB_PATH.exists()),
    reason="research DBs not present (runs on the PC, skipped in CI)",
)
def test_pipeline_reproduces_fixture(fixture_df: pd.DataFrame) -> None:
    from research.book_validation import run_pipeline

    monthly = run_pipeline()
    # Bars grow over time: compare the overlapping months only.
    merged = monthly.merge(fixture_df, on="month", suffixes=("", "_fix"))
    assert len(merged) >= 150
    np.testing.assert_allclose(
        merged["book_ret"].values, merged["book_ret_fix"].values, atol=1e-9,
        err_msg="pipeline no longer reproduces the committed monthly book returns",
    )
