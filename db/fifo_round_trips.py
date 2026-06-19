"""FIFO round-trip matching for equities and options."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from db.corporate_actions import (
    SplitEvent,
    SplitFactorsFn,
    cumulative_split_ratio,
    get_split_factors,
)
from imports.money import decimal_to_str, parse_dollar
from imports.option_symbol import is_option_symbol

_FIFO_EVENT_TYPES = frozenset({"trade", "option_expired"})


def _fifo_events_by_symbol(
    events: list[dict[str, Any]],
    *,
    stocks_only: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event.get("event_type") not in _FIFO_EVENT_TYPES:
            continue
        symbol = event.get("symbol")
        if not symbol:
            continue
        if stocks_only and is_option_symbol(symbol):
            continue
        by_symbol[symbol].append(event)
    return by_symbol


def _equity_symbols(symbols: Iterable[str]) -> set[str]:
    return {s for s in symbols if s and not is_option_symbol(s)}


def _resolve_splits_map(
    symbols: Iterable[str],
    split_factors: SplitFactorsFn | None,
) -> dict[str, list[SplitEvent]]:
    equity = _equity_symbols(symbols)
    if not equity:
        return {}
    fn = split_factors or get_split_factors
    return fn(equity)


def _split_adjust_rows(
    rows: list[dict[str, Any]],
    splits: list[SplitEvent],
) -> list[dict[str, Any]]:
    if not splits:
        return rows

    adjusted: list[dict[str, Any]] = []
    for row in rows:
        event_date = date.fromisoformat(row["trade_date"])
        ratio = cumulative_split_ratio(splits, event_date)
        if ratio == Decimal("1"):
            adjusted.append(row)
            continue

        new_row = dict(row)
        qty = parse_dollar(row.get("quantity"))
        if qty > 0:
            new_row["quantity"] = decimal_to_str(qty * ratio)

        price_raw = row.get("price")
        if price_raw is not None:
            price = parse_dollar(price_raw)
            if price > 0:
                new_row["price"] = decimal_to_str(price / ratio)

        adjusted.append(new_row)
    return adjusted


def _rows_for_fifo(
    symbol: str,
    rows: list[dict[str, Any]],
    splits_map: dict[str, list[SplitEvent]],
) -> list[dict[str, Any]]:
    if is_option_symbol(symbol):
        # Option corporate actions deferred to R1d.
        return rows
    splits = splits_map.get(symbol.upper(), [])
    return _split_adjust_rows(rows, splits)


def _close_long_lots(
    *,
    symbol: str,
    trade_date: str,
    remaining: Decimal,
    proceeds: Decimal,
    close_fees: Decimal,
    close_qty: Decimal,
    lots: list[tuple[Decimal, Decimal, str]],
    closed: list[dict[str, Any]] | None,
) -> Decimal:
    """Match a closing sell against open long lots. Returns unmatched quantity."""
    while remaining > 0 and lots:
        lot_qty, lot_cost, lot_date = lots[0]
        open_date = lot_date
        take = min(remaining, lot_qty)
        cost_basis = take * lot_cost
        if closed is not None:
            pnl = (proceeds * take / close_qty) - cost_basis - (close_fees * take / close_qty)
            closed.append(
                {
                    "symbol": symbol,
                    "open_date": open_date,
                    "close_date": trade_date,
                    "quantity": decimal_to_str(take),
                    "pnl": decimal_to_str(pnl),
                    "is_option": is_option_symbol(symbol),
                    "asset_class": "option" if is_option_symbol(symbol) else "equity",
                }
            )
        remaining -= take
        if take == lot_qty:
            lots.pop(0)
        else:
            lots[0] = (lot_qty - take, lot_cost, lot_date)
    return remaining


def _close_short_lots(
    *,
    symbol: str,
    trade_date: str,
    remaining: Decimal,
    close_cost: Decimal,
    close_fees: Decimal,
    close_qty: Decimal,
    short_lots: list[tuple[Decimal, Decimal, str]],
    closed: list[dict[str, Any]] | None,
) -> Decimal:
    """Match a buy-to-close against open short lots. Returns unmatched quantity."""
    while remaining > 0 and short_lots:
        lot_qty, credit_per, lot_date = short_lots[0]
        open_date = lot_date
        take = min(remaining, lot_qty)
        open_credit = take * credit_per
        if closed is not None:
            paid = (close_cost * take / close_qty) + (close_fees * take / close_qty)
            pnl = open_credit - paid
            closed.append(
                {
                    "symbol": symbol,
                    "open_date": open_date,
                    "close_date": trade_date,
                    "quantity": decimal_to_str(take),
                    "pnl": decimal_to_str(pnl),
                    "is_option": True,
                    "asset_class": "option",
                }
            )
        remaining -= take
        if take == lot_qty:
            short_lots.pop(0)
        else:
            short_lots[0] = (lot_qty - take, credit_per, lot_date)
    return remaining


def _process_option_expired(
    *,
    symbol: str,
    row: dict[str, Any],
    long_lots: list[tuple[Decimal, Decimal, str]],
    short_lots: list[tuple[Decimal, Decimal, str]],
    closed: list[dict[str, Any]] | None,
    stats: dict[str, int] | None,
) -> None:
    trade_date = row["trade_date"]
    raw_qty = parse_dollar(row.get("quantity"))
    qty = abs(raw_qty)
    if qty <= 0:
        return

    if raw_qty < 0:
        remaining = qty
        while remaining > 0 and short_lots:
            lot_qty, credit_per, lot_date = short_lots[0]
            open_date = lot_date
            take = min(remaining, lot_qty)
            if closed is not None:
                pnl = take * credit_per
                closed.append(
                    {
                        "symbol": symbol,
                        "open_date": open_date,
                        "close_date": trade_date,
                        "quantity": decimal_to_str(take),
                        "pnl": decimal_to_str(pnl),
                        "is_option": True,
                        "asset_class": "option",
                    }
                )
            if stats is not None:
                stats["round_trips"] += 1
                if take * credit_per > 0:
                    stats["wins"] += 1
                elif take * credit_per < 0:
                    stats["losses"] += 1
            remaining -= take
            if take == lot_qty:
                short_lots.pop(0)
            else:
                short_lots[0] = (lot_qty - take, credit_per, lot_date)
        return

    remaining = qty
    while remaining > 0 and long_lots:
        lot_qty, lot_cost, lot_date = long_lots[0]
        open_date = lot_date
        take = min(remaining, lot_qty)
        cost_basis = take * lot_cost
        if closed is not None:
            pnl = -cost_basis
            closed.append(
                {
                    "symbol": symbol,
                    "open_date": open_date,
                    "close_date": trade_date,
                    "quantity": decimal_to_str(take),
                    "pnl": decimal_to_str(pnl),
                    "is_option": True,
                    "asset_class": "option",
                }
            )
        if stats is not None:
            stats["round_trips"] += 1
            if -cost_basis > 0:
                stats["wins"] += 1
            elif -cost_basis < 0:
                stats["losses"] += 1
        remaining -= take
        if take == lot_qty:
            long_lots.pop(0)
        else:
            long_lots[0] = (lot_qty - take, lot_cost, lot_date)


def _process_fifo_rows(
    symbol: str,
    rows: list[dict[str, Any]],
    *,
    collect_closed: bool,
    collect_stats: bool,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    sorted_rows = sorted(rows, key=lambda r: (r["trade_date"], r["id"]))
    long_lots: list[tuple[Decimal, Decimal, str]] = []
    short_lots: list[tuple[Decimal, Decimal, str]] = []
    closed: list[dict[str, Any]] = [] if collect_closed else []
    stats = {"wins": 0, "losses": 0, "round_trips": 0} if collect_stats else {}

    for row in sorted_rows:
        event_type = row.get("event_type")
        trade_date = row["trade_date"]
        option = is_option_symbol(symbol)

        if event_type == "option_expired":
            _process_option_expired(
                symbol=symbol,
                row=row,
                long_lots=long_lots,
                short_lots=short_lots,
                closed=closed if collect_closed else None,
                stats=stats if collect_stats else None,
            )
            continue

        side = (row.get("side") or "").lower()
        qty = parse_dollar(row.get("quantity"))
        if qty <= 0:
            continue
        fees = parse_dollar(row.get("fees"))
        amount = parse_dollar(row.get("amount"))

        if side == "buy":
            if option and short_lots:
                remaining = _close_short_lots(
                    symbol=symbol,
                    trade_date=trade_date,
                    remaining=qty,
                    close_cost=-amount,
                    close_fees=fees,
                    close_qty=qty,
                    short_lots=short_lots,
                    closed=closed if collect_closed else None,
                )
                if remaining > 0:
                    cost_per = (-amount - fees) / qty if qty else Decimal("0")
                    long_lots.append((remaining, cost_per, trade_date))
            else:
                cost_per = (-amount - fees) / qty if qty else Decimal("0")
                long_lots.append((qty, cost_per, trade_date))
            continue

        if side != "sell":
            continue

        if long_lots:
            remaining = qty
            proceeds = amount
            sell_fees = fees
            matched_qty = Decimal("0")
            cost_basis = Decimal("0")

            while remaining > 0 and long_lots:
                lot_qty, lot_cost, lot_date = long_lots[0]
                take = min(remaining, lot_qty)
                if collect_closed:
                    open_date = lot_date
                    partial_cost = take * lot_cost
                    pnl = (proceeds * take / qty) - partial_cost - (sell_fees * take / qty)
                    closed.append(
                        {
                            "symbol": symbol,
                            "open_date": open_date,
                            "close_date": trade_date,
                            "quantity": decimal_to_str(take),
                            "pnl": decimal_to_str(pnl),
                            "is_option": option,
                            "asset_class": "option" if option else "equity",
                        }
                    )
                else:
                    matched_qty += take
                    cost_basis += take * lot_cost
                remaining -= take
                if take == lot_qty:
                    long_lots.pop(0)
                else:
                    long_lots[0] = (lot_qty - take, lot_cost, lot_date)

            if collect_stats and not collect_closed:
                if matched_qty > 0:
                    pnl = proceeds - cost_basis - sell_fees
                    stats["round_trips"] += 1
                    if pnl > 0:
                        stats["wins"] += 1
                    elif pnl < 0:
                        stats["losses"] += 1
            continue

        if option:
            credit_per = (amount - fees) / qty if qty else Decimal("0")
            short_lots.append((qty, credit_per, trade_date))

    return closed, stats


def fifo_round_trip_stats(
    trade_events: list[dict[str, Any]],
    *,
    split_factors: SplitFactorsFn | None = None,
) -> tuple[int, int, int]:
    """FIFO round-trip win/loss counts (equities + options)."""
    by_symbol = _fifo_events_by_symbol(trade_events)
    splits_map = _resolve_splits_map(by_symbol.keys(), split_factors)
    wins = 0
    losses = 0
    round_trips = 0

    for symbol, rows in by_symbol.items():
        _, stats = _process_fifo_rows(
            symbol,
            _rows_for_fifo(symbol, rows, splits_map),
            collect_closed=False,
            collect_stats=True,
        )
        wins += stats["wins"]
        losses += stats["losses"]
        round_trips += stats["round_trips"]

    return wins, losses, round_trips


def fifo_compute_round_trips(
    trade_events: list[dict[str, Any]],
    *,
    stocks_only: bool = False,
    split_factors: SplitFactorsFn | None = None,
) -> list[dict[str, Any]]:
    """FIFO closed trades with realized P&L."""
    by_symbol = _fifo_events_by_symbol(trade_events, stocks_only=stocks_only)
    splits_map = _resolve_splits_map(by_symbol.keys(), split_factors)
    closed: list[dict[str, Any]] = []

    for symbol, rows in by_symbol.items():
        symbol_closed, _ = _process_fifo_rows(
            symbol,
            _rows_for_fifo(symbol, rows, splits_map),
            collect_closed=True,
            collect_stats=False,
        )
        closed.extend(symbol_closed)

    closed.sort(key=lambda r: r["close_date"], reverse=True)
    return closed


def fifo_compute_open_lots(
    trade_events: list[dict[str, Any]],
    *,
    stocks_only: bool = False,
    split_factors: SplitFactorsFn | None = None,
) -> dict[str, dict[str, str]]:
    """FIFO open lots aggregated per symbol."""
    by_symbol = _fifo_events_by_symbol(trade_events, stocks_only=stocks_only)
    splits_map = _resolve_splits_map(by_symbol.keys(), split_factors)
    open_lots: dict[str, dict[str, str]] = {}

    for symbol, rows in by_symbol.items():
        sorted_rows = sorted(
            _rows_for_fifo(symbol, rows, splits_map),
            key=lambda r: (r["trade_date"], r["id"]),
        )
        long_lots: list[tuple[Decimal, Decimal, str]] = []
        short_lots: list[tuple[Decimal, Decimal, str]] = []
        option = is_option_symbol(symbol)

        for row in sorted_rows:
            event_type = row.get("event_type")
            trade_date = row["trade_date"]

            if event_type == "option_expired":
                raw_qty = parse_dollar(row.get("quantity"))
                qty = abs(raw_qty)
                if qty <= 0:
                    continue
                if raw_qty < 0:
                    remaining = qty
                    while remaining > 0 and short_lots:
                        lot_qty, credit_per, lot_date = short_lots[0]
                        take = min(remaining, lot_qty)
                        remaining -= take
                        if take == lot_qty:
                            short_lots.pop(0)
                        else:
                            short_lots[0] = (lot_qty - take, credit_per, lot_date)
                else:
                    remaining = qty
                    while remaining > 0 and long_lots:
                        lot_qty, lot_cost, lot_date = long_lots[0]
                        take = min(remaining, lot_qty)
                        remaining -= take
                        if take == lot_qty:
                            long_lots.pop(0)
                        else:
                            long_lots[0] = (lot_qty - take, lot_cost, lot_date)
                continue

            side = (row.get("side") or "").lower()
            qty = parse_dollar(row.get("quantity"))
            if qty <= 0:
                continue
            fees = parse_dollar(row.get("fees"))
            amount = parse_dollar(row.get("amount"))

            if side == "buy":
                if option and short_lots:
                    remaining = qty
                    while remaining > 0 and short_lots:
                        lot_qty, credit_per, lot_date = short_lots[0]
                        take = min(remaining, lot_qty)
                        remaining -= take
                        if take == lot_qty:
                            short_lots.pop(0)
                        else:
                            short_lots[0] = (lot_qty - take, credit_per, lot_date)
                    if remaining > 0:
                        cost_per = (-amount - fees) / qty if qty else Decimal("0")
                        long_lots.append((remaining, cost_per, trade_date))
                else:
                    cost_per = (-amount - fees) / qty if qty else Decimal("0")
                    long_lots.append((qty, cost_per, trade_date))
                continue

            if side != "sell":
                continue

            if long_lots:
                remaining = qty
                while remaining > 0 and long_lots:
                    lot_qty, lot_cost, lot_date = long_lots[0]
                    take = min(remaining, lot_qty)
                    remaining -= take
                    if take == lot_qty:
                        long_lots.pop(0)
                    else:
                        long_lots[0] = (lot_qty - take, lot_cost, lot_date)
                continue

            if option:
                credit_per = (amount - fees) / qty if qty else Decimal("0")
                short_lots.append((qty, credit_per, trade_date))

        if not long_lots and not short_lots:
            continue

        if short_lots and not long_lots:
            total_qty = sum(lot_qty for lot_qty, _, _ in short_lots)
            credit_basis = sum(lot_qty * credit_per for lot_qty, credit_per, _ in short_lots)
            avg_credit = credit_basis / total_qty if total_qty else Decimal("0")
            open_lots[symbol] = {
                "quantity": decimal_to_str(-total_qty),
                "avg_cost": decimal_to_str(avg_credit),
                "cost_basis": decimal_to_str(-credit_basis),
            }
            continue

        total_qty = sum(lot_qty for lot_qty, _, _ in long_lots)
        cost_basis = sum(lot_qty * lot_cost for lot_qty, lot_cost, _ in long_lots)
        avg_cost = cost_basis / total_qty if total_qty else Decimal("0")
        open_lots[symbol] = {
            "quantity": decimal_to_str(total_qty),
            "avg_cost": decimal_to_str(avg_cost),
            "cost_basis": decimal_to_str(cost_basis),
        }

    return open_lots
