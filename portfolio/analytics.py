"""Derive portfolio analytics from poll snapshots."""

from __future__ import annotations

from typing import Any

_CONCENTRATION_THRESHOLD_PCT = 25.0
_CASH_HEAVY_THRESHOLD_PCT = 30.0
_DRIFT_THRESHOLD_PCT = 5.0


def _num(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _position_weights(
    positions: list[dict[str, Any]], total_equity: float
) -> list[dict[str, Any]]:
    if total_equity <= 0:
        return []
    weighted: list[dict[str, Any]] = []
    for pos in positions:
        market_value = _num(pos.get("market_value"))
        weight_pct = round((market_value / total_equity) * 100, 2)
        weighted.append(
            {
                "symbol": pos.get("symbol"),
                "weight_pct": weight_pct,
                "market_value": round(market_value, 2),
                "unrealized_pl": round(_num(pos.get("unrealized_pl")), 2),
                "qty": _num(pos.get("qty")),
                "current_price": round(_num(pos.get("current_price")), 4),
            }
        )
    weighted.sort(key=lambda row: row["weight_pct"], reverse=True)
    return weighted


def _prior_weights(snapshot: dict[str, Any]) -> dict[str, float]:
    account = snapshot.get("account", {})
    equity = _num(account.get("portfolio_value"))
    positions = snapshot.get("positions") or []
    if equity <= 0:
        return {}
    return {
        str(pos.get("symbol")): round((_num(pos.get("market_value")) / equity) * 100, 2)
        for pos in positions
        if pos.get("symbol")
    }


def _build_flags(
    *,
    cash_pct: float,
    largest_weight_pct: float,
    positions: list[dict[str, Any]],
    exposure_drift: dict[str, float],
    week_exposure_drift: dict[str, float] | None = None,
) -> list[str]:
    flags: list[str] = []
    if largest_weight_pct >= _CONCENTRATION_THRESHOLD_PCT:
        flags.append(
            f"Concentrated: largest position is {largest_weight_pct:.1f}% of portfolio"
        )
    if cash_pct >= _CASH_HEAVY_THRESHOLD_PCT:
        flags.append(f"Cash heavy: {cash_pct:.1f}% in cash")
    if positions and all(_num(p.get("unrealized_pl")) < 0 for p in positions):
        flags.append("All open positions are underwater on unrealized P/L")
    for symbol, delta in exposure_drift.items():
        if delta >= _DRIFT_THRESHOLD_PCT:
            flags.append(f"Exposure up: {symbol} weight +{delta:.1f}pp vs prior poll")
            break
    for symbol, delta in (week_exposure_drift or {}).items():
        if delta >= _DRIFT_THRESHOLD_PCT:
            flags.append(
                f"Exposure up vs 7d ago: {symbol} weight +{delta:.1f}pp"
            )
            break
    return flags


def _compare_to_reference(
    snapshot: dict[str, Any],
    reference: dict[str, Any] | None,
) -> tuple[float | None, float | None, dict[str, float]]:
    """Equity delta and exposure drift vs a reference snapshot."""
    if not reference:
        return None, None, {}
    account = snapshot.get("account", {})
    total_equity = _num(account.get("portfolio_value"))
    ref_equity = _num(reference.get("account", {}).get("portfolio_value"))
    equity_change = None
    equity_change_pct = None
    if ref_equity > 0:
        equity_change = round(total_equity - ref_equity, 2)
        equity_change_pct = round((equity_change / ref_equity) * 100, 2)

    positions = list(snapshot.get("positions") or [])
    weighted = _position_weights(positions, total_equity)
    current_weights = {
        row["symbol"]: row["weight_pct"] for row in weighted if row.get("symbol")
    }
    prior_map = _prior_weights(reference)
    drift: dict[str, float] = {}
    for symbol, weight in current_weights.items():
        prior_weight = prior_map.get(symbol, 0.0)
        delta = round(weight - prior_weight, 2)
        if abs(delta) >= 0.01:
            drift[symbol] = delta
    return equity_change, equity_change_pct, drift


def compute_portfolio_analytics(
    snapshot: dict[str, Any],
    prior_snapshot: dict[str, Any] | None = None,
    *,
    day_ago_snapshot: dict[str, Any] | None = None,
    week_ago_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute exposure, concentration, drift, and risk flags from a snapshot."""
    account = snapshot.get("account", {})
    positions = list(snapshot.get("positions") or [])
    total_equity = _num(account.get("portfolio_value"))
    cash = _num(account.get("cash"))
    buying_power = _num(account.get("buying_power"))

    cash_pct = round((cash / total_equity) * 100, 2) if total_equity > 0 else 0.0
    invested_pct = round(100.0 - cash_pct, 2) if total_equity > 0 else 0.0
    total_unrealized_pl = round(
        sum(_num(p.get("unrealized_pl")) for p in positions), 2
    )

    weighted = _position_weights(positions, total_equity)
    largest = weighted[0] if weighted else None
    largest_weight_pct = largest["weight_pct"] if largest else 0.0

    winners = [
        row["symbol"]
        for row in sorted(weighted, key=lambda r: r["unrealized_pl"], reverse=True)
        if row["unrealized_pl"] > 0
    ]
    losers = [
        row["symbol"]
        for row in sorted(weighted, key=lambda r: r["unrealized_pl"])
        if row["unrealized_pl"] < 0
    ]

    equity_change, equity_change_pct, exposure_drift = _compare_to_reference(
        snapshot, prior_snapshot
    )
    day_equity_change, day_equity_change_pct, day_exposure_drift = _compare_to_reference(
        snapshot, day_ago_snapshot
    )
    week_equity_change, week_equity_change_pct, week_exposure_drift = (
        _compare_to_reference(snapshot, week_ago_snapshot)
    )

    flags = _build_flags(
        cash_pct=cash_pct,
        largest_weight_pct=largest_weight_pct,
        positions=positions,
        exposure_drift=exposure_drift,
        week_exposure_drift=week_exposure_drift,
    )

    return {
        "captured_at": snapshot.get("captured_at"),
        "broker": snapshot.get("broker"),
        "total_equity": round(total_equity, 2),
        "cash": round(cash, 2),
        "cash_pct": cash_pct,
        "invested_pct": invested_pct,
        "buying_power": round(buying_power, 2),
        "total_unrealized_pl": total_unrealized_pl,
        "position_count": len(positions),
        "largest_position": largest,
        "largest_position_pct": largest_weight_pct,
        "top_holdings": weighted[:5],
        "winners": winners[:5],
        "losers": losers[:5],
        "equity_change_since_prior": equity_change,
        "equity_change_pct_since_prior": equity_change_pct,
        "exposure_drift_pct": exposure_drift,
        "equity_change_since_day_ago": day_equity_change,
        "equity_change_pct_since_day_ago": day_equity_change_pct,
        "exposure_drift_vs_day_ago_pct": day_exposure_drift,
        "equity_change_since_week_ago": week_equity_change,
        "equity_change_pct_since_week_ago": week_equity_change_pct,
        "exposure_drift_vs_week_ago_pct": week_exposure_drift,
        "flags": flags,
    }
