"""One-off: earnings-blowout drift on the Russell Mid Cap C&P expansion.

Compares the chosen portfolio config (10 slots x 10%, arrival-order skip) on:
  A. S&P 500 C&P only (the existing 6,624-trade baseline, cost 0.20%)
  B. mid-cap-only new names (from the expanded build, cost 0.30%)
  C. combined book (A + B)
  D. combined with mid-cap legs at 0.40% cost (spread sensitivity)

Universe labels come from data/research/universe_snapshot.json (written
2026-07-16 from the live Norgate watchlists). Read-only.
"""

from __future__ import annotations

import json
import random
import sqlite3
import statistics
from pathlib import Path

from research.portfolio import backtest_portfolio

ROOT = Path(__file__).resolve().parents[2]
OLD_DB = ROOT / "data" / "research" / "earnings_labeled.db"
NEW_DB = ROOT / "data" / "research" / "earnings_labeled_midcap.db"
SNAPSHOT = ROOT / "data" / "research" / "universe_snapshot.json"


def load(db: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        src = conn.execute(
            "SELECT source FROM strategy_setups GROUP BY source ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()[0]
        return conn.execute(
            """
            SELECT symbol, entry_date, holding_days, blended_return_pct
            FROM strategy_setups WHERE source = ? ORDER BY entry_date ASC
            """,
            (src,),
        ).fetchall()
    finally:
        conn.close()


def sim(trades: list[tuple[str, int, float]], label: str, *, n_seeds: int = 10) -> None:
    """Same-day ties compete for slots in list order, so a deterministic sort
    that touches the return column injects look-ahead (see ordering_fragility.py).
    Average over return-blind shuffles instead."""
    runs = []
    for seed in range(n_seeds):
        sh = trades[:]
        random.Random(seed).shuffle(sh)
        runs.append(
            backtest_portfolio(sh, max_concurrent=10, per_trade_fraction=0.10, cost_pct=0.0)
        )
    cagrs = [r["cagr"] for r in runs]
    dds = [r["max_drawdown"] for r in runs]
    sharpes = [r["sharpe"] for r in runs]
    finals = [r["final_equity"] for r in runs]
    taken = round(statistics.fmean(r["n_taken"] for r in runs))
    invested = statistics.fmean(r["avg_pct_invested"] for r in runs)
    rets = [t[2] for t in trades]
    win = sum(1 for x in rets if x > 0) / len(rets) * 100.0
    print(
        f"{label:<42} n={len(trades):>5}  "
        f"CAGR={statistics.fmean(cagrs) * 100:>6.2f}% "
        f"[{min(cagrs) * 100:.2f}..{max(cagrs) * 100:.2f}]  "
        f"maxDD={statistics.fmean(dds) * 100:>6.2f}%  "
        f"sharpe={statistics.fmean(sharpes):>5.2f}  "
        f"taken~{taken:>5}/{len(trades):<5}  invested={invested * 100:>5.1f}%  "
        f"per-trade={statistics.fmean(rets):>6.3f}%  win={win:.1f}%  "
        f"final~${statistics.fmean(finals):>12,.0f}"
    )


def main() -> None:
    snap = json.loads(SNAPSHOT.read_text())
    sp_universe = set(snap["sp500_cp"])

    old_rows = load(OLD_DB)
    new_rows = load(NEW_DB)

    sp_trades = [
        (r["entry_date"], int(r["holding_days"]), float(r["blended_return_pct"]))
        for r in old_rows
    ]
    # mid-cap-only = expanded-build trades in symbols outside the S&P C&P universe
    mid_rows = [r for r in new_rows if r["symbol"] not in sp_universe]
    mid_trades = [
        (r["entry_date"], int(r["holding_days"]), float(r["blended_return_pct"]))
        for r in mid_rows
    ]
    # 0.40% sensitivity: returns are net of 0.30, so subtract another 0.10
    mid_trades_40 = [(d, h, ret - 0.10) for d, h, ret in mid_trades]

    n_mid_syms = len({r["symbol"] for r in mid_rows})
    print(
        f"expanded build: {len(new_rows)} setups total, "
        f"{len(mid_rows)} in {n_mid_syms} genuinely-new mid-cap symbols\n"
    )

    sim(sp_trades, "A. S&P 500 C&P only (baseline, 0.20%)")
    sim(mid_trades, "B. mid-cap-only new names (0.30%)")
    sim(sp_trades + mid_trades, "C. combined (A + B)")
    sim(sp_trades + mid_trades_40, "D. combined, mid legs at 0.40%")

    # E. tiered: S&P signals outrank mid-cap ones for scarce slots. Class-based
    # priority is knowable in real time (no look-ahead), unlike return-based ties.
    # backtest_portfolio's date sort is stable, so tag and pre-sort by (date, tier)
    # with the within-tier order still shuffled per seed inside sim(): emulate by
    # sorting each shuffled list — sim shuffles first, so sort here is not enough.
    tiered = [(t, 0) for t in sp_trades] + [(t, 1) for t in mid_trades]
    runs = []
    for seed in range(10):
        sh = tiered[:]
        random.Random(seed).shuffle(sh)
        ordered = [t for t, _tier in sorted(sh, key=lambda x: (x[0][0], x[1]))]
        runs.append(
            backtest_portfolio(ordered, max_concurrent=10, per_trade_fraction=0.10, cost_pct=0.0)
        )
    cagrs = [r["cagr"] for r in runs]
    print(
        f"{'E. combined, S&P-priority tiering':<42} n={len(tiered):>5}  "
        f"CAGR={statistics.fmean(cagrs) * 100:>6.2f}% "
        f"[{min(cagrs) * 100:.2f}..{max(cagrs) * 100:.2f}]  "
        f"maxDD={statistics.fmean(r['max_drawdown'] for r in runs) * 100:>6.2f}%  "
        f"sharpe={statistics.fmean(r['sharpe'] for r in runs):>5.2f}  "
        f"taken~{round(statistics.fmean(r['n_taken'] for r in runs)):>5}/{len(tiered):<5}  "
        f"invested={statistics.fmean(r['avg_pct_invested'] for r in runs) * 100:>5.1f}%  "
        f"final~${statistics.fmean(r['final_equity'] for r in runs):>12,.0f}"
    )


if __name__ == "__main__":
    main()
