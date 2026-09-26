"""Best-effort cross-client invalidation for the shared price-alert list."""

from __future__ import annotations

import logging
from typing import Literal

from brokers.market_stream import market_hub

logger = logging.getLogger(__name__)

PriceAlertChangeAction = Literal["created", "updated", "deleted", "triggered"]


def price_alert_change_event(
    action: PriceAlertChangeAction,
    *,
    alert_id: int,
    symbol: str | None = None,
) -> dict[str, object]:
    """Build the small WS control frame; REST remains the state authority."""
    event: dict[str, object] = {
        "type": "price_alerts_changed",
        "action": action,
        "alert_id": int(alert_id),
    }
    if symbol:
        event["symbol"] = symbol.strip().upper()
    return event


async def broadcast_price_alert_change(
    action: PriceAlertChangeAction,
    *,
    alert_id: int,
    symbol: str | None = None,
) -> None:
    """Tell connected clients to refetch, without risking the committed mutation."""
    try:
        await market_hub.broadcast_event(
            price_alert_change_event(action, alert_id=alert_id, symbol=symbol)
        )
    except Exception:
        logger.exception(
            "price-alert WS invalidation failed: action=%s alert_id=%s",
            action,
            alert_id,
        )
