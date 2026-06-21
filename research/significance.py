"""Overlap-aware significance testing for strategy EV via block bootstrap."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np

from db.base import connect
from research.strategy_dataset import DEFAULT_STRATEGY_DB_PATH

_Z_95 = 1.96


def aggregate_daily(dates: list[str], returns: list[float]) -> tuple[list[str], np.ndarray]:
    """Group trades by entry_date -> equal-weight mean return per date."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for d, r in zip(dates, returns, strict=True):
        buckets[d].append(r)

    sorted_dates = sorted(buckets)
    daily_means = np.array([float(np.mean(buckets[d])) for d in sorted_dates])
    return sorted_dates, daily_means


def moving_block_bootstrap_mean(
    series: np.ndarray,
    *,
    block_size: int = 10,
    n_boot: int = 10000,
    seed: int = 42,
) -> dict:
    """Circular moving-block bootstrap of the mean of `series`."""
    series = np.asarray(series, dtype=float)
    n = len(series)
    if n == 0:
        return {
            "observed_mean": None,
            "ci_lo": None,
            "ci_hi": None,
            "p_le_zero": None,
            "n_obs": 0,
            "block_size": block_size,
            "n_boot": n_boot,
        }

    observed_mean = float(np.mean(series))
    if n == 1:
        return {
            "observed_mean": observed_mean,
            "ci_lo": observed_mean,
            "ci_hi": observed_mean,
            "p_le_zero": 1.0 if observed_mean <= 0 else 0.0,
            "n_obs": 1,
            "block_size": block_size,
            "n_boot": n_boot,
        }

    block_size = max(1, block_size)
    n_blocks = int(np.ceil(n / block_size))
    rng = np.random.default_rng(seed)
    boot_means = np.empty(n_boot)

    for b in range(n_boot):
        starts = rng.integers(0, n, size=n_blocks)
        sample = np.empty(n_blocks * block_size, dtype=float)
        pos = 0
        for start in starts:
            block_idx = (start + np.arange(block_size)) % n
            sample[pos : pos + block_size] = series[block_idx]
            pos += block_size
        boot_means[b] = float(np.mean(sample[:n]))

    return {
        "observed_mean": observed_mean,
        "ci_lo": float(np.percentile(boot_means, 2.5)),
        "ci_hi": float(np.percentile(boot_means, 97.5)),
        "p_le_zero": float(np.mean(boot_means <= 0)),
        "n_obs": n,
        "block_size": block_size,
        "n_boot": n_boot,
    }


def _empty_report(*, block_size: int = 10, n_boot: int = 10000) -> dict:
    return {
        "n_trades": 0,
        "n_dates": 0,
        "n_months": 0,
        "per_trade_mean": None,
        "naive_iid_ci": (None, None),
        "daily_mean": None,
        "block_ci": (None, None),
        "p_le_zero": None,
        "block_size": block_size,
        "n_boot": n_boot,
        "significant": False,
    }


def significance_report(
    dates: list[str],
    returns: list[float],
    *,
    block_size: int = 10,
    n_boot: int = 10000,
    seed: int = 42,
) -> dict:
    """Full overlap-aware significance report for per-trade EV."""
    n_trades = len(returns)
    if n_trades == 0 or len(dates) != n_trades:
        return _empty_report(block_size=block_size, n_boot=n_boot)

    trade_returns = np.asarray(returns, dtype=float)
    per_trade_mean = float(np.mean(trade_returns))
    n_months = len({d[:7] for d in dates})

    unique_dates, daily_series = aggregate_daily(dates, returns)
    n_dates = len(unique_dates)

    if n_dates <= 1:
        return {
            "n_trades": n_trades,
            "n_dates": n_dates,
            "n_months": n_months,
            "per_trade_mean": per_trade_mean,
            "naive_iid_ci": (None, None),
            "daily_mean": float(np.mean(daily_series)) if n_dates == 1 else None,
            "block_ci": (None, None),
            "p_le_zero": None,
            "block_size": block_size,
            "n_boot": n_boot,
            "significant": False,
        }

    trade_std = float(np.std(trade_returns, ddof=1)) if n_trades > 1 else 0.0
    se_iid = trade_std / np.sqrt(n_trades)
    naive_iid_ci = (per_trade_mean - _Z_95 * se_iid, per_trade_mean + _Z_95 * se_iid)

    daily_mean = float(np.mean(daily_series))
    boot = moving_block_bootstrap_mean(
        daily_series, block_size=block_size, n_boot=n_boot, seed=seed
    )
    block_ci = (boot["ci_lo"], boot["ci_hi"])
    significant = boot["ci_lo"] is not None and boot["ci_lo"] > 0

    return {
        "n_trades": n_trades,
        "n_dates": n_dates,
        "n_months": n_months,
        "per_trade_mean": per_trade_mean,
        "naive_iid_ci": naive_iid_ci,
        "daily_mean": daily_mean,
        "block_ci": block_ci,
        "p_le_zero": boot["p_le_zero"],
        "block_size": block_size,
        "n_boot": n_boot,
        "significant": significant,
    }


def _format_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _format_ci(lo: float | None, hi: float | None) -> str:
    return f"[{_format_pct(lo)}, {_format_pct(hi)}]"


def _load_strategy_returns(db_path: Path, source: str) -> tuple[list[str], list[float]]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT entry_date, blended_return_pct FROM strategy_setups WHERE source=?",
            (source,),
        ).fetchall()
    finally:
        conn.close()
    dates = [str(row["entry_date"]) for row in rows]
    returns = [float(row["blended_return_pct"]) for row in rows]
    return dates, returns


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Overlap-aware block-bootstrap significance test for strategy EV.",
    )
    parser.add_argument("--source", default="norgate", help="strategy_setups source filter")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_STRATEGY_DB_PATH),
        help="Path to strategy_labeled.db",
    )
    parser.add_argument("--block-size", type=int, default=10, help="Moving-block length in days")
    parser.add_argument("--n-boot", type=int, default=10000, help="Bootstrap resamples")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    dates, returns = _load_strategy_returns(Path(args.db), args.source)
    report = significance_report(
        dates,
        returns,
        block_size=args.block_size,
        n_boot=args.n_boot,
        seed=args.seed,
    )

    print(
        f"SIGNIFICANCE (block bootstrap, block={report['block_size']}d, "
        f"{report['n_boot']:,} resamples)"
    )
    print(
        f"  trades: {report['n_trades']:,} | distinct entry-dates: {report['n_dates']:,} | "
        f"months: {report['n_months']:,}   <- effective sample is the DATES, not trades"
    )
    print(f"  per-trade EV: {_format_pct(report['per_trade_mean'])}")
    print(
        f"  naive IID 95% CI:  {_format_ci(*report['naive_iid_ci'])}   "
        f"(OVERSTATED - ignores clustering)"
    )
    print(
        f"  block-bootstrap 95% CI (per-date): {_format_ci(*report['block_ci'])}   (HONEST)"
    )
    p_le_zero = report["p_le_zero"]
    if p_le_zero is None:
        print("  P(mean <= 0): N/A")
    else:
        print(f"  P(mean <= 0): {p_le_zero * 100:.1f}%")
    if report["significant"]:
        print("  VERDICT: edge is significant at 5% (honest CI excludes 0)")
    else:
        print("  VERDICT: NOT significant")


if __name__ == "__main__":
    main()
