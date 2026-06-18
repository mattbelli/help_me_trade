"""Performance analytics: periods, round-trips, AI insight bundles."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from db.trades import daily_trade_stats, list_trade_events, trade_summary_stats
from imports.money import decimal_to_str, parse_dollar

PERIODS = frozenset(
    {"day", "week", "month", "quarter", "year", "all", "custom"}
)

OVERTRADING_TRADES_PER_DAY = 8
CONCENTRATION_PCT = 0.5
REVENGE_DAYS = 1


def resolve_period(
    period: str,
    *,
    custom_from: str | None = None,
    custom_to: str | None = None,
) -> tuple[str | None, str | None]:
    today = date.today()
    p = period.strip().lower()
    if p == "custom":
        return custom_from, custom_to
    if p == "day":
        d = today.isoformat()
        return d, d
    if p == "week":
        start = today - timedelta(days=today.weekday())
        return start.isoformat(), today.isoformat()
    if p == "month":
        start = today.replace(day=1)
        return start.isoformat(), today.isoformat()
    if p == "quarter":
        q_month = ((today.month - 1) // 3) * 3 + 1
        start = today.replace(month=q_month, day=1)
        return start.isoformat(), today.isoformat()
    if p == "year":
        start = today.replace(month=1, day=1)
        return start.isoformat(), today.isoformat()
    return None, None  # all


def compute_round_trips(
    trade_events: list[dict[str, Any]],
    *,
    stocks_only: bool = False,
) -> list[dict[str, Any]]:
    """FIFO closed trades with realized P&L."""
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in trade_events:
        symbol = event.get("symbol")
        if not symbol:
            continue
        if stocks_only and " " in symbol:
            continue
        by_symbol[symbol].append(event)

    closed: list[dict[str, Any]] = []
    for symbol, rows in by_symbol.items():
        sorted_rows = sorted(rows, key=lambda r: (r["trade_date"], r["id"]))
        lots: list[tuple[Decimal, Decimal, str]] = []

        for row in sorted_rows:
            side = (row.get("side") or "").lower()
            qty = parse_dollar(row.get("quantity"))
            if qty <= 0:
                continue
            fees = parse_dollar(row.get("fees"))
            amount = parse_dollar(row.get("amount"))
            trade_date = row["trade_date"]

            if side == "buy":
                cost_per = (-amount - fees) / qty if qty else Decimal("0")
                lots.append((qty, cost_per, trade_date))
                continue
            if side != "sell":
                continue

            remaining = qty
            proceeds = amount
            sell_fees = fees
            open_date = lots[0][2] if lots else trade_date

            while remaining > 0 and lots:
                lot_qty, lot_cost, lot_date = lots[0]
                open_date = lot_date
                take = min(remaining, lot_qty)
                cost_basis = take * lot_cost
                pnl = (proceeds * take / qty) - cost_basis - (sell_fees * take / qty)
                remaining -= take
                if take == lot_qty:
                    lots.pop(0)
                else:
                    lots[0] = (lot_qty - take, lot_cost, lot_date)

                closed.append(
                    {
                        "symbol": symbol,
                        "open_date": open_date,
                        "close_date": trade_date,
                        "quantity": decimal_to_str(take),
                        "pnl": decimal_to_str(pnl),
                        "is_option": " " in symbol,
                    }
                )
    closed.sort(key=lambda r: r["close_date"], reverse=True)
    return closed


def asset_class_breakdown(events: list[dict[str, Any]]) -> dict[str, Any]:
    stock_trades = 0
    option_trades = 0
    other = 0
    for e in events:
        if e.get("event_type") != "trade":
            other += 1
            continue
        sym = e.get("symbol") or ""
        if " " in sym:
            option_trades += 1
        else:
            stock_trades += 1
    total = stock_trades + option_trades + other
    return {
        "stock_trades": stock_trades,
        "option_trades": option_trades,
        "other_events": other,
        "stock_pct": round(stock_trades / total, 4) if total else None,
        "option_pct": round(option_trades / total, 4) if total else None,
    }


def day_of_week_stats(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_dow: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"net_amount": Decimal("0"), "trade_count": 0}
    )
    for e in events:
        if e.get("event_type") != "trade":
            continue
        try:
            dow = datetime.fromisoformat(e["trade_date"]).weekday()
        except ValueError:
            continue
        by_dow[dow]["net_amount"] += parse_dollar(e["amount"])
        by_dow[dow]["trade_count"] += 1
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return [
        {
            "day": names[dow],
            "net_amount": decimal_to_str(data["net_amount"]),
            "trade_count": data["trade_count"],
        }
        for dow, data in sorted(by_dow.items())
    ]


def daily_realized_stats(
    round_trips: list[dict[str, Any]],
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    """Daily realized P&L from closed round-trips (by close_date)."""
    by_day: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    counts: dict[str, int] = defaultdict(int)
    for rt in round_trips:
        day = rt["close_date"]
        if from_date and day < from_date:
            continue
        if to_date and day > to_date:
            continue
        by_day[day] += parse_dollar(rt["pnl"])
        counts[day] += 1

    return [
        {
            "date": day,
            "net_amount": decimal_to_str(amount),
            "event_count": counts[day],
            "close_count": counts[day],
        }
        for day, amount in sorted(by_day.items())
    ]


def day_of_week_realized(round_trips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_dow: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"net_amount": Decimal("0"), "trade_count": 0}
    )
    for rt in round_trips:
        try:
            dow = datetime.fromisoformat(rt["close_date"]).weekday()
        except ValueError:
            continue
        by_dow[dow]["net_amount"] += parse_dollar(rt["pnl"])
        by_dow[dow]["trade_count"] += 1
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return [
        {
            "day": names[dow],
            "net_amount": decimal_to_str(data["net_amount"]),
            "trade_count": data["trade_count"],
        }
        for dow, data in sorted(by_dow.items())
    ]


def compute_profit_factor(round_trips: list[dict[str, Any]]) -> dict[str, Any]:
    gross_wins = Decimal("0")
    gross_losses = Decimal("0")
    for rt in round_trips:
        pnl = parse_dollar(rt["pnl"])
        if pnl > 0:
            gross_wins += pnl
        elif pnl < 0:
            gross_losses += -pnl

    profit_factor: float | None = None
    if gross_losses > 0:
        profit_factor = round(float(gross_wins / gross_losses), 2)

    return {
        "gross_wins": decimal_to_str(gross_wins),
        "gross_losses": decimal_to_str(gross_losses),
        "profit_factor": profit_factor,
    }


def compute_hold_times(round_trips: list[dict[str, Any]]) -> dict[str, Any]:
    if not round_trips:
        return {
            "avg_hold_days": None,
            "min_hold_days": None,
            "max_hold_days": None,
            "count": 0,
        }

    hold_days: list[int] = []
    for rt in round_trips:
        open_d = date.fromisoformat(rt["open_date"])
        close_d = date.fromisoformat(rt["close_date"])
        hold_days.append((close_d - open_d).days)

    total = sum(hold_days)
    return {
        "avg_hold_days": round(total / len(hold_days), 1),
        "min_hold_days": min(hold_days),
        "max_hold_days": max(hold_days),
        "count": len(hold_days),
    }


def compute_streaks(round_trips: list[dict[str, Any]]) -> dict[str, Any]:
    if not round_trips:
        return {
            "longest_win": 0,
            "longest_loss": 0,
            "current_type": "none",
            "current_len": 0,
        }

    ordered = sorted(round_trips, key=lambda r: r["close_date"])
    longest_win = 0
    longest_loss = 0
    cur_win = 0
    cur_loss = 0

    for rt in ordered:
        pnl = parse_dollar(rt["pnl"])
        if pnl > 0:
            cur_win += 1
            cur_loss = 0
            longest_win = max(longest_win, cur_win)
        elif pnl < 0:
            cur_loss += 1
            cur_win = 0
            longest_loss = max(longest_loss, cur_loss)
        else:
            cur_win = 0
            cur_loss = 0

    if cur_win > 0:
        current_type = "win"
        current_len = cur_win
    elif cur_loss > 0:
        current_type = "loss"
        current_len = cur_loss
    else:
        current_type = "none"
        current_len = 0

    return {
        "longest_win": longest_win,
        "longest_loss": longest_loss,
        "current_type": current_type,
        "current_len": current_len,
    }


def compute_behavioral_flags(
    round_trips: list[dict[str, Any]],
    daily_stats: list[dict[str, Any]],
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []

    # Heuristic: unusually high mean trade-event count on days with activity.
    if daily_stats:
        active_counts = [d["event_count"] for d in daily_stats if d["event_count"] > 0]
        if active_counts:
            mean_events = sum(active_counts) / len(active_counts)
            if mean_events > OVERTRADING_TRADES_PER_DAY:
                flags.append(
                    {
                        "key": "overtrading",
                        "label": "Overtrading",
                        "detail": (
                            f"Average {mean_events:.1f} trade events per active day "
                            f"(threshold {OVERTRADING_TRADES_PER_DAY})."
                        ),
                    }
                )

    # Heuristic: one symbol dominates closed round-trip count.
    if round_trips:
        by_symbol: dict[str, int] = defaultdict(int)
        for rt in round_trips:
            by_symbol[rt["symbol"]] += 1
        top_symbol = max(by_symbol, key=lambda s: by_symbol[s])
        top_count = by_symbol[top_symbol]
        top_pct = top_count / len(round_trips)
        if top_pct > CONCENTRATION_PCT:
            flags.append(
                {
                    "key": "concentration",
                    "label": "Concentration",
                    "detail": (
                        f"{top_symbol} accounts for {top_pct:.0%} of closed "
                        f"round-trips ({top_count}/{len(round_trips)})."
                    ),
                }
            )

    # Heuristic: re-entering a symbol shortly after a losing close.
    revenge_symbols: list[str] = []
    by_symbol_trips: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rt in round_trips:
        by_symbol_trips[rt["symbol"]].append(rt)
    for symbol, trips in by_symbol_trips.items():
        ordered = sorted(trips, key=lambda r: r["close_date"])
        for idx in range(len(ordered) - 1):
            if parse_dollar(ordered[idx]["pnl"]) >= 0:
                continue
            loss_close = date.fromisoformat(ordered[idx]["close_date"])
            next_open = date.fromisoformat(ordered[idx + 1]["open_date"])
            if (next_open - loss_close).days <= REVENGE_DAYS:
                revenge_symbols.append(symbol)
                break
    if revenge_symbols:
        symbols = ", ".join(sorted(set(revenge_symbols)))
        flags.append(
            {
                "key": "revenge_trading",
                "label": "Revenge trading",
                "detail": (
                    f"Re-opened within {REVENGE_DAYS} day(s) after a losing close: "
                    f"{symbols}."
                ),
            }
        )

    return flags


def _round_trips_in_period(
    *,
    account_id: str | None,
    from_date: str | None,
    to_date: str | None,
) -> list[dict[str, Any]]:
    history_events = list_trade_events(
        account_id=account_id,
        to_date=to_date,
        limit=100_000,
    )
    history_trades = [e for e in history_events if e["event_type"] == "trade"]
    round_trips_all = compute_round_trips(history_trades)
    return [
        rt
        for rt in round_trips_all
        if (not from_date or rt["close_date"] >= from_date)
        and (not to_date or rt["close_date"] <= to_date)
    ]


def performance_dashboard(
    *,
    account_id: str | None = None,
    period: str = "all",
    custom_from: str | None = None,
    custom_to: str | None = None,
) -> dict[str, Any]:
    from_date, to_date = resolve_period(
        period, custom_from=custom_from, custom_to=custom_to
    )
    events = list_trade_events(
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
        limit=100_000,
    )
    # FIFO needs full history through to_date so sells match earlier buys.
    history_events = list_trade_events(
        account_id=account_id,
        to_date=to_date,
        limit=100_000,
    )
    history_trades = [e for e in history_events if e["event_type"] == "trade"]
    summary = trade_summary_stats(
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
    )
    round_trips_all = compute_round_trips(history_trades)
    round_trips_in_period = [
        rt
        for rt in round_trips_all
        if (not from_date or rt["close_date"] >= from_date)
        and (not to_date or rt["close_date"] <= to_date)
    ]
    stock_round_trips = [rt for rt in round_trips_in_period if not rt["is_option"]]
    wins = sum(1 for r in stock_round_trips if parse_dollar(r["pnl"]) > 0)
    realized_total = sum(parse_dollar(rt["pnl"]) for rt in round_trips_in_period)
    daily_cash_flow = daily_trade_stats(
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
    )
    daily_realized = daily_realized_stats(
        round_trips_all, from_date=from_date, to_date=to_date
    )

    return {
        "period": period,
        "from_date": from_date,
        "to_date": to_date,
        "summary": summary,
        "realized_pnl": decimal_to_str(realized_total),
        "cash_flow_pnl": summary["net_amount"],
        "daily_realized": daily_realized,
        "daily_cash_flow": daily_cash_flow,
        "round_trips": round_trips_in_period[:100],
        "stock_win_rate": round(wins / len(stock_round_trips), 4)
        if stock_round_trips
        else None,
        "asset_classes": asset_class_breakdown(events),
        "day_of_week": day_of_week_stats(events),
        "day_of_week_realized": day_of_week_realized(round_trips_in_period),
        "top_winners": sorted(
            stock_round_trips,
            key=lambda r: parse_dollar(r["pnl"]),
            reverse=True,
        )[:5],
        "top_losers": sorted(
            stock_round_trips,
            key=lambda r: parse_dollar(r["pnl"]),
        )[:5],
    }


def ai_performance_context(
    *,
    account_id: str | None = None,
    period: str = "year",
) -> dict[str, Any]:
    """Compact structured context for agent tools and reflection."""
    dash = performance_dashboard(account_id=account_id, period=period)
    from db.trades import symbol_trade_stats

    symbols = symbol_trade_stats(
        account_id=account_id,
        from_date=dash["from_date"],
        to_date=dash["to_date"],
    )[:10]

    round_trips_in_period = _round_trips_in_period(
        account_id=account_id,
        from_date=dash["from_date"],
        to_date=dash["to_date"],
    )

    return {
        "period": period,
        "from_date": dash["from_date"],
        "to_date": dash["to_date"],
        "summary": dash["summary"],
        "asset_classes": dash["asset_classes"],
        "day_of_week": dash["day_of_week"],
        "top_symbols": symbols,
        "recent_closed_trades": dash["round_trips"][:15],
        "largest_wins": dash["top_winners"][:3],
        "largest_losses": dash["top_losers"][:3],
        "profit_factor": compute_profit_factor(round_trips_in_period),
        "hold_time": compute_hold_times(round_trips_in_period),
        "streaks": compute_streaks(round_trips_in_period),
        "behavioral_flags": compute_behavioral_flags(
            round_trips_in_period,
            dash["daily_cash_flow"],
        ),
    }
