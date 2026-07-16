"""Unified notification emit — persist first, then best-effort delivery."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.notifications import create_notification

logger = logging.getLogger(__name__)

# Routing matrix (surface 4): ambient kinds are inbox-only — they persist to
# the store and reach the web card feed over WS, but never buzz the phone,
# regardless of what the caller passed for push. A warning/critical severity
# escapes the demotion (an ambient source reporting a real problem must push).
# Phone tier stays: proposals, executions/fills, risk/kill-switch, price
# alerts, idea triggers, and budget-gated flow convergence.
INBOX_ONLY_KINDS = {
    "rvol",
    "earnings",
    "report_ready",
    "research_ready",
    "scan_ready",
    "idea_resolved",
}

_inflight_notify_tasks: set[asyncio.Task] = set()


async def _broadcast_ws(payload: dict[str, Any]) -> None:
    try:
        from brokers.market_stream import market_hub

        await market_hub.broadcast_alert(payload)
    except Exception:
        logger.exception("notification WS broadcast failed")


async def broadcast_notification_update(
    *,
    notification_id: str,
    read: bool,
    dismissed: bool | None = None,
) -> None:
    payload: dict[str, Any] = {
        "type": "notification_update",
        "id": notification_id,
        "read": read,
    }
    if dismissed is not None:
        payload["dismissed"] = dismissed
    await _broadcast_ws(payload)


async def notify(
    *,
    kind: str,
    title: str,
    body: str = "",
    severity: str = "info",
    account_id: str | None = None,
    strategy_id: str | None = None,
    ref_type: str | None = None,
    ref_id: str | None = None,
    action_url: str | None = None,
    group_key: str | None = None,
    data: dict[str, Any] | None = None,
    push: bool = True,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    if push and kind in INBOX_ONLY_KINDS and severity not in ("warning", "critical"):
        push = False
    try:
        row = create_notification(
            kind=kind,
            title=title,
            body=body,
            severity=severity,
            account_id=account_id,
            strategy_id=strategy_id,
            ref_type=ref_type,
            ref_id=ref_id,
            action_url=action_url,
            group_key=group_key,
            data=data,
            db_path=db_path,
        )
    except Exception:
        # notify() must never raise into its caller (poll loops, the execution
        # chokepoint). A persist failure loses this one notification, logged.
        logger.exception("notification persist failed: kind=%s title=%s", kind, title)
        return {}

    await _broadcast_ws({"type": "notification", **row})

    if push:
        try:
            from signals.push import send_alert_push

            push_data: dict[str, Any] = {
                "type": "notification",
                "notification_id": row["id"],
                "kind": kind,
            }
            if data:
                push_data.update(data)
            await send_alert_push(
                title,
                body,
                data=push_data,
                db_path=db_path,
            )
        except Exception:
            logger.exception("notification push failed")

    return row


def notify_soon(**kwargs: Any) -> None:
    loop = asyncio.get_running_loop()
    task = loop.create_task(notify(**kwargs))
    _inflight_notify_tasks.add(task)
    task.add_done_callback(_inflight_notify_tasks.discard)
