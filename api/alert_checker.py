"""Price alert evaluation against live quotes."""

from __future__ import annotations

from datetime import UTC, datetime

from db.chart_features import list_price_alerts, mark_alert_triggered

# Persistent (repeat) alerts re-arm after each crossing. Cross-detection already
# means they only fire on an actual cross (not while price sits past the line),
# but price oscillating tick-by-tick right at the target could still spam. A
# short cooldown debounces that without affecting normal crossings.
_REPEAT_COOLDOWN_S = 60.0


def _crossed(prev: float, current: float, target: float, direction: str) -> bool:
    if direction == "above":
        return prev < target <= current
    if direction == "below":
        return prev > target >= current
    return (prev < target <= current) or (prev > target >= current)


def _within_cooldown(last_triggered_at: str | None, now: datetime) -> bool:
    if not last_triggered_at:
        return False
    try:
        last = datetime.fromisoformat(last_triggered_at)
    except ValueError:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (now - last).total_seconds() < _REPEAT_COOLDOWN_S


def check_alerts(
    quotes: dict[str, float],
    *,
    prev_quotes: dict[str, float],
) -> list[dict]:
    """Return alerts that just triggered; marks them in the database.

    One-shot alerts are marked triggered (leave the active list). Persistent
    (repeat) alerts stay active and re-arm, debounced by a short cooldown.
    """
    alerts = list_price_alerts(active_only=True)
    triggered: list[dict] = []
    now = datetime.now(UTC)

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
        if not _crossed(prev, current, target, direction):
            continue

        is_repeat = bool(alert.get("repeat"))
        if is_repeat and _within_cooldown(alert.get("last_triggered_at"), now):
            continue

        mark_alert_triggered(int(alert["id"]), repeat=is_repeat)
        triggered.append(
            {
                **alert,
                "trigger_price": current,
            }
        )

    return triggered
