"""Live smoke test of the strategy auto-entry chain (PAPER ONLY).

Fires a real paper order for a chosen strategy+symbol through the SAME `auto_execute_entry`
path the scanner uses — so you can verify the whole chain on demand instead of waiting for a
real signal:  size -> RiskEngine -> ExecutionService.submit -> "Trade executed" phone push ->
fill reconcile -> managed_positions row (so the exit engine will manage it).

It bypasses ONLY the strategy predicate + the scan loop (both verified separately) — every
risk/execution gate is exercised for real. Refuses to run unless execution_mode is paper/dry_run.

Usage:
  .venv/bin/python tools/fire_test_entry.py --symbol AAPL --strategy dip_buy --dry   # size only
  .venv/bin/python tools/fire_test_entry.py --symbol AAPL --strategy dip_buy         # live paper fire

After a live fire, flatten the paper position when done (the test position is real in paper).
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--strategy", default="dip_buy")
    ap.add_argument("--dry", action="store_true", help="size only — no order, no push")
    args = ap.parse_args()
    symbol = args.symbol.strip().upper()

    from config.settings import trading_config
    from db.accounts import get_account
    from db.base import DEFAULT_DB_PATH
    from db.strategies import get_strategy
    from trading.proposal_intake import TradeCandidate, _size_candidate, auto_execute_entry

    strat = get_strategy(args.strategy)
    if not strat:
        print(f"unknown strategy {args.strategy!r}")
        return
    mode = trading_config()["execution_mode"]
    account_id = strat.get("account_id")
    print(
        f"mode={mode}  strategy={args.strategy}  symbol={symbol}  "
        f"notional={strat['notional_usd']}  account={account_id}"
    )
    if mode not in ("dry_run", "paper"):
        print("REFUSING — execution_mode is not paper/dry_run.")
        return

    candidate = TradeCandidate(
        symbol=symbol,
        side="buy",
        notional_usd=Decimal(str(strat["notional_usd"])),
        order_type="market",
        source=f"strategy:{args.strategy}",
        account_id=account_id,
    )

    if args.dry:
        sized = await _size_candidate(candidate)
        print("SIZED (no order placed):", sized)
        return

    print("Firing auto_execute_entry — REAL paper order through the chokepoint...")
    result = await auto_execute_entry(candidate)
    print("RESULT:", result)
    if result.get("status") != "executed":
        print("Chain stopped here (not executed). Status above explains why.")
        return
    print("→ A 'Trade executed' push should have hit your phone just now.")

    broker = (get_account(str(account_id)) or {}).get("broker") if account_id else None
    if not broker:
        print("No broker on account — can't reconcile here; check managed_positions later.")
        return

    from trading.fill_tracker import reconcile_open_executions

    print("Polling for fill + managed_positions row (up to ~30s)...")
    for _ in range(10):
        await asyncio.sleep(3)
        try:
            await reconcile_open_executions(broker, account_id=str(account_id))
        except Exception as exc:  # noqa: BLE001
            print("  reconcile error:", exc)
        conn = sqlite3.connect(DEFAULT_DB_PATH)
        conn.row_factory = sqlite3.Row
        mp = conn.execute(
            "SELECT id, strategy_id, qty, status FROM managed_positions "
            "WHERE symbol=? AND status='open' ORDER BY created_at DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        conn.close()
        if mp:
            print(
                f"✓ MANAGED POSITION opened: id={mp['id']} strategy={mp['strategy_id']} "
                f"qty={mp['qty']} status={mp['status']} — the exit engine will now manage it."
            )
            print("Done. Flatten the paper position when you're finished testing.")
            return
    print("✗ No managed_positions row after polling — order may not have filled; check executions.")


if __name__ == "__main__":
    asyncio.run(main())
