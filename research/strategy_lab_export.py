"""Export Strategy Lab JSON artifacts (see References/STRATEGY_LAB_SPEC.md).

Runs on the PC (needs the gitignored research DBs + NDU for benchmark bars) and
writes committed JSON under research/artifacts/strategy_lab/ so serv can render
the sleeves without ever touching the research pipeline.

Usage: .venv-win/Scripts/python.exe -m research.strategy_lab_export
"""

from __future__ import annotations

import json
import random
import sqlite3
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

from research.portfolio import backtest_portfolio

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "research" / "artifacts" / "strategy_lab"
SNAPSHOT = ROOT / "data" / "research" / "universe_snapshot.json"

BENCH_START, BENCH_END = "2010-07-20", "2026-06-18"
BENCHMARKS = ["SPY", "IWR", "MDY", "IWM"]
N_SEEDS = 10
CAVEATS = [
    "Backtest, not live performance",
    "Linear-accrual marking - live drawdowns will run deeper",
    f"Fair-shuffle mean over {N_SEEDS} orderings",
]


def load_rows(db: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        src = conn.execute(
            "SELECT source FROM strategy_setups GROUP BY source ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()[0]
        return conn.execute(
            "SELECT symbol, entry_date, holding_days, blended_return_pct FROM strategy_setups "
            "WHERE source = ? ORDER BY entry_date ASC",
            (src,),
        ).fetchall()
    finally:
        conn.close()


def sleeve_artifact(trades, *, sleeve_id: str, label: str, config: dict) -> dict:
    acc: dict[str, list[float]] = defaultdict(list)
    runs = []
    for seed in range(N_SEEDS):
        sh = trades[:]
        random.Random(seed).shuffle(sh)
        res = backtest_portfolio(sh, max_concurrent=10, per_trade_fraction=0.10, cost_pct=0.0)
        runs.append(res)
        for d, eq in res["daily_equity"]:
            acc[d].append(eq)
    equity = [[d, round(float(np.mean(acc[d])), 2)] for d in sorted(acc)]
    rets = [t[2] for t in trades]
    return {
        "id": sleeve_id,
        "label": label,
        "generated": date.today().isoformat(),
        "config": {**config, "slots": 10, "fraction": 0.10, "n_trades": len(trades)},
        "caveats": CAVEATS,
        "metrics": {
            "cagr": round(statistics.fmean(r["cagr"] for r in runs), 4),
            "max_drawdown": round(statistics.fmean(r["max_drawdown"] for r in runs), 4),
            "sharpe": round(statistics.fmean(r["sharpe"] for r in runs), 2),
            "avg_invested": round(statistics.fmean(r["avg_pct_invested"] for r in runs), 3),
            "per_trade_mean_pct": round(statistics.fmean(rets), 3),
            "win_pct": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1),
        },
        "equity": equity,
    }


def benchmark_series(sym: str) -> list[list]:
    import norgatedata  # NDU must be running

    df = norgatedata.price_timeseries(
        sym,
        stock_price_adjustment_setting=norgatedata.StockPriceAdjustmentType.TOTALRETURN,
        padding_setting=norgatedata.PaddingType.NONE,
        start_date=BENCH_START,
        end_date=BENCH_END,
        timeseriesformat="pandas-dataframe",
    )
    closes = df["Close"]
    scaled = closes / closes.iloc[0] * 100_000.0
    return [[ts.date().isoformat(), round(float(v), 2)] for ts, v in scaled.items()]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    snap = json.loads(SNAPSHOT.read_text())
    sp_universe = set(snap["sp500_cp"])

    old_rows = load_rows(ROOT / "data" / "research" / "earnings_labeled.db")
    new_rows = load_rows(ROOT / "data" / "research" / "earnings_labeled_midcap.db")
    as_trade = lambda r: (r["entry_date"], int(r["holding_days"]), float(r["blended_return_pct"]))  # noqa: E731

    sleeves = [
        sleeve_artifact(
            [as_trade(r) for r in old_rows],
            sleeve_id="earnings_drift_sp",
            label="Earnings drift - S&P 500 C&P",
            config={"cost_pct": 0.20, "universe": "S&P 500 Current & Past"},
        ),
        sleeve_artifact(
            [as_trade(r) for r in new_rows if r["symbol"] not in sp_universe],
            sleeve_id="earnings_drift_midcap",
            label="Earnings drift - Russell Mid Cap C&P (new names)",
            config={"cost_pct": 0.30, "universe": "Russell Mid Cap C&P ex S&P"},
        ),
    ]
    for s in sleeves:
        path = OUT_DIR / f"{s['id']}.json"
        path.write_text(json.dumps(s, separators=(",", ":")))
        print(f"wrote {path} ({len(s['equity'])} pts, CAGR {s['metrics']['cagr']:.2%})")

    bench = {
        "generated": date.today().isoformat(),
        "series": {sym: benchmark_series(sym) for sym in BENCHMARKS},
    }
    path = OUT_DIR / "benchmarks.json"
    path.write_text(json.dumps(bench, separators=(",", ":")))
    print(f"wrote {path} ({', '.join(BENCHMARKS)})")


if __name__ == "__main__":
    main()
