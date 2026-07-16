"""One-off: measure the survivorship-bias wedge on the earnings-blowout strategy.

Compares the chosen portfolio config (10 slots x 10%, arrival-order skip) on:
  A. the full survivorship-free Current & Past trade list (the honest number), vs
  B. only trades in symbols that are S&P 500 members TODAY (what a free,
     survivor-biased dataset would have shown us).

Requires NDU running (norgatedata) for today's member list. Read-only.
Run with: .venv-win/Scripts/python.exe -m research._edgehunt.survivorship_wedge
"""

from __future__ import annotations

import sqlite3
import statistics
from pathlib import Path

from research.portfolio import backtest_portfolio

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "research" / "earnings_labeled.db"


def load_trades() -> list[sqlite3.Row]:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(strategy_setups)")}
        sym_col = "symbol" if "symbol" in cols else None
        if sym_col is None:
            raise SystemExit(f"no symbol column; columns = {sorted(cols)}")
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


def run(rows, label: str) -> None:
    trades = [(r["entry_date"], int(r["holding_days"]), float(r["blended_return_pct"])) for r in rows]
    res = backtest_portfolio(trades, max_concurrent=10, per_trade_fraction=0.10, cost_pct=0.0)
    per_trade = [float(r["blended_return_pct"]) for r in rows]
    win = sum(1 for x in per_trade if x > 0) / len(per_trade) * 100.0
    print(
        f"{label:<34} n={len(rows):>5}  CAGR={res['cagr'] * 100:>6.2f}%  "
        f"maxDD={res['max_drawdown'] * 100:>7.2f}%  sharpe={res['sharpe']:>5.2f}  "
        f"per-trade mean={statistics.fmean(per_trade):>6.3f}%  win={win:.1f}%  "
        f"final=${res['final_equity']:>12,.0f}"
    )


def main() -> None:
    import norgatedata  # NDU must be running

    survivors = set(norgatedata.watchlist_symbols("S&P 500"))
    print(f"today's S&P 500 members: {len(survivors)}\n")

    rows = load_trades()
    surv_rows = [r for r in rows if r["symbol"] in survivors]
    dead_rows = [r for r in rows if r["symbol"] not in survivors]

    run(rows, "A. full Current & Past (honest)")
    run(surv_rows, "B. today's members only (biased)")
    if dead_rows:
        run(dead_rows, "C. departed names only")

    syms_all = {r["symbol"] for r in rows}
    syms_surv = {r["symbol"] for r in surv_rows}
    print(
        f"\nsymbols: {len(syms_all)} total -> {len(syms_surv)} still in the index "
        f"({len(syms_all) - len(syms_surv)} departed/delisted)"
    )


if __name__ == "__main__":
    main()
