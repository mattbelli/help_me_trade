"""One-off: breadth-vs-concentration portfolio sim + blowout_reaction_pct decile bucket.

Runs the existing earnings-blowout setup list (earnings_labeled.db) through
research.portfolio.backtest_portfolio at two configs, then buckets the same
setups by blowout_reaction_pct decile. Read-only; no DB writes.
"""

from __future__ import annotations

import sqlite3
import statistics
from pathlib import Path

from research.portfolio import backtest_portfolio

ROOT = Path(__file__).resolve().parents[2]

CANDIDATE_DBS = [
    ROOT / "data" / "research" / "earnings_labeled.db",
    ROOT / "data" / "research" / "strategy_labeled.db",
]


def find_dataset() -> tuple[Path, str, int]:
    best = None
    for db in CANDIDATE_DBS:
        if not db.exists():
            continue
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(strategy_setups)")}
            if "blowout_reaction_pct" not in cols:
                continue
            for src, n in conn.execute(
                "SELECT source, COUNT(*) FROM strategy_setups GROUP BY source"
            ):
                print(f"  {db.name} source={src!r}: {n} rows")
                if best is None or n > best[2]:
                    best = (db, src, n)
        finally:
            conn.close()
    if best is None:
        raise SystemExit("no strategy_setups table with blowout_reaction_pct found")
    return best


def load(db: Path, source: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT entry_date, holding_days, blended_return_pct, blowout_reaction_pct
            FROM strategy_setups
            WHERE source = ?
            ORDER BY entry_date ASC
            """,
            (source,),
        ).fetchall()
    finally:
        conn.close()


def run_portfolio(rows, *, max_concurrent: int, fraction: float) -> dict:
    trades = [(r["entry_date"], int(r["holding_days"]), float(r["blended_return_pct"])) for r in rows]
    return backtest_portfolio(
        trades,
        max_concurrent=max_concurrent,
        per_trade_fraction=fraction,
    )


def decile_table(rows) -> None:
    labeled = [
        (float(r["blowout_reaction_pct"]), float(r["blended_return_pct"]))
        for r in rows
        if r["blowout_reaction_pct"] is not None
    ]
    n_null = len(rows) - len(labeled)
    labeled.sort(key=lambda t: t[0])
    n = len(labeled)
    print(f"\nblowout_reaction_pct decile bucket ({n} setups, {n_null} null reaction)")
    header = (
        f"{'decile':>6} {'reaction lo':>12} {'reaction hi':>12} {'n':>5} "
        f"{'mean ret%':>10} {'median%':>9} {'win%':>6} {'stdev':>7}"
    )
    print(header)
    print("-" * len(header))
    for d in range(10):
        lo_i = d * n // 10
        hi_i = (d + 1) * n // 10
        bucket = labeled[lo_i:hi_i]
        if not bucket:
            continue
        rets = [ret for _, ret in bucket]
        mean = statistics.fmean(rets)
        med = statistics.median(rets)
        win = sum(1 for x in rets if x > 0) / len(rets) * 100.0
        sd = statistics.pstdev(rets)
        print(
            f"{d + 1:>6} {bucket[0][0]:>11.2f}% {bucket[-1][0]:>11.2f}% {len(bucket):>5} "
            f"{mean:>10.3f} {med:>9.3f} {win:>5.1f}% {sd:>7.2f}"
        )


def main() -> None:
    print("Datasets found:")
    db, source, n = find_dataset()
    print(f"\nUsing {db.name} source={source!r} ({n} setups)\n")
    rows = load(db, source)

    configs = [
        ("concentrated  (max_concurrent=5,  fraction=0.10)", 5, 0.10),
        ("breadth       (max_concurrent=50, fraction=0.02)", 50, 0.02),
        ("breadth+size  (max_concurrent=50, fraction=0.03)", 50, 0.03),
        ("breadth+size  (max_concurrent=50, fraction=0.05)", 50, 0.05),
    ]
    results = []
    for label, mc, frac in configs:
        res = run_portfolio(rows, max_concurrent=mc, fraction=frac)
        results.append((label, res))

    header = (
        f"{'config':<48} {'CAGR':>8} {'maxDD':>8} {'sharpe':>7} {'sortino':>8} "
        f"{'taken':>11} {'avg inv':>8} {'final eq':>12}"
    )
    print(header)
    print("-" * len(header))
    for label, res in results:
        taken = f"{res['n_taken']}/{res['n_taken'] + res['n_skipped']}"
        print(
            f"{label:<48} {res['cagr'] * 100:>7.2f}% {res['max_drawdown'] * 100:>7.2f}% "
            f"{res['sharpe']:>7.2f} {res['sortino']:>8.2f} {taken:>11} "
            f"{res['avg_pct_invested'] * 100:>7.1f}% {res['final_equity']:>12,.0f}"
        )

    decile_table(rows)


if __name__ == "__main__":
    main()
