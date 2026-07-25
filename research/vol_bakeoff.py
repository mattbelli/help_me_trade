"""Daily EWMA vs GARCH(1,1) bake-off — settles the `per-symbol-volatility` deferral.

That card ships EWMA (RiskMetrics lambda=0.94) as the v1 sizing estimator and defers
GARCH "adopted only if an offline bake-off wins". This is that bake-off, at the
frequency the card actually uses: **completed daily bars, one-day-ahead**.

Deliberately NOT the intraday comparison the Kronos probe produces as a byproduct.
Volatility results do not transfer across frequencies — at 5-minute resolution the
intraday U-shape dominates and neither plain estimator models it, so an intraday
result says little about a daily sizing decision.

**EWMA wins ties.** It is the zero-dependency incumbent: no `arch`, no optimizer that
can fail to converge, deterministic. GARCH has to clear it by a margin whose 95%
confidence interval excludes zero to be worth the dependency.

`arch` is an on-demand research dependency, deliberately NOT in requirements.txt (the
card scopes the arch dep out of the shipping path):

    .venv/bin/pip install arch
    .venv/bin/python -m research.vol_bakeoff fetch
    .venv/bin/python -m research.vol_bakeoff run
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from db.base import ROOT

logger = logging.getLogger(__name__)

BARS_DB = ROOT / "data" / "research" / "bars.db"
OUTPUT = ROOT / "data" / "research" / "vol_bakeoff.json"

HISTORY_YEARS = 12
EWMA_LAMBDA = 0.94  # RiskMetrics, matches the card's v1 spec
WARMUP_BARS = 500  # GARCH(1,1) is unreliable on much less
GARCH_WINDOW = 1000  # rolling fit window, capped so late fits stay affordable
REFIT_EVERY = 21  # trading days; the recursion is filtered forward in between
MAX_ZERO_RETURN_FRACTION = 0.20  # above this the window is a halt, not a market
SIGMA_FLOOR = 1e-4  # 1bp daily; below this is a data artifact, not a quiet stock
MIN_EVAL_DAYS = 250
N_RESAMPLES = 2000


def _symbols() -> list[str]:
    """The live watchlist universe — whatever daily_bars already tracks."""
    conn = sqlite3.connect(f"file:{ROOT / 'data' / 'trading.db'}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT symbol FROM daily_bars GROUP BY symbol ORDER BY symbol"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def cmd_fetch() -> int:
    from research.bars import cache_bars, get_daily_bars

    start = date.today() - timedelta(days=365 * HISTORY_YEARS)
    end = date.today()
    for symbol in _symbols():
        try:
            bars = get_daily_bars(symbol, start, end)
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the pull
            logger.warning("%s: fetch failed: %s", symbol, exc)
            continue
        cache_bars(symbol, bars)
        logger.info("%s: %d daily bars", symbol, len(bars))
    return 0


def _load_closes(symbol: str) -> tuple[np.ndarray, list[str]]:
    conn = sqlite3.connect(f"file:{BARS_DB}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT date, c FROM daily_bars WHERE symbol = ? AND source = 'alpaca' "
            "ORDER BY date",
            (symbol,),
        ).fetchall()
    finally:
        conn.close()
    return np.array([r[1] for r in rows], dtype=float), [r[0] for r in rows]


def is_degenerate(log_returns: np.ndarray) -> bool:
    """True when the window is too stale to estimate volatility from.

    Found the hard way: NBIS (Nebius, formerly Yandex) was halted on Nasdaq for
    ~2.5 years, leaving 636 zero returns in 2,610 bars. A long run of identical
    closes decays EWMA's variance to ~1e-9, so the first real move after the halt
    scores r^2/sigma^2 near 1e17 and a single symbol swamps 170k honest points.

    GARCH survives that only because its omega term structurally floors the
    variance — a genuine property, but not one the comparison should be measuring.
    So the guard applies to BOTH arms identically: a window that stale means the
    instrument was not trading, and neither estimator should be forecasting from it.
    """
    if log_returns.size == 0:
        return True
    return float((log_returns == 0).mean()) > MAX_ZERO_RETURN_FRACTION


def ewma_sigma(log_returns: np.ndarray, lam: float = EWMA_LAMBDA) -> float:
    """One-step-ahead sigma. Recursive form — the same thing the card's v1 will do."""
    var = float(np.var(log_returns[: min(60, log_returns.size)]))
    for r in log_returns:
        var = lam * var + (1 - lam) * r * r
    return max(float(np.sqrt(var)), SIGMA_FLOOR)


def garch_fit(log_returns: np.ndarray) -> tuple[float, float, float, float] | None:
    """Fit GARCH(1,1); return (omega, alpha, beta, next_variance) in percent units.

    Re-estimating the parameters every day would be ~194k fits (about four hours) for
    no statistical gain — parameters barely move day to day. Standard practice is to
    re-estimate periodically and run the variance recursion forward in between, which
    is what `garch_forward` does. Returns None if the optimizer fails.
    """
    from arch import arch_model

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fitted = arch_model(
                log_returns * 100, mean="Zero", vol="Garch", p=1, q=1
            ).fit(disp="off", show_warning=False)
        params = fitted.params
        omega = float(params["omega"])
        alpha = float(params["alpha[1]"])
        beta = float(params["beta[1]"])
        last_var = float(fitted.conditional_volatility[-1]) ** 2
    except Exception as exc:  # noqa: BLE001 - a failed fit drops this point, not the run
        logger.debug("GARCH fit failed: %s", exc)
        return None

    next_var = garch_forward(omega, alpha, beta, last_var, float(log_returns[-1]) * 100)
    if not all(np.isfinite(v) for v in (omega, alpha, beta, next_var)) or next_var <= 0:
        return None
    return omega, alpha, beta, next_var


