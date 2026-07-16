"""Diagnostic: how sensitive is backtest_portfolio to same-day signal ordering?

backtest_portfolio sorts stably by entry date, so within a date the caller's
list order decides who gets the scarce slots. This measures the envelope:
DB order (alphabetical-ish), return-ascending (anti-lookahead), return-
descending (lookahead), and N random shuffles (the fair, return-blind case).
"""

from __future__ import annotations

import random
import sqlite3
import statistics
from pathlib import Path

from research.portfolio import backtest_portfolio

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "research" / "earnings_labeled.db"


def load() -> list[tuple[str, int, float]]:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        src = conn.execute(
            "SELECT source FROM strategy_setups GROUP BY source ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT entry_date, holding_days, blended_return_pct FROM strategy_setups "
            "WHERE source = ? ORDER BY entry_date ASC",
            (src,),
        ).fetchall()
    finally:
        conn.close()
    return [(r["entry_date"], int(r["holding_days"]), float(r["blended_return_pct"])) for r in rows]


def run(trades, label: str) -> float:
    res = backtest_portfolio(trades, max_concurrent=10, per_trade_fraction=0.10, cost_pct=0.0)
    print(
        f"{label:<38} CAGR={res['cagr'] * 100:>7.2f}%  maxDD={res['max_drawdown'] * 100:>6.2f}%  "
        f"taken={res['n_taken']:>5}  final=${res['final_equity']:>12,.0f}"
    )
    return res["cagr"]


def main() -> None:
    trades = load()
    run(trades, "DB order (build order, return-blind)")
    run(sorted(trades), "sorted() (ret-asc ties: anti-lookahead)")
    run(
        sorted(trades, key=lambda t: (t[0], -t[2])),
        "ret-desc ties (lookahead, upper bound)",
    )
    cagrs = []
    for seed in range(10):
        rng = random.Random(seed)
        sh = trades[:]
        rng.shuffle(sh)  # stable date sort inside backtest keeps this as tie order
        cagrs.append(run(sh, f"shuffle seed={seed} (fair)"))
    print(
        f"\nfair-shuffle CAGR: mean={statistics.fmean(cagrs) * 100:.2f}%  "
        f"min={min(cagrs) * 100:.2f}%  max={max(cagrs) * 100:.2f}%  "
        f"sd={statistics.pstdev(cagrs) * 100:.2f}pts"
    )


if __name__ == "__main__":
    main()
