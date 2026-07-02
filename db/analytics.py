"""Performance analytics: periods, round-trips, AI insight bundles."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from db.behavioral import behavioral_facts
from db.fifo_round_trips import fifo_compute_open_lots, fifo_compute_round_trips
from db.realized_pnl import _CLOSE_TOLERANCE, _RECON_EVENT_TYPES, realized_summary
from db.trades import daily_trade_stats, list_trade_events, trade_summary_stats
from imports.money import decimal_to_str, parse_dollar
from imports.option_symbol import is_option_symbol

PERIODS = frozenset(
    {"day", "week", "month", "quarter", "year", "all", "custom"}
)

OVERTRADING_TRADES_PER_DAY = 8
CONCENTRATION_PCT = 0.5
REVENGE_DAYS = 1

# Trading day boundaries follow US market time, not the server's UTC clock:
# on a UTC host, date.today() rolls to "tomorrow" at 8pm ET, which made the
# "Day" performance window jump to a future date with no trades (showing $0).
_ET = ZoneInfo("America/New_York")


def resolve_period(
    period: str,
    *,
    custom_from: str | None = None,
    custom_to: str | None = None,
) -> tuple[str | None, str | None]:
    today = datetime.now(_ET).date()
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
    return fifo_compute_round_trips(trade_events, stocks_only=stocks_only)


def compute_open_lots(
    trade_events: list[dict[str, Any]],
    *,
    stocks_only: bool = False,
) -> dict[str, dict[str, str]]:
    """FIFO open lots aggregated per symbol (full trade history, not period-scoped)."""
    return fifo_compute_open_lots(trade_events, stocks_only=stocks_only)


def compute_unrealized(
    open_lots: dict[str, dict[str, str]],
    snapshot_positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Mark open ledger lots to latest snapshot prices (broker values authoritative)."""
    snapshot_by_symbol: dict[str, dict[str, Any]] = {}
    for pos in snapshot_positions:
        symbol = pos.get("symbol")
        if symbol:
            snapshot_by_symbol[symbol] = pos

    by_symbol: dict[str, dict[str, Any]] = {}
    missing_price_symbols: list[str] = []
    totals_cost = Decimal("0")
    totals_mv = Decimal("0")
    totals_ledger = Decimal("0")
    totals_broker = Decimal("0")

    for symbol, lot in open_lots.items():
        snap = snapshot_by_symbol.get(symbol)
        if snap is None:
            missing_price_symbols.append(symbol)
            continue

        cost_basis = parse_dollar(lot["cost_basis"])
        market_value = Decimal(str(snap.get("market_value", 0)))
        broker_pl = Decimal(str(snap.get("unrealized_pl", 0)))
        ledger_pl = market_value - cost_basis

        by_symbol[symbol] = {
            "quantity": lot["quantity"],
            "cost_basis": lot["cost_basis"],
            "market_value": decimal_to_str(market_value),
            "ledger_unrealized_pl": decimal_to_str(ledger_pl),
            "broker_unrealized_pl": decimal_to_str(broker_pl),
        }
        totals_cost += cost_basis
        totals_mv += market_value
        totals_ledger += ledger_pl
        totals_broker += broker_pl

    for symbol, snap in snapshot_by_symbol.items():
        if symbol in open_lots:
            continue
        broker_pl = Decimal(str(snap.get("unrealized_pl", 0)))
        by_symbol[symbol] = {
            "cost_basis": None,
            "ledger_unrealized_pl": None,
            "broker_unrealized_pl": decimal_to_str(broker_pl),
        }

    missing_price_symbols.sort()

    return {
        "by_symbol": by_symbol,
        "totals": {
            "cost_basis": decimal_to_str(totals_cost),
            "market_value": decimal_to_str(totals_mv),
            "ledger_unrealized_pl": decimal_to_str(totals_ledger),
            "broker_unrealized_pl": decimal_to_str(totals_broker),
        },
        "missing_price_symbols": missing_price_symbols,
    }


def asset_class_breakdown(events: list[dict[str, Any]]) -> dict[str, Any]:
    stock_trades = 0
    option_trades = 0
    other = 0
    for e in events:
        if e.get("event_type") != "trade":
            other += 1
            continue
        sym = e.get("symbol") or ""
        if is_option_symbol(sym):
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


