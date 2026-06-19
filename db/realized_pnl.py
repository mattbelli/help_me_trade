"""Net-cash realized P&L and position reconciliation (order-independent truth)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from imports.money import decimal_to_str, parse_dollar

_RECON_EVENT_TYPES = frozenset({"trade", "option_expired"})
_CLOSE_TOLERANCE = Decimal("1e-6")


@dataclass(frozen=True)
class PositionRecon:
    gross_bought_qty: Decimal
    gross_sold_qty: Decimal
    net_qty: Decimal
    net_cash: Decimal
    status: str  # closed | open_long | incomplete


def _event_qty(event: dict[str, Any]) -> Decimal:
    return abs(parse_dollar(event.get("quantity")))


def reconcile_positions(events: list[dict[str, Any]]) -> dict[str, PositionRecon]:
    """Per-symbol quantity and cash reconciliation from trade events."""
    buckets: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {
            "bought": Decimal("0"),
            "sold": Decimal("0"),
            "net_cash": Decimal("0"),
        }
    )

    for event in events:
        event_type = event.get("event_type")
        if event_type not in _RECON_EVENT_TYPES:
            continue
        symbol = event.get("symbol")
        if not symbol:
            continue

        bucket = buckets[symbol]
        bucket["net_cash"] += parse_dollar(event.get("amount"))

        if event_type == "option_expired":
            raw_qty = parse_dollar(event.get("quantity"))
            if raw_qty < 0:
                bucket["bought"] += abs(raw_qty)
            else:
                bucket["sold"] += abs(raw_qty)
            continue

        side = (event.get("side") or "").lower()
        qty = _event_qty(event)
        if side == "buy":
            bucket["bought"] += qty
        elif side == "sell":
            bucket["sold"] += qty

    result: dict[str, PositionRecon] = {}
    for symbol, bucket in buckets.items():
        gross_bought = bucket["bought"]
        gross_sold = bucket["sold"]
        net_qty = gross_bought - gross_sold
        abs_net = abs(net_qty)
        if abs_net < _CLOSE_TOLERANCE:
            status = "closed"
        elif net_qty > 0:
            status = "open_long"
        else:
            status = "incomplete"

        result[symbol] = PositionRecon(
            gross_bought_qty=gross_bought,
            gross_sold_qty=gross_sold,
            net_qty=net_qty,
            net_cash=bucket["net_cash"],
            status=status,
        )
    return result


def _position_recon_to_dict(recon: PositionRecon) -> dict[str, str]:
    return {
        "gross_bought_qty": decimal_to_str(recon.gross_bought_qty),
        "gross_sold_qty": decimal_to_str(recon.gross_sold_qty),
        "net_qty": decimal_to_str(recon.net_qty),
        "net_cash": decimal_to_str(recon.net_cash),
        "status": recon.status,
    }


def realized_summary(
    events: list[dict[str, Any]],
    *,
    latest_prices: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    """Net-cash realized P&L headline plus per-symbol reconciliation."""
    recon_by_symbol = reconcile_positions(events)

    per_symbol: list[dict[str, Any]] = []
    open_positions: list[dict[str, Any]] = []
    incomplete_positions: list[dict[str, Any]] = []
    realized_closed = Decimal("0")
    closed_wins = 0
    closed_count = 0

    for symbol in sorted(recon_by_symbol):
        recon = recon_by_symbol[symbol]
        entry: dict[str, Any] = {
            "symbol": symbol,
            "net_cash": decimal_to_str(recon.net_cash),
            "status": recon.status,
            "net_qty": decimal_to_str(recon.net_qty),
        }
        per_symbol.append(entry)

        if recon.status == "closed":
            realized_closed += recon.net_cash
            closed_count += 1
            if recon.net_cash > 0:
                closed_wins += 1
        elif recon.status == "open_long":
            open_entry: dict[str, Any] = {
                "symbol": symbol,
                "net_qty": decimal_to_str(recon.net_qty),
                "net_cash": decimal_to_str(recon.net_cash),
                "status": recon.status,
            }
            if latest_prices and symbol in latest_prices:
                price = latest_prices[symbol]
                mtm = recon.net_qty * price + recon.net_cash
                open_entry["latest_price"] = decimal_to_str(price)
                open_entry["approximate_unrealized_mtm"] = decimal_to_str(mtm)
            open_positions.append(open_entry)
        else:
            incomplete_positions.append(
                {
                    "symbol": symbol,
                    "net_qty": decimal_to_str(recon.net_qty),
                    "net_cash": decimal_to_str(recon.net_cash),
                    "status": recon.status,
                    "note": "needs Schwab Realized G/L (cost basis outside export window)",
                }
            )

    closed_win_rate = (
        round(closed_wins / closed_count, 4) if closed_count else None
    )

    reliability: dict[str, str] = {
        "realized_closed": "exact",
        "closed_win_rate": "exact (per-symbol, not per-trade)",
        "per_symbol": "exact for closed; open/incomplete flagged",
        "open_positions": (
            "approximate (MTM when latest_prices supplied)"
            if latest_prices
            else "partial — still holding; realized-so-far not derivable"
        ),
        "incomplete_positions": "needs external cost basis",
    }

    return {
        "realized_closed": decimal_to_str(realized_closed),
        "per_symbol": per_symbol,
        "closed_win_rate": closed_win_rate,
        "open_positions": open_positions,
        "incomplete_positions": incomplete_positions,
        "reliability": reliability,
        "reconciliation": {
            symbol: _position_recon_to_dict(recon)
            for symbol, recon in sorted(recon_by_symbol.items())
        },
    }
