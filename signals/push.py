"""Phone push delivery for trade proposals — gated, fail-safe, transport-agnostic."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from config.settings import push_config
from db.base import DEFAULT_DB_PATH
from db.device_tokens import list_active_device_tokens

logger = logging.getLogger(__name__)

_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def _proposal_body(proposal: dict[str, Any]) -> str:
    summary = proposal.get("summary")
    if summary:
        return str(summary)
    inner = proposal.get("proposal") if isinstance(proposal.get("proposal"), dict) else {}
    side = str(inner.get("side") or proposal.get("side") or "buy").upper()
    qty = inner.get("qty") if inner.get("qty") is not None else proposal.get("qty")
    symbol = inner.get("symbol") or proposal.get("symbol") or "?"
    return f"{side} {qty} {symbol}"


def _build_expo_message(token: str, proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "to": token,
        "title": "Trade proposal",
        "body": _proposal_body(proposal),
        "data": {
            "type": "trade_proposal",
            "proposal_id": proposal["id"],
            "payload_hash": proposal.get("payload_hash"),
            "expires_at": proposal.get("expires_at"),
        },
        "categoryId": "TRADE_PROPOSAL",
        "sound": "default",
        "priority": "high",
    }


async def _send_expo(messages: list[dict[str, Any]], cfg: dict[str, Any]) -> int:
    if not messages:
        return 0
    headers: dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}
    access_token = str(cfg.get("expo_access_token") or "").strip()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(_EXPO_PUSH_URL, json=messages, headers=headers)
        response.raise_for_status()
    return len(messages)


async def send_proposal_push(
    proposal: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Send an actionable push for a new proposal to active devices.

    Returns count sent. NO-OP (returns 0) when disabled / no tokens / unconfigured.
    NEVER raises — all failures are logged and swallowed.
    """
    try:
        cfg = push_config()
        if not cfg["enabled"]:
            logger.debug("push disabled")
            return 0

        provider = str(cfg.get("provider") or "expo").strip().lower()
        if provider == "apns":
            logger.info("apns not implemented yet")
            return 0
        if provider != "expo":
            logger.warning("Unknown push provider %r — skipping", provider)
            return 0

        token_type = "expo"
        tokens = list_active_device_tokens(token_type=token_type, db_path=db_path)
        if not tokens:
            return 0

        messages = [
            _build_expo_message(str(row["token"]), proposal) for row in tokens
        ]
        sent = await _send_expo(messages, cfg)
        logger.info(
            "Sent proposal push for %s to %d device(s)",
            proposal.get("id"),
            sent,
        )
        return sent
    except Exception:
        logger.exception(
            "Failed to send proposal push for %s",
            proposal.get("id"),
        )
        return 0
