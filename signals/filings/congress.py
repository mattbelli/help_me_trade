"""Congressional trade feed via Lambda Finance's free normalized endpoint.

STOCK Act disclosures are legally up to 45 days stale — every tracker, paid or
free, shows old trades; this lane is context, not timing. Third-party source:
if the endpoint dies the lane simply goes quiet (fail-soft), and the primary
sources (House clerk / Senate eFD) remain the fallback to build against.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_URL = "https://www.lambdafin.com/api/congressional/recent"
_TIMEOUT = 20.0
_AMOUNT_RE = re.compile(r"\$([\d,]+)")


def _uid(t: dict[str, Any]) -> str:
    key = "|".join(
        str(t.get(k, ""))
        for k in ("representative", "symbol", "transactionDate", "type", "amount", "owner")
    )
    return "congress:" + hashlib.sha1(key.encode()).hexdigest()[:20]


def _side(raw_type: str) -> str:
    t = raw_type.strip().upper()
    if t.startswith("P"):
        return "buy"
    if t.startswith("S"):
        return "sell"
    return ""


def _amount_floor(amount: str) -> float | None:
    """Lower bound of the disclosed range ('$1,001 - $15,000' -> 1001)."""
    m = _AMOUNT_RE.search(amount or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def map_trade(t: dict[str, Any]) -> dict[str, Any] | None:
    """One raw Lambda row -> a feed event, or None if not feed-worthy."""
    symbol = str(t.get("symbol", "")).strip().upper()
    side = _side(str(t.get("type", "")))
    # "NONE"/"N/A" = non-ticker assets (munis, funds, real estate) — not chartable.
    if not symbol or symbol in ("NONE", "N/A", "--") or not side:
        return None
    party = str(t.get("party", "")).strip()
    chamber = str(t.get("chamber", "")).strip()
    state = str(t.get("state", "")).strip()
    role = " ".join(p for p in (party.lower(), chamber.lower(), state) if p)
    return {
        "uid": _uid(t),
        "kind": "congress_trade",
        "symbol": symbol,
        "person": str(t.get("representative", "")).strip(),
        "role": role,
        "side": side,
        "value_usd": _amount_floor(str(t.get("amount", ""))),
        "value_label": str(t.get("amount", "")).strip().replace(" - ", "–"),
        "filed_at": str(t.get("disclosureDate", "")).strip(),
        "traded_at": str(t.get("transactionDate", "")).strip() or None,
        "url": str(t.get("ptrLink", "")).strip(),
        "detail": {
            "asset": str(t.get("assetDescription", "")).strip(),
            "owner": str(t.get("owner", "")).strip(),
            "raw_type": str(t.get("type", "")).strip(),
        },
    }


def poll_congress_once() -> list[dict[str, Any]]:
    with httpx.Client() as client:
        resp = client.get(_URL, timeout=_TIMEOUT)
        resp.raise_for_status()
        trades = resp.json().get("trades") or []
    events = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        ev = map_trade(t)
        if ev:
            events.append(ev)
    return events