def realized_asset_class_breakdown(
    round_trips: list[dict[str, Any]],
) -> dict[str, Any]:
    equity_trips = [rt for rt in round_trips if not rt.get("is_option")]
    option_trips = [rt for rt in round_trips if rt.get("is_option")]

    def _summary(trips: list[dict[str, Any]]) -> dict[str, Any]:
        wins = sum(1 for rt in trips if parse_dollar(rt["pnl"]) > 0)
        losses = sum(1 for rt in trips if parse_dollar(rt["pnl"]) < 0)
        total_pnl = sum(parse_dollar(rt["pnl"]) for rt in trips)
        return {
            "round_trips": len(trips),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / len(trips), 4) if trips else None,
            "realized_pnl": decimal_to_str(total_pnl),
        }

    return {
        "equity": _summary(equity_trips),
        "options": _summary(option_trips),
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


def _relevant_trade_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["event_type"] in ("trade", "option_expired")]


def _events_through(
    events: list[dict[str, Any]], through_date: str | None
) -> list[dict[str, Any]]:
    if through_date is None:
        return list(events)
    return [e for e in events if e["trade_date"] <= through_date]


def _day_before(iso_date: str) -> str:
    return (date.fromisoformat(iso_date) - timedelta(days=1)).isoformat()


def _recon_qty_delta(event: dict[str, Any]) -> Decimal:
    event_type = event.get("event_type")
    if event_type == "option_expired":
        raw_qty = parse_dollar(event.get("quantity"))
        if raw_qty < 0:
            return abs(raw_qty)
        return -abs(raw_qty)
    side = (event.get("side") or "").lower()
    qty = abs(parse_dollar(event.get("quantity")))
    if side == "buy":
        return qty
    if side == "sell":
        return -qty
    return Decimal("0")


def _last_flat_cash_by_symbol(
    events: list[dict[str, Any]],
) -> dict[tuple[str, str], Decimal]:
    """Cumulative net cash at the latest end-of-day flat, per (account, symbol).

    Keyed by ``(account_id, symbol)`` — the same ticker held in two accounts is
    two independent positions with two cost bases, so an aggregate scope (all
    real / all paper) must never merge them into one running position. For a
    single-account scope the account_id is constant, so this reduces to
    per-symbol tracking.
    """
    if not events:
        return {}

    sorted_events = sorted(events, key=lambda e: e["trade_date"])
    running_qty: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    running_cash: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    last_flat_cash: dict[tuple[str, str], Decimal] = {}

    def _snapshot_flat_symbols() -> None:
        for key, qty in running_qty.items():
            if abs(qty) < _CLOSE_TOLERANCE:
                last_flat_cash[key] = running_cash[key]

    current_day: str | None = None
    for event in sorted_events:
        day = event["trade_date"]
        if current_day is not None and day != current_day:
            _snapshot_flat_symbols()
        current_day = day

        event_type = event.get("event_type")
        if event_type not in _RECON_EVENT_TYPES:
            continue
        symbol = event.get("symbol")
        if not symbol:
            continue
        key = (str(event.get("account_id") or ""), symbol)

        running_cash[key] += parse_dollar(event.get("amount"))
        running_qty[key] += _recon_qty_delta(event)

    if current_day is not None:
        _snapshot_flat_symbols()

    return last_flat_cash


def _last_flat_period_delta(
    start_events: list[dict[str, Any]] | None,
    end_events: list[dict[str, Any]],
) -> Decimal:
    end_flat = _last_flat_cash_by_symbol(end_events)
    start_flat = _last_flat_cash_by_symbol(start_events) if start_events else {}
    keys = set(end_flat) | set(start_flat)
    total = Decimal("0")
    for key in keys:
        total += end_flat.get(key, Decimal("0")) - start_flat.get(key, Decimal("0"))
    return total


def _last_flat_win_rate(
    start_events: list[dict[str, Any]] | None,
    end_events: list[dict[str, Any]],
) -> float | None:
    end_flat = _last_flat_cash_by_symbol(end_events)
    start_flat = _last_flat_cash_by_symbol(start_events) if start_events else {}
    keys = set(end_flat) | set(start_flat)
    wins = 0
    count = 0
    for key in keys:
        delta = end_flat.get(key, Decimal("0")) - start_flat.get(key, Decimal("0"))
        if delta == 0:
            continue
        count += 1
        if delta > 0:
            wins += 1
    return round(wins / count, 4) if count else None


