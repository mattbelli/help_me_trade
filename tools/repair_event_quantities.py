"""Re-parse Schwab API trade_events raw_json through the fixed multi-leg parser.

Dry run (default) prints quantity/side/price diffs. ``--apply`` updates those
columns only (never amount, fees, external_key, or raw_json).

Expected live result after the parser fix: exactly one diff —
``evt_294cfb94e52d`` TSLA 2026-07-02 quantity 126.0 → 200.0 (price 399.0,
side sell unchanged). The orchestrator runs ``--apply`` after review; do not
apply automatically from CI.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "trading.db"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imports.money import decimal_to_str, parse_dollar  # noqa: E402
from trading.sync import _schwab_transaction_to_event  # noqa: E402


@dataclass(frozen=True)
class FieldDiff:
    event_id: str
    account_label: str
    symbol: str
    trade_date: str
    field: str
    stored: str
    reparsed: str


def _fmt_decimal(value: Decimal | None) -> str:
    if value is None:
        return "None"
    return decimal_to_str(value)


def _side_mismatch(stored: str | None, reparsed: str) -> bool:
    return (stored or "").lower() != reparsed.lower()


def _fetch_schwab_api_events(db_path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT e.id, e.account_id, e.trade_date, e.symbol, e.side,
                   e.quantity, e.price, e.raw_json, a.label AS account_label
            FROM trade_events e
            JOIN trade_accounts a ON a.id = e.account_id
            WHERE e.source = 'schwab_api' AND e.raw_json IS NOT NULL
            ORDER BY e.trade_date, e.id
            """
        ).fetchall()
    finally:
        conn.close()


def _collect_diffs(rows: list[sqlite3.Row]) -> list[FieldDiff]:
    diffs: list[FieldDiff] = []
    for row in rows:
        try:
            item = json.loads(row["raw_json"])
        except json.JSONDecodeError:
            continue
        reparsed = _schwab_transaction_to_event(item, account_id=row["account_id"])
        if reparsed is None:
            continue

        stored_qty = parse_dollar(row["quantity"])
        if reparsed.quantity != stored_qty:
            diffs.append(
                FieldDiff(
                    event_id=row["id"],
                    account_label=row["account_label"],
                    symbol=row["symbol"] or reparsed.symbol,
                    trade_date=row["trade_date"],
                    field="quantity",
                    stored=_fmt_decimal(stored_qty),
                    reparsed=_fmt_decimal(reparsed.quantity),
                )
            )

        if _side_mismatch(row["side"], reparsed.side):
            diffs.append(
                FieldDiff(
                    event_id=row["id"],
                    account_label=row["account_label"],
                    symbol=row["symbol"] or reparsed.symbol,
                    trade_date=row["trade_date"],
                    field="side",
                    stored=str(row["side"]),
                    reparsed=reparsed.side,
                )
            )

        stored_price = (
            parse_dollar(row["price"]) if row["price"] is not None else None
        )
        if reparsed.price != stored_price:
            diffs.append(
                FieldDiff(
                    event_id=row["id"],
                    account_label=row["account_label"],
                    symbol=row["symbol"] or reparsed.symbol,
                    trade_date=row["trade_date"],
                    field="price",
                    stored=_fmt_decimal(stored_price),
                    reparsed=_fmt_decimal(reparsed.price),
                )
            )
    return diffs


def _print_table(diffs: list[FieldDiff]) -> None:
    if not diffs:
        print("No quantity/side/price diffs found.")
        return
    header = f"{'event_id':<26} {'account':<16} {'symbol':<8} {'date':<12} {'field':<10} stored → reparsed"
    print(header)
    print("-" * len(header))
    for d in diffs:
        print(
            f"{d.event_id:<26} {d.account_label:<16} {d.symbol:<8} {d.trade_date:<12} "
            f"{d.field:<10} {d.stored} → {d.reparsed}"
        )


def _apply_updates(db_path: Path, diffs: list[FieldDiff]) -> int:
    by_event: dict[str, dict[str, str]] = {}
    for d in diffs:
        by_event.setdefault(d.event_id, {})[d.field] = d.reparsed

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")
        updated = 0
        for event_id, fields in by_event.items():
            sets: list[str] = []
            params: list[str] = []
            if "quantity" in fields:
                sets.append("quantity = ?")
                params.append(fields["quantity"])
            if "side" in fields:
                sets.append("side = ?")
                params.append(fields["side"])
            if "price" in fields:
                price_val = fields["price"]
                sets.append("price = ?")
                params.append(None if price_val == "None" else price_val)
            if not sets:
                continue
            params.append(event_id)
            conn.execute(
                f"UPDATE trade_events SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            updated += 1
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile Schwab API event quantity/side/price from raw_json."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Database path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply UPDATEs (default: dry run only).",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"Database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    rows = _fetch_schwab_api_events(args.db)
    diffs = _collect_diffs(rows)
    _print_table(diffs)
    print(f"\nSummary: {len(diffs)} field diff(s) across {len({d.event_id for d in diffs})} event(s)")

    if args.apply:
        if not diffs:
            print("Nothing to apply.")
            return
        updated = _apply_updates(args.db, diffs)
        print(f"Applied updates to {updated} event(s).")


if __name__ == "__main__":
    main()