def garch_forward(
    omega: float, alpha: float, beta: float, variance: float, last_return: float
) -> float:
    """One step of the GARCH(1,1) recursion: sigma2_next = w + a*r^2 + b*sigma2."""
    return omega + alpha * last_return**2 + beta * variance


def qlike(sigma: np.ndarray, realized: np.ndarray) -> np.ndarray:
    """log(sigma^2) + r^2/sigma^2 — robust where MSE over-punishes a noisy proxy."""
    variance = sigma**2
    return np.log(variance) + realized**2 / variance


def paired_ci(
    diffs: np.ndarray, dates: np.ndarray, *, seed: int = 20260724
) -> tuple[float, float, float]:
    """Mean difference and 95% CI, resampling whole DATES.

    The resampling unit is the date, not the observation: every symbol moves with the
    market on the same day, so treating symbol-days as independent would understate
    the interval badly.
    """
    rng = np.random.default_rng(seed)
    unique = np.unique(dates)
    grouped = [diffs[dates == d] for d in unique]
    means = np.empty(N_RESAMPLES)
    for k in range(N_RESAMPLES):
        pick = rng.integers(0, len(grouped), size=len(grouped))
        means[k] = np.concatenate([grouped[p] for p in pick]).mean()
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(diffs.mean()), float(lo), float(hi)


def evaluate(symbols: list[str]) -> dict:
    ewma_losses: list[float] = []
    garch_losses: list[float] = []
    dates: list[str] = []
    fit_failures = 0
    degenerate = 0
    skipped: list[str] = []

    for n, symbol in enumerate(symbols, start=1):
        closes, bar_dates = _load_closes(symbol)
        if closes.size < WARMUP_BARS + MIN_EVAL_DAYS:
            skipped.append(symbol)
            continue
        logger.info(
            "[%d/%d] %s: %d bars, %d points so far",
            n,
            len(symbols),
            symbol,
            closes.size,
            len(dates),
        )
        returns = np.diff(np.log(closes))
        # returns[t] is the move INTO bar_dates[t+1]; predicting it uses returns[:t],
        # which end at bar t. No lookahead.
        params: tuple[float, float, float] | None = None
        variance = 0.0
        for t in range(WARMUP_BARS, returns.size):
            history = returns[max(0, t - GARCH_WINDOW) : t]
            if is_degenerate(history):
                degenerate += 1
                continue
            if (t - WARMUP_BARS) % REFIT_EVERY == 0:
                fit = garch_fit(history)
                if fit is None:
                    fit_failures += 1
                    params = None
                    continue
                *params, variance = fit  # type: ignore[assignment]
                params = tuple(params)  # type: ignore[assignment]
            elif params is not None:
                variance = garch_forward(*params, variance, float(returns[t - 1]) * 100)
            if params is None or variance <= 0:
                continue

            realized = returns[t]
            garch = max(float(np.sqrt(variance) / 100), SIGMA_FLOOR)
            ewma_losses.append(float(qlike(np.array([ewma_sigma(history)]), realized)[0]))
            garch_losses.append(float(qlike(np.array([garch]), realized)[0]))
            dates.append(bar_dates[t + 1])

    if len(dates) < MIN_EVAL_DAYS:
        return {"n_points": len(dates), "verdict": "INSUFFICIENT DATA"}

    ewma_arr, garch_arr = np.array(ewma_losses), np.array(garch_losses)
    mean, lo, hi = paired_ci(garch_arr - ewma_arr, np.array(dates))
    return {
        "n_points": len(dates),
        "n_symbols": len(symbols) - len(skipped),
        "skipped_short_history": skipped,
        "garch_fit_failures": fit_failures,
        "degenerate_windows_skipped": degenerate,
        "ewma": {"qlike": float(ewma_arr.mean())},
        "garch": {"qlike": float(garch_arr.mean())},
        "garch_minus_ewma": {
            "mean_qlike_delta": mean,
            "ci_low": lo,
            "ci_high": hi,
            "note": "negative favours GARCH",
        },
        "verdict": verdict(mean, hi),
    }


def verdict(mean_delta: float, ci_high: float) -> str:
    """EWMA wins ties — it is the zero-dependency incumbent.

    GARCH is adopted only when it beats EWMA *and* the whole 95% interval sits below
    zero. A tie, or a win whose interval straddles zero, keeps EWMA.
    """
    return "ADOPT GARCH" if mean_delta < 0 and ci_high < 0 else "KEEP EWMA"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Daily EWMA vs GARCH(1,1) bake-off.")
    parser.add_argument("command", choices=("fetch", "run"))
    args = parser.parse_args(argv)
    if args.command == "fetch":
        return cmd_fetch()

    result = evaluate(_symbols())
    Path(OUTPUT).parent.mkdir(parents=True, exist_ok=True)
    Path(OUTPUT).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
