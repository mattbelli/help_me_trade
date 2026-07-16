"""Chart: S&P vs mid-cap — benchmarks and our earnings-drift sleeves, 2010–2026.

Three panels: (1) log equity curves ($100k start) for SPY B&H, IWR B&H, and the
two strategy sleeves (fair-shuffle mean across 10 seeds); (2) year-by-year
returns; (3) recent quarterly returns. Requires NDU running for benchmark bars.
Writes research/artifacts/midcap_vs_sp.png (gitignored dir; viz artifact).
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
from collections import defaultdict
from pathlib import Path

logging.disable(logging.CRITICAL)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import norgatedata
import numpy as np
import pandas as pd

from research.portfolio import backtest_portfolio

ROOT = Path(__file__).resolve().parents[2]
OLD_DB = ROOT / "data" / "research" / "earnings_labeled.db"
NEW_DB = ROOT / "data" / "research" / "earnings_labeled_midcap.db"
SNAPSHOT = ROOT / "data" / "research" / "universe_snapshot.json"
OUT = ROOT / "research" / "artifacts" / "midcap_vs_sp.png"

START, END = "2010-07-20", "2026-06-18"


def bench_series(sym: str) -> pd.Series:
    df = norgatedata.price_timeseries(
        sym,
        stock_price_adjustment_setting=norgatedata.StockPriceAdjustmentType.TOTALRETURN,
        padding_setting=norgatedata.PaddingType.NONE,
        start_date=START,
        end_date=END,
        timeseriesformat="pandas-dataframe",
    )
    s = df["Close"]
    return s / s.iloc[0] * 100_000.0


def load_trades(db: Path) -> list[tuple[str, int, float]]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        src = conn.execute(
            "SELECT source FROM strategy_setups GROUP BY source ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT symbol, entry_date, holding_days, blended_return_pct FROM strategy_setups "
            "WHERE source = ? ORDER BY entry_date ASC",
            (src,),
        ).fetchall()
    finally:
        conn.close()
    return rows


def strat_series(trades: list[tuple[str, int, float]], n_seeds: int = 10) -> pd.Series:
    """Mean daily equity across fair shuffles (see ordering_fragility.py)."""
    acc: dict[str, list[float]] = defaultdict(list)
    for seed in range(n_seeds):
        sh = trades[:]
        random.Random(seed).shuffle(sh)
        res = backtest_portfolio(sh, max_concurrent=10, per_trade_fraction=0.10, cost_pct=0.0)
        for d, eq in res["daily_equity"]:
            acc[d].append(eq)
    dates = sorted(acc)
    vals = [float(np.mean(acc[d])) for d in dates]
    return pd.Series(vals, index=pd.to_datetime(dates))


def annual_returns(s: pd.Series) -> pd.Series:
    ye = s.resample("YE").last()
    first = s.iloc[0]
    rets = ye.pct_change()
    rets.iloc[0] = ye.iloc[0] / first - 1.0
    rets.index = rets.index.year
    return rets * 100.0


def quarterly_returns(s: pd.Series, n_quarters: int = 12) -> pd.Series:
    qe = s.resample("QE").last()
    rets = (qe.pct_change() * 100.0).dropna()
    return rets.iloc[-n_quarters:]


def main() -> None:
    snap = json.loads(SNAPSHOT.read_text())
    sp_universe = set(snap["sp500_cp"])

    old_rows = load_trades(OLD_DB)
    new_rows = load_trades(NEW_DB)
    sp_trades = [
        (r["entry_date"], int(r["holding_days"]), float(r["blended_return_pct"])) for r in old_rows
    ]
    mid_trades = [
        (r["entry_date"], int(r["holding_days"]), float(r["blended_return_pct"]))
        for r in new_rows
        if r["symbol"] not in sp_universe
    ]

    series = {
        "SPY buy & hold": (bench_series("SPY"), "#888888", "--"),
        "IWR (mid cap) buy & hold": (bench_series("IWR"), "#c49a3a", "--"),
        "Strat: S&P universe": (strat_series(sp_trades), "#1f77b4", "-"),
        "Strat: mid-cap universe": (strat_series(mid_trades), "#d62728", "-"),
    }

    fig, axes = plt.subplots(
        3, 1, figsize=(13, 14), gridspec_kw={"height_ratios": [1.4, 1, 1]}
    )
    fig.suptitle(
        "Earnings-blowout drift: S&P vs mid-cap universes, 2010–2026\n"
        "(strategy = 10 slots × 10%, fair-shuffle mean; benchmarks total-return)",
        fontsize=13,
    )

    ax = axes[0]
    for label, (s, color, ls) in series.items():
        ax.plot(s.index, s.values, label=label, color=color, linestyle=ls, linewidth=1.4)
    ax.set_yscale("log")
    ax.set_ylabel("equity ($, log scale, $100k start)")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.set_title("Equity curves", fontsize=11)

    ax = axes[1]
    ann = {label: annual_returns(s) for label, (s, _, _) in series.items()}
    years = sorted(set().union(*[a.index for a in ann.values()]))
    width = 0.2
    x = np.arange(len(years))
    for i, (label, (s, color, _)) in enumerate(series.items()):
        vals = [ann[label].get(y, np.nan) for y in years]
        ax.bar(x + (i - 1.5) * width, vals, width, label=label, color=color)
    ax.set_xticks(x)
    ax.set_xticklabels([str(y) for y in years], rotation=45)
    ax.axhline(0, color="black", linewidth=0.7)
    ax.set_ylabel("return %")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", alpha=0.3)
    ax.set_title("Year-by-year returns", fontsize=11)

    ax = axes[2]
    qtr = {label: quarterly_returns(s) for label, (s, _, _) in series.items()}
    quarters = sorted(set().union(*[q.index for q in qtr.values()]))
    qlabels = [f"{q.year}Q{q.quarter}" for q in quarters]
    x = np.arange(len(quarters))
    for i, (label, (s, color, _)) in enumerate(series.items()):
        vals = [qtr[label].get(q, np.nan) for q in quarters]
        ax.bar(x + (i - 1.5) * width, vals, width, label=label, color=color)
    ax.set_xticks(x)
    ax.set_xticklabels(qlabels, rotation=45)
    ax.axhline(0, color="black", linewidth=0.7)
    ax.set_ylabel("return %")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", alpha=0.3)
    ax.set_title("Recent quarters", fontsize=11)

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=130)
    print(f"wrote {OUT}")

    # console: recent relative trend
    iwr_ann = ann["IWR (mid cap) buy & hold"]
    spy_ann = ann["SPY buy & hold"]
    print("\nmid-cap minus SPY, by year (B&H):")
    for y in years:
        d = iwr_ann.get(y, np.nan) - spy_ann.get(y, np.nan)
        print(f"  {y}: {d:+.1f}pts")


if __name__ == "__main__":
    main()
