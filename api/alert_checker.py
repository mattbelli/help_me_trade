"""Price alert evaluation against live quotes."""

from __future__ import annotations

from db.chart_features import list_price_alerts, mark_alert_triggered


def _crossed(prev: float, current: float, target: float, direction: str) -> bool:
    if direction == "above":
        return prev < target <= current
    if direction == "below":
        return prev > target >= current
    return (prev < target <= current) or (prev > target >= current)


def check_alerts(
    quotes: dict[str, float],
    *,
    prev_quotes: dict[str, float],
) -> list[dict]:
    """Return alerts that just triggered; marks them in the database."""
    alerts = list_price_alerts(active_only=True)
    triggered: list[dict] = []

    for alert in alerts:
        sym = str(alert["symbol"]).upper()
        current = quotes.get(sym)
        if current is None:
            continue
        prev = prev_quotes.get(sym)
        if prev is None:
            continue
        target = float(alert["price"])
        direction = str(alert.get("direction", "cross"))
        if _crossed(prev, current, target, direction):
            mark_alert_triggered(int(alert["id"]))
            triggered.append(
                {
                    **alert,
                    "trigger_price": current,
                }
            )

    return triggered