def _all_history_last_flat_win_rate(end_events: list[dict[str, Any]]) -> float | None:
    end_flat = _last_flat_cash_by_symbol(end_events)
    wins = sum(1 for cash in end_flat.values() if cash > 0)
    count = sum(1 for cash in end_flat.values() if cash != 0)
    return round(wins / count, 4) if count else None


def realized_between(
    history_trades: list[dict[str, Any]],
    from_date: str | None,
    to_date: str | None,
    *,
    latest_prices: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    """Net-cash realized in a period via last-flat boundary deltas on full history.

    Recognition point: a symbol's realized P&L books on each day it ends flat
    (running qty 0), so every completed round trip lands in the window where it
    finished — adjoining windows always sum to the covering window. NOTE: this
    can diverge from the closed-symbols-only figures in ``in_window_recon`` /
    ``position_reconciliation`` — a symbol that went flat and later REOPENED
    still contributes its completed round trips here, while closed-only recon
    reports it as open/0 until it closes again. That divergence is intentional
    (a finished round trip is realized whether or not you re-entered).
    """
    del latest_prices  # retained for API compatibility; last-flat uses events only
    end_events = _events_through(history_trades, to_date)

    if from_date is None:
        start_events = None
        closed_win_rate = _all_history_last_flat_win_rate(end_events)
    else:
        start_events = _events_through(history_trades, _day_before(from_date))
        closed_win_rate = _last_flat_win_rate(start_events, end_events)

    realized = _last_flat_period_delta(start_events, end_events)

    return {
        "realized_closed": decimal_to_str(realized),
        "closed_win_rate": closed_win_rate,
    }


def daily_boundary_realized_stats(
    history_trades: list[dict[str, Any]],
    daily_cash_flow: list[dict[str, Any]],
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    latest_prices: dict[str, Decimal] | None = None,
) -> list[dict[str, Any]]:
    """Per-day boundary-delta realized P&L plus honest net-cash from trade events."""
    cash_by_day = {row["date"]: row for row in daily_cash_flow}
    days: set[str] = set(cash_by_day)
    for event in history_trades:
        day = event["trade_date"]
        if from_date and day < from_date:
            continue
        if to_date and day > to_date:
            continue
        days.add(day)

    rows: list[dict[str, Any]] = []
    for day in sorted(days):
        period = realized_between(
            history_trades,
            day,
            day,
            latest_prices=latest_prices,
        )
        cash = cash_by_day.get(day, {})
        rows.append(
            {
                "date": day,
                "realized": period["realized_closed"],
                "net_amount": cash.get("net_amount", "0"),
                "event_count": cash.get("event_count", 0),
            }
        )
    return rows


def _round_trips_in_period(
    *,
    account_id: str | None,
    from_date: str | None,
    to_date: str | None,
    real_only: bool = False,
    paper_only: bool = False,
) -> list[dict[str, Any]]:
    history_events = list_trade_events(
        account_id=account_id,
        to_date=to_date,
        real_only=real_only,
        paper_only=paper_only,
        limit=100_000,
    )
    history_trades = _relevant_trade_events(history_events)
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
    include_daily_realized: bool = True,
    real_only: bool = False,
    paper_only: bool = False,
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
        real_only=real_only,
        paper_only=paper_only,
        limit=100_000,
    )
    # FIFO needs full history through to_date so sells match earlier buys.
    history_events = list_trade_events(
        account_id=account_id,
        to_date=to_date,
        real_only=real_only,
        paper_only=paper_only,
        limit=100_000,
    )
    history_trades = _relevant_trade_events(history_events)
    period_trades = _relevant_trade_events(events)
    summary = trade_summary_stats(
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
        real_only=real_only,
        paper_only=paper_only,
    )
    round_trips_all = compute_round_trips(history_trades)
    round_trips_in_period = [
        rt
        for rt in round_trips_all
        if (not from_date or rt["close_date"] >= from_date)
        and (not to_date or rt["close_date"] <= to_date)
    ]
    stock_round_trips = [rt for rt in round_trips_in_period if not rt["is_option"]]
    fifo_wins_all = sum(
        1 for r in round_trips_in_period if parse_dollar(r["pnl"]) > 0
    )
    fifo_wins = sum(1 for r in stock_round_trips if parse_dollar(r["pnl"]) > 0)
    fifo_realized_total = sum(
        parse_dollar(rt["pnl"]) for rt in round_trips_in_period
    )
    full_recon = realized_summary(history_trades)
    period_recon = realized_summary(period_trades)
    period_realized = realized_between(history_trades, from_date, to_date)
    behavior = behavioral_facts(events)
    daily_cash_flow = daily_trade_stats(
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
        real_only=real_only,
        paper_only=paper_only,
    )
    # Per-day boundary recons are the expensive part — LLM-context callers
    # never read daily_realized, so let them skip it.
    daily_realized = (
        daily_boundary_realized_stats(
            history_trades,
            daily_cash_flow,
            from_date=from_date,
            to_date=to_date,
        )
        if include_daily_realized
        else []
    )

    return {
        "period": period,
        "from_date": from_date,
        "to_date": to_date,
        "summary": summary,
        "realized_pnl": period_realized["realized_closed"],
        "realized_closed": period_realized["realized_closed"],
        "closed_win_rate": period_realized["closed_win_rate"],
        "cash_flow_pnl": summary["net_amount"],
        "in_window_recon": {
            "realized_closed": period_recon["realized_closed"],
            "closed_win_rate": period_recon["closed_win_rate"],
            "per_symbol": period_recon["per_symbol"],
            "open_positions": period_recon["open_positions"],
            "incomplete_positions": period_recon["incomplete_positions"],
            "reliability": period_recon["reliability"],
        },
        "position_reconciliation": {
            "period": {
                "per_symbol": period_recon["per_symbol"],
                "open_positions": period_recon["open_positions"],
                "incomplete_positions": period_recon["incomplete_positions"],
                "reliability": period_recon["reliability"],
            },
            "full_history": {
                "per_symbol": full_recon["per_symbol"],
                "open_positions": full_recon["open_positions"],
                "incomplete_positions": full_recon["incomplete_positions"],
                "reliability": full_recon["reliability"],
            },
        },
        "behavioral": behavior,
        "approximate_fifo": {
            "realized_pnl": decimal_to_str(fifo_realized_total),
            "win_rate": round(fifo_wins_all / len(round_trips_in_period), 4)
            if round_trips_in_period
            else None,
            "stock_win_rate": round(fifo_wins / len(stock_round_trips), 4)
            if stock_round_trips
            else None,
            "note": "approximate (hold-time only; unreliable for P&L on date-only exports)",
        },
        "daily_realized": daily_realized,
        "daily_cash_flow": daily_cash_flow,
        "round_trips": round_trips_in_period[:100],
        "round_trips_note": "approximate (hold-time only)",
        "win_rate": period_realized["closed_win_rate"],
        "stock_win_rate": round(fifo_wins / len(stock_round_trips), 4)
        if stock_round_trips
        else None,
        "asset_classes": asset_class_breakdown(events),
        "asset_class_realized": realized_asset_class_breakdown(round_trips_in_period),
        "day_of_week": day_of_week_stats(events),
        "day_of_week_realized": day_of_week_realized(round_trips_in_period),
        "top_winners": sorted(
            round_trips_in_period,
            key=lambda r: parse_dollar(r["pnl"]),
            reverse=True,
        )[:5],
        "top_losers": sorted(
            round_trips_in_period,
            key=lambda r: parse_dollar(r["pnl"]),
        )[:5],
    }


