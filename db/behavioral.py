"""Transaction-based behavioral metrics (robust to intraday ordering)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import Any

from imports.money import decimal_to_str, parse_dollar
from imports.option_symbol import parse_option_symbol

_BEHAVIOR_EVENT_TYPES = frozenset({"trade", "option_expired"})
_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _trade_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("event_type") in _BEHAVIOR_EVENT_TYPES]


def _pct_share(value: int | Decimal, total: int | Decimal) -> float | None:
    if not total:
        return None
    return round(float(value / total), 4)


def behavioral_facts(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Behavioral patterns derivable from transactions alone."""
    trades = _trade_events(events)
    if not trades:
        return {
            "trade_count": 0,
            "date_range": None,
            "trades_by_month": {},
            "notional": {
                "avg_abs_amount": None,
                "median_abs_amount": None,
                "median_trend": None,
            },
            "symbol_concentration": {
                "by_trade_count": [],
                "by_volume": [],
            },
            "same_day_flips": {
                "total": 0,
                "top_symbols": [],
            },
            "day_of_week": [],
            "buy_sell_ratio": {"overall": None, "by_month": {}},
            "asset_mix": {
                "equity_trades": 0,
                "option_trades": 0,
                "equity_pct": None,
                "option_pct": None,
            },
        }

    dates: list[str] = []
    notionals: list[Decimal] = []
    by_month: dict[str, int] = defaultdict(int)
    symbol_counts: dict[str, int] = defaultdict(int)
    symbol_volume: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    day_side: dict[tuple[str, str], set[str]] = defaultdict(set)
    dow_counts: dict[int, int] = defaultdict(int)
    buy_count = 0
    sell_count = 0
    buys_by_month: dict[str, int] = defaultdict(int)
    sells_by_month: dict[str, int] = defaultdict(int)
    equity_trades = 0
    option_trades = 0

    for event in trades:
        trade_date = event["trade_date"]
        dates.append(trade_date)
        month_key = trade_date[:7]
        by_month[month_key] += 1

        amount = parse_dollar(event.get("amount"))
        abs_amount = abs(amount)
        notionals.append(abs_amount)

        symbol = event.get("symbol") or ""
        if symbol:
            symbol_counts[symbol] += 1
            symbol_volume[symbol] += abs_amount

        if event.get("event_type") == "option_expired":
            side_key = "sell" if parse_dollar(event.get("quantity")) >= 0 else "buy"
        else:
            side_key = (event.get("side") or "").lower()

        if side_key == "buy":
            buy_count += 1
            buys_by_month[month_key] += 1
        elif side_key == "sell":
            sell_count += 1
            sells_by_month[month_key] += 1

        if symbol:
            day_side[(symbol, trade_date)].add(side_key)

        try:
            dow = datetime.fromisoformat(trade_date).weekday()
            dow_counts[dow] += 1
        except ValueError:
            pass

        if event.get("event_type") == "trade":
            if parse_option_symbol(symbol):
                option_trades += 1
            else:
                equity_trades += 1

    flip_symbols: dict[str, int] = defaultdict(int)
    flip_total = 0
    for (symbol, _day), sides in day_side.items():
        if "buy" in sides and "sell" in sides:
            flip_total += 1
            flip_symbols[symbol] += 1

    total_trades = len(trades)
    sorted_by_count = sorted(
        symbol_counts.items(), key=lambda item: (-item[1], item[0])
    )
    sorted_by_volume = sorted(
        symbol_volume.items(), key=lambda item: (-item[1], item[0])
    )
    total_volume = sum(symbol_volume.values(), Decimal("0"))

    def _top_symbols(
        ranked: list[tuple[str, int | Decimal]],
        total: int | Decimal,
    ) -> list[dict[str, Any]]:
        top = ranked[:5]
        return [
            {
                "symbol": symbol,
                "value": int(value) if isinstance(value, int) else decimal_to_str(value),
                "share_pct": _pct_share(value, total),
            }
            for symbol, value in top
        ]

    median_trend: str | None = None
    if len(notionals) >= 2:
        mid = len(notionals) // 2
        first_half = sorted(notionals[:mid])
        second_half = sorted(notionals[mid:])
        first_med = Decimal(str(median([float(v) for v in first_half])))
        second_med = Decimal(str(median([float(v) for v in second_half])))
        if second_med > first_med * Decimal("1.05"):
            median_trend = "increasing"
        elif second_med < first_med * Decimal("0.95"):
            median_trend = "decreasing"
        else:
            median_trend = "stable"

    asset_total = equity_trades + option_trades
    buy_sell_by_month = {
        month: {
            "buys": buys_by_month[month],
            "sells": sells_by_month[month],
            "ratio": round(buys_by_month[month] / sells_by_month[month], 4)
            if sells_by_month[month]
            else None,
        }
        for month in sorted(set(buys_by_month) | set(sells_by_month))
    }

    return {
        "trade_count": total_trades,
        "date_range": {"from": min(dates), "to": max(dates)},
        "trades_by_month": dict(sorted(by_month.items())),
        "notional": {
            "avg_abs_amount": decimal_to_str(
                sum(notionals, Decimal("0")) / Decimal(total_trades)
            ),
            "median_abs_amount": decimal_to_str(
                Decimal(str(median([float(v) for v in notionals])))
            ),
            "median_trend": median_trend,
        },
        "symbol_concentration": {
            "by_trade_count": _top_symbols(sorted_by_count, total_trades),
            "by_volume": _top_symbols(sorted_by_volume, total_volume),
        },
        "same_day_flips": {
            "total": flip_total,
            "top_symbols": [
                {"symbol": symbol, "flip_days": count}
                for symbol, count in sorted(
                    flip_symbols.items(),
                    key=lambda item: (-item[1], item[0]),
                )[:5]
            ],
        },
        "day_of_week": [
            {"day": _WEEKDAY_NAMES[dow], "trade_count": dow_counts[dow]}
            for dow in sorted(dow_counts)
        ],
        "buy_sell_ratio": {
            "overall": round(buy_count / sell_count, 4) if sell_count else None,
            "by_month": buy_sell_by_month,
        },
        "asset_mix": {
            "equity_trades": equity_trades,
            "option_trades": option_trades,
            "equity_pct": _pct_share(equity_trades, asset_total),
            "option_pct": _pct_share(option_trades, asset_total),
        },
    }
