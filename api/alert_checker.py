"""Price alert evaluation against live quotes."""

from __future__ import annotations

from datetime import UTC, datetime

from db.chart_features import (
    list_price_alerts,
    mark_alert_triggered,
    rearm_repeat_alert,
)

# Persistent (repeat) alerts re-arm after each crossing. Cross-detection means
# they only fire on an actual cross, but that isn't enough: a price dithering
# tick-by-tick right at the target keeps crossing and would re-fire on the 60s
# cooldown alone. So after a repeat alert fires it is DISARMED and only re-arms
# once price moves at least a band AWAY from where it fired — then a fresh cross
# can fire again. The 60s cooldown stays as a floor on top of that.
_REPEAT_COOLDOWN_S = 60.0


def _rearm_band(target: float) -> float:
    """Minimum move away from the fire price before a repeat alert re-arms."""
    return max(abs(target) * 0.003, 0.01)


def _left_band(prev: float, current: float, fire_price: float, target: float) -> bool:
    """True once price has retreated at least the band from the last fire price.

    Measured from the fire price (~= target, plus the tick's overshoot) so a
    price hovering within the band of the level never re-arms.
    """
    band = _rearm_band(target)
    return max(abs(prev - fire_price), abs(current - fire_price)) >= band


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
        alert_id = int(alert["id"])
        is_repeat = bool(alert.get("repeat"))

        if is_repeat:
            # Hysteresis: while disarmed (last_fire_price set), only re-arm once
            # price has moved a band away — evaluated every tick, independent of
            # cross direction, so a directional alert's retreat still re-arms it.
            fire_price = alert.get("last_fire_price")
            if fire_price is not None:
                if _left_band(prev, current, float(fire_price), target):
                    rearm_repeat_alert(alert_id)
                else:
                    continue  # still parked at the level → suppress
            if not _crossed(prev, current, target, direction):
                continue
            if _within_cooldown(alert.get("last_triggered_at"), now):
                continue
            mark_alert_triggered(alert_id, repeat=True, fire_price=current)
        else:
            if not _crossed(prev, current, target, direction):
                continue
            # One-shot: only the writer that flips triggered 0->1 fires, so two
            # concurrent evaluations can't both notify.
            if not mark_alert_triggered(alert_id, repeat=False):
                continue

        triggered.append(
            {
                **alert,
                "trigger_price": current,
            }
        )

    return triggered
