"""Read-only live snapshot of the user's REAL Schwab accounts.

Schwab is READ-ONLY here (real funded accounts — see CLAUDE.md and the
``schwab-live-funded-readonly`` rule). This module ONLY issues GET calls
(``get_account_numbers`` / ``get_account`` / ``get_orders_for_account``). It
NEVER places, cancels, replaces, or otherwise mutates anything, and it is
deliberately independent of the Alpaca paper-execution account machinery
(``poll_runs`` / ``trade_accounts``) so real funded balances can never be
comingled with a paper sandbox or treated as executable.

One refresh = ``1 + N`` GETs (account list + one account/orders round-trip per
linked account). A short TTL cache keeps the poll well under Schwab's Akamai
burst limit even if several widgets poll at once.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from brokers.schwab_auth import OPEN_ORDER_STATUSES, create_client, token_exists

ROOT = Path(__file__).resolve().parent.parent
_LABELS_PATH = ROOT / "data" / "schwab_account_labels.json"
_CACHE_TTL = 30.0  # seconds

_cache: dict = {"ts": 0.0, "data": None}
_lock = threading.Lock()  # threading (not asyncio) so sync callers can share the cache


def _labels() -> dict:
    """Optional {last4: friendly_label} overrides written once the user maps
    each masked account number to a registration (Individual/Roth/Joint)."""
    try:
        return json.loads(_LABELS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _last4(num: str) -> str:
    num = str(num or "")
    return num[-4:] if len(num) >= 4 else num


def _parse_positions(sec: dict) -> list[dict]:
    out: list[dict] = []
    for item in sec.get("positions") or []:
        long_qty = float(item.get("longQuantity") or 0)
        short_qty = float(item.get("shortQuantity") or 0)
        qty = long_qty if long_qty > 0 else -short_qty
        if qty == 0:
            continue
        inst = item.get("instrument") or {}
        symbol = item.get("symbol") or inst.get("symbol") or ""
        market_value = float(item.get("marketValue") or 0)
        avg_price = float(item.get("averagePrice") or 0)
        unrealized = float(item.get("longOpenProfitLoss") or 0) + float(
            item.get("shortOpenProfitLoss") or 0
        )
        out.append(
            {
                "symbol": str(symbol),
                "asset_type": str(inst.get("assetType") or ""),
                "qty": qty,
                "market_value": market_value,
                "avg_price": avg_price,
                "current_price": abs(market_value / qty)
                if qty and market_value
                else avg_price,
                "unrealized_pl": unrealized,
                "day_pl": float(item.get("currentDayProfitLoss") or 0),
            }
        )
    return out


def _parse_open_orders(raw: list) -> list[dict]:
    out: list[dict] = []
    for item in raw or []:
        status = str(item.get("status", ""))
        if status not in OPEN_ORDER_STATUSES:
            continue
        legs = item.get("orderLegCollection") or []
        symbol = ""
        side = ""
        if legs:
            leg = legs[0]
            symbol = str((leg.get("instrument") or {}).get("symbol", leg.get("symbol", "")))
            side = str(leg.get("instruction", "")).split("_")[0].lower()
        price = item.get("price")
        out.append(
            {
                "order_id": str(item.get("orderId", "")),
                "symbol": symbol,
                "side": side,
                "qty": float(item.get("quantity") or 0),
                "filled_qty": float(item.get("filledQuantity") or 0),
                "order_type": str(item.get("orderType", "")).lower(),
                "status": status.lower(),
                "limit_price": float(price) if price not in (None, "") else None,
            }
        )
    return out


def _fetch_all() -> dict:
    """Blocking: pull every linked Schwab account (balances + positions + open
    orders). Called off the event loop via ``asyncio.to_thread``."""
    from schwab.client import Client

    client = create_client()
    resp = client.get_account_numbers()
    resp.raise_for_status()
    linked = resp.json() or []
    labels = _labels()

    accounts: list[dict] = []
    for entry in linked:
        num = str(entry.get("accountNumber", ""))
        account_hash = entry.get("hashValue", "")
        last4 = _last4(num)

        ar = client.get_account(account_hash, fields=[Client.Account.Fields.POSITIONS])
        ar.raise_for_status()
        sec = (ar.json() or {}).get("securitiesAccount", {})
        bal = sec.get("currentBalances") or sec.get("initialBalances") or {}
        positions = _parse_positions(sec)

        try:
            orders_resp = client.get_orders_for_account(account_hash)
            orders_resp.raise_for_status()
            open_orders = _parse_open_orders(orders_resp.json())
        except Exception:  # noqa: BLE001 — orders are best-effort; never fail the snapshot
            open_orders = []

        liq = float(bal.get("liquidationValue") or bal.get("cashBalance") or 0)
        init_liq = float((sec.get("initialBalances") or {}).get("liquidationValue") or 0)
        # Day P&L from Schwab's OWN start-of-day liquidation value — this captures
        # REALIZED closed trades AND unrealized open positions. Summing position
        # currentDayProfitLoss alone reads 0 once the account is flat (a day-trader
        # who round-trips out), silently dropping the day's realized gain. Fall
        # back to the position-day sum only if Schwab omits initialBalances.
        day_pl = (
            round(liq - init_liq, 2)
            if init_liq
            else sum(p["day_pl"] for p in positions)
        )

        accounts.append(
            {
                "id": f"schwab_{last4}",
                "last4": last4,
                "label": labels.get(last4) or f"Schwab ••{last4}",
                "account_type": str(sec.get("type") or ""),
                "is_day_trader": bool(sec.get("isDayTrader")),
                "liquidation_value": liq,
                "cash": float(bal.get("cashBalance") or 0),
                "long_market_value": float(bal.get("longMarketValue") or 0),
                "day_pl": day_pl,
                "unrealized_pl": sum(p["unrealized_pl"] for p in positions),
                "positions": positions,
                "open_orders": open_orders,
            }
        )

    accounts.sort(key=lambda a: a["liquidation_value"], reverse=True)
    return {
        "accounts": accounts,
        "totals": {
            "liquidation_value": sum(a["liquidation_value"] for a in accounts),
            "cash": sum(a["cash"] for a in accounts),
            "long_market_value": sum(a["long_market_value"] for a in accounts),
            "day_pl": sum(a["day_pl"] for a in accounts),
            "unrealized_pl": sum(a["unrealized_pl"] for a in accounts),
            "account_count": len(accounts),
        },
    }


def get_schwab_accounts_sync(force: bool = False) -> dict:
    """TTL-cached read-only snapshot of all linked Schwab accounts (blocking).

    Shared core for both the async API route and the sync perception reader — one
    threading lock + one cache, so an agent read reuses whatever the frontend's
    polling already fetched. Fail-soft: on a fetch error, serve the last good
    snapshot (flagged ``stale``) if we have one; otherwise return an ``error``
    status. Never raises into the caller.
    """
    if not token_exists():
        return {
            "authenticated": False,
            "accounts": [],
            "totals": {},
            "message": "Schwab not authenticated. Run: python -m scripts.schwab_login",
        }

    now = time.time()
    with _lock:
        cached = _cache["data"]
        if not force and cached is not None and (now - _cache["ts"]) < _CACHE_TTL:
            return cached
        try:
            data = _fetch_all()
        except Exception as exc:  # noqa: BLE001 — read path must never raise into callers
            if cached is not None:
                return {**cached, "stale": True, "error": str(exc)}
            return {"authenticated": True, "accounts": [], "totals": {}, "error": str(exc)}
        data["authenticated"] = True
        data["fetched_at"] = now
        _cache.update(ts=now, data=data)
        return data


async def get_schwab_accounts(force: bool = False) -> dict:
    """Async wrapper over the sync core — offloads the blocking GETs off the loop."""
    return await asyncio.to_thread(get_schwab_accounts_sync, force)
