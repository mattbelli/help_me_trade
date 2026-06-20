"""Broker trade ledger sync — auto on connect + manual refresh."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from brokers.alpaca import AlpacaAdapter
from brokers.errors import BrokerError
from brokers.factory import get_broker, get_enabled_broker_names
from brokers.schwab import SchwabAdapter
from db.trades import (
    get_or_create_broker_account,
    import_trade_events,
    set_account_last_synced,
)
from imports.alpaca_activities import parse_alpaca_activities
from imports.base import ParsedTradeEvent
from imports.money import parse_dollar
from imports.schwab_transactions import _classify_action, _external_key

logger = logging.getLogger(__name__)


def _alpaca_fetch_all_fills(adapter: AlpacaAdapter) -> list[dict[str, Any]]:
    """Paginate Alpaca FILL activities (full account history)."""
    fills: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {
            "activity_types": "FILL",
            "direction": "desc",
            "page_size": 100,
        }
        if page_token:
            params["page_token"] = page_token
        batch = adapter.get_account_activities(params)
        if not batch:
            break
        fills.extend(batch)
        if len(batch) < 100:
            break
        page_token = str(batch[-1].get("id", ""))
        if not page_token:
            break
    return fills


def _schwab_transaction_to_event(
    item: dict[str, Any],
    *,
    account_id: str,
) -> ParsedTradeEvent | None:
    """Best-effort map Schwab API transaction to ledger row."""
    tx_type = str(item.get("type", "")).upper()
    if tx_type and tx_type != "TRADE":
        return None

    transfer_items = item.get("transferItems") or []
    symbol = None
    qty = None
    price = None
    amount = Decimal("0")
    fees = Decimal("0")
    side = None
    description = str(item.get("description", ""))

    for leg in transfer_items:
        if not isinstance(leg, dict):
            continue
        instr = leg.get("instrument") or {}
        sym = instr.get("symbol") or leg.get("symbol")
        if sym and not symbol:
            symbol = str(sym).upper()
        fee_type = leg.get("feeType")
        if fee_type:
            fees += abs(parse_dollar(str(leg.get("amount", 0))))
            continue
        leg_amount = parse_dollar(str(leg.get("amount", 0)))
        amount += leg_amount
        qty_raw = leg.get("quantity") or instr.get("quantity")
        if qty_raw is not None:
            qty = abs(parse_dollar(str(qty_raw)))
        price_raw = leg.get("price")
        if price_raw is not None:
            price = abs(parse_dollar(str(price_raw)))

    if not symbol:
        return None

    action = "Buy" if amount < 0 else "Sell"
    event_type, side = _classify_action(action)
    if event_type != "trade":
        return None

    trade_date_raw = item.get("tradeDate") or item.get("settlementDate") or ""
    try:
        if isinstance(trade_date_raw, str) and trade_date_raw:
            if "T" in trade_date_raw:
                trade_date = datetime.fromisoformat(
                    trade_date_raw.replace("Z", "+00:00")
                ).date()
            else:
                trade_date = date.fromisoformat(trade_date_raw[:10])
        else:
            return None
    except ValueError:
        return None

    tx_id = str(item.get("activityId") or item.get("transactionId") or "")
    external_key = (
        hashlib.sha256(f"schwab_api|{account_id}|{tx_id}".encode()).hexdigest()
        if tx_id
        else _external_key(
            account_id, trade_date, action, symbol, qty, price, amount, fees
        )
    )

    return ParsedTradeEvent(
        event_type="trade",
        trade_date=trade_date,
        symbol=symbol,
        side=side,
        quantity=qty,
        price=price,
        fees=fees,
        amount=amount,
        description=description or f"Schwab {action} {symbol}",
        external_key=external_key,
        raw=item,
        broker_txn_id=tx_id.strip() or None,
    )


def _schwab_fetch_transactions(
    adapter: SchwabAdapter,
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    from schwab.client import Client

    response = adapter._client.get_transactions(
        adapter._account_hash,
        start_date=datetime.combine(start, datetime.min.time()),
        end_date=datetime.combine(end, datetime.max.time()),
        transaction_types=[Client.TransactionType.TRADE],
    )
    response.raise_for_status()
    data = response.json()
    if isinstance(data, list):
        return data
    return []


def sync_alpaca() -> dict[str, Any]:
    adapter = get_broker("alpaca")
    if not isinstance(adapter, AlpacaAdapter):
        raise BrokerError("Alpaca adapter required")

    account = get_or_create_broker_account("alpaca", label="Alpaca")
    raw = _alpaca_fetch_all_fills(adapter)
    events = parse_alpaca_activities(raw, account_id=account["id"])
    result = import_trade_events(
        account["id"],
        events,
        source="alpaca_api",
        from_date=min((e.trade_date.isoformat() for e in events), default=None),
        to_date=max((e.trade_date.isoformat() for e in events), default=None),
    )
    set_account_last_synced(account["id"])
    return {
        "broker": "alpaca",
        "fetched": len(raw),
        "parsed": len(events),
        "inserted": result.inserted,
        "duplicates": result.duplicates,
    }


def sync_schwab(*, lookback_days: int = 365) -> dict[str, Any]:
    adapter = get_broker("schwab")
    if not isinstance(adapter, SchwabAdapter):
        raise BrokerError("Schwab adapter required")

    account = get_or_create_broker_account("schwab", label="Schwab")
    end = date.today()
    start = end - timedelta(days=lookback_days)
    all_events: list[ParsedTradeEvent] = []

    # Chunk into ~60-day windows (Schwab API friendly)
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=59), end)
        try:
            raw = _schwab_fetch_transactions(adapter, start=chunk_start, end=chunk_end)
        except Exception:
            logger.exception(
                "Schwab sync chunk failed %s → %s", chunk_start, chunk_end
            )
            chunk_start = chunk_end + timedelta(days=1)
            continue
        for item in raw:
            parsed = _schwab_transaction_to_event(item, account_id=account["id"])
            if parsed:
                all_events.append(parsed)
        chunk_start = chunk_end + timedelta(days=1)

    result = import_trade_events(
        account["id"],
        all_events,
        source="schwab_api",
        from_date=start.isoformat(),
        to_date=end.isoformat(),
    )
    set_account_last_synced(account["id"])
    return {
        "broker": "schwab",
        "fetched": len(all_events),
        "parsed": len(all_events),
        "inserted": result.inserted,
        "duplicates": result.duplicates,
        "note": "Schwab API may not return full lifetime history — use Import for complete Schwab exports.",
    }


def sync_broker(broker: str) -> dict[str, Any]:
    broker = broker.strip().lower()
    if broker == "alpaca":
        return sync_alpaca()
    if broker == "schwab":
        return sync_schwab()
    raise BrokerError(f"Unsupported broker for sync: {broker}")


def sync_all_enabled_brokers() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name in get_enabled_broker_names():
        try:
            results.append(sync_broker(name))
        except Exception:
            logger.exception("Trade sync failed for %s", name)
            results.append({"broker": name, "error": "sync_failed"})
    return results


async def async_sync_all_enabled_brokers() -> list[dict[str, Any]]:
    return await asyncio.to_thread(sync_all_enabled_brokers)