def ai_performance_context(
    *,
    account_id: str | None = None,
    real_only: bool = False,
    paper_only: bool = False,
    period: str = "year",
    custom_from: str | None = None,
    custom_to: str | None = None,
    include_trade_context: bool = False,
) -> dict[str, Any]:
    """Compact structured context for agent tools and reflection."""
    from db.sqlite import get_latest_snapshot
    from db.trades import get_trade_account, symbol_trade_stats

    effective_period = period
    if custom_from is not None and custom_to is not None:
        effective_period = "custom"
    dash = performance_dashboard(
        account_id=account_id,
        real_only=real_only,
        paper_only=paper_only,
        period=effective_period,
        custom_from=custom_from,
        custom_to=custom_to,
        # This caller never reads daily_realized — skip the per-day recons.
        include_daily_realized=False,
    )

    symbols = symbol_trade_stats(
        account_id=account_id,
        from_date=dash["from_date"],
        to_date=dash["to_date"],
        real_only=real_only,
        paper_only=paper_only,
    )[:10]

    round_trips_in_period = _round_trips_in_period(
        account_id=account_id,
        from_date=dash["from_date"],
        to_date=dash["to_date"],
        real_only=real_only,
        paper_only=paper_only,
    )

    history_events = list_trade_events(
        account_id=account_id,
        real_only=real_only,
        paper_only=paper_only,
        limit=100_000,
    )
    history_trades = _relevant_trade_events(history_events)
    period_events = list_trade_events(
        account_id=account_id,
        from_date=dash["from_date"],
        to_date=dash["to_date"],
        real_only=real_only,
        paper_only=paper_only,
        limit=100_000,
    )
    period_trades = _relevant_trade_events(period_events)
    open_lots = compute_open_lots(history_trades)

    use_paper_snapshot = paper_only
    if not use_paper_snapshot and account_id:
        acct = get_trade_account(account_id)
        if acct and acct.get("broker") == "alpaca":
            use_paper_snapshot = True

    latest_prices: dict[str, Decimal] | None = None
    if use_paper_snapshot:
        snap = get_latest_snapshot(broker="alpaca")
        if snap and snap.get("positions"):
            latest_prices = {}
            for pos in snap["positions"]:
                symbol = pos.get("symbol")
                if not symbol:
                    continue
                price = pos.get("current_price")
                if price is not None:
                    latest_prices[symbol] = Decimal(str(price))
        unrealized = compute_unrealized(
            open_lots,
            snap["positions"] if snap else [],
        )
    else:
        unrealized = {
            "unavailable": "no real-time broker snapshot for real accounts",
        }

    full_recon = realized_summary(history_trades, latest_prices=latest_prices)
    period_recon = realized_summary(period_trades, latest_prices=latest_prices)
    period_realized = realized_between(
        history_trades,
        dash["from_date"],
        dash["to_date"],
        latest_prices=latest_prices,
    )
    behavior = behavioral_facts(period_events)

    result: dict[str, Any] = {
        "period": period,
        "from_date": dash["from_date"],
        "to_date": dash["to_date"],
        "realized": {
            "realized_closed": period_realized["realized_closed"],
            "closed_win_rate": period_realized["closed_win_rate"],
            "per_symbol": period_recon["per_symbol"],
            "open_positions": full_recon["open_positions"],
            "incomplete_positions": full_recon["incomplete_positions"],
            "reliability": {
                **period_recon["reliability"],
                "incomplete_positions": full_recon["reliability"][
                    "incomplete_positions"
                ],
                "open_positions": full_recon["reliability"]["open_positions"],
            },
            # No per_symbol here — it duplicates the top-level list into every
            # LLM payload for no informational gain.
            "in_window_recon": {
                "realized_closed": period_recon["realized_closed"],
                "closed_win_rate": period_recon["closed_win_rate"],
            },
        },
        "behavioral": behavior,
        "approximate_fifo": {
            "profit_factor": compute_profit_factor(round_trips_in_period),
            "hold_time": compute_hold_times(round_trips_in_period),
            "streaks": compute_streaks(round_trips_in_period),
            "recent_closed_trades": dash["round_trips"][:15],
            "largest_wins": dash["top_winners"][:3],
            "largest_losses": dash["top_losers"][:3],
            "note": dash["round_trips_note"],
        },
        "summary": dash["summary"],
        "asset_classes": dash["asset_classes"],
        "day_of_week": dash["day_of_week"],
        "top_symbols": symbols,
        "behavioral_flags": compute_behavioral_flags(
            round_trips_in_period,
            dash["daily_cash_flow"],
        ),
        "unrealized": unrealized,
    }

    if include_trade_context:
        from brokers.factory import get_broker
        from config.settings import broker_is_configured, primary_broker
        from signals.trade_context import build_entry_style_block

        broker = None
        broker_name = primary_broker()
        if broker_is_configured(broker_name):
            try:
                broker = get_broker(broker_name)
            except Exception:
                import logging

                logging.getLogger(__name__).exception(
                    "Failed to initialize broker for trade context"
                )
        result["entry_style"] = build_entry_style_block(
            period_trades,
            broker=broker,
        )

    return result
