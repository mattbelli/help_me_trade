"""Broker trade ledger sync — auto on connect + manual refresh."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from brokers.alpaca import AlpacaAdapter
from brokers.errors import BrokerError
from brokers.factory import get_broker, get_enabled_broker_names
from brokers.schwab import SchwabAdapter
from brokers.schwab_auth import create_client, token_exists
from db.accounts import list_accounts
from db.trades import (
    get_or_create_broker_account,
    import_trade_events,
    latest_trade_date,
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
    """Map a Schwab Transactions-API TRADE to a ledger row.

    A Schwab TRADE has multiple ``transferItems``: CURRENCY legs (cash + each
    fee) and one or more security legs (EQUITY/OPTION/FUTURE). We key off the
    security leg — its ``amount`` is the signed share/contract count (>0 buy,
    <0 sell) and ``price`` the fill price — and use the transaction-level
    ``netAmount`` as the net cash (fee-inclusive, matching the JSON export's
    Amount column, so net-cash P&L stays consistent across both import paths).
    Fees are summed from the fee-typed CURRENCY legs. Multi-leg spreads collapse
    to their primary (largest-quantity) leg; net cash stays correct.
    """
    if str(item.get("type", "")).upper() != "TRADE":
        return None

    legs = item.get("transferItems") or []
    sec_legs = [
        leg
        for leg in legs
        if isinstance(leg, dict)
        and (leg.get("instrument") or {}).get("assetType") not in (None, "CURRENCY")
    ]
    if not sec_legs:
        return None

    leg = max(sec_legs, key=lambda x: abs(parse_dollar(str(x.get("amount", 0)))))
    instr = leg.get("instrument") or {}
    symbol = str(instr.get("uniformSymbol") or instr.get("symbol") or "").upper()
    if not symbol:
        return None

    shares = parse_dollar(str(leg.get("amount", 0)))
    if shares == 0:
        return None
    action = "Buy" if shares > 0 else "Sell"
    event_type, side = _classify_action(action)
    if event_type != "trade":
        return None
    qty = abs(shares)

    price_raw = leg.get("price")
    price = abs(parse_dollar(str(price_raw))) if price_raw is not None else None

    fees = Decimal("0")
    for fleg in legs:
        if isinstance(fleg, dict) and fleg.get("feeType"):
            fees += abs(parse_dollar(str(fleg.get("amount", 0))))

    net_raw = item.get("netAmount")
    amount = parse_dollar(str(net_raw)) if net_raw is not None else Decimal("0")

    trade_date_raw = (
        item.get("tradeDate") or item.get("time") or item.get("settlementDate") or ""
    )
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
        description=str(item.get("description", "")) or f"Schwab {action} {symbol}",
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
        transaction_types=[Client.Transactions.TransactionType.TRADE],
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


# Schwab trade-sync tunables.
_SCHWAB_REFETCH_DAYS = 7  # rolling window re-pulled each run (catches same-day/late fills)
_SCHWAB_CHUNK_PACE_S = 0.4  # gap between chunk GETs (Akamai-friendly)
_schwab_sync_lock = threading.Lock()


def _schwab_sync_start(
    json_max: date | None,
    api_max: date | None,
    end: date,
    lookback_days: int,
) -> date:
    """First date to fetch for an account (pure — unit-tested).

    - Never at/before the JSON-import boundary (its dedup key differs, so an
      overlap would double-count) → ``json_max + 1`` is a hard floor.
    - Once synced via the API, re-fetch a rolling ``_SCHWAB_REFETCH_DAYS`` window
      ending today, so **same-day and late-settling fills are never dropped** (the
      naive ``last + 1`` cursor lost them under the hourly loop); dedup filters
      anything already stored.
    - First-ever sync backfills ``lookback_days``.
    """
    if api_max is not None:
        start = api_max - timedelta(days=_SCHWAB_REFETCH_DAYS)
        if json_max is not None:
            start = max(start, json_max + timedelta(days=1))
        return start
    if json_max is not None:
        return json_max + timedelta(days=1)
    return end - timedelta(days=lookback_days)


def _sync_schwab_account(
    client: Any,
    acct: dict[str, Any],
    *,
    end: date,
    lookback_days: int,
) -> dict[str, Any]:
    """Sync one Schwab account. Isolated so one account can't starve the others."""
    account_id = acct["id"]
    adapter = SchwabAdapter(client, str(acct["broker_external_id"]))
    start = _schwab_sync_start(
        latest_trade_date(account_id, source="schwab_json_import"),
        latest_trade_date(account_id, source="schwab_api"),
        end,
        lookback_days,
    )
    if start > end:
        return {"account_id": account_id, "label": acct.get("label"), "up_to_date": True}

    events: list[ParsedTradeEvent] = []
    had_failure = False
    chunk_start = start
    paced = False
    while chunk_start <= end:  # ~60-day windows (Schwab API friendly)
        chunk_end = min(chunk_start + timedelta(days=59), end)
        if paced:
            time.sleep(_SCHWAB_CHUNK_PACE_S)
        paced = True
        try:
            raw = _schwab_fetch_transactions(adapter, start=chunk_start, end=chunk_end)
        except Exception:
            logger.exception(
                "Schwab sync chunk failed %s %s → %s",
                account_id,
                chunk_start,
                chunk_end,
            )
            had_failure = True
            chunk_start = chunk_end + timedelta(days=1)
            continue
        for item in raw:
            try:
                parsed = _schwab_transaction_to_event(item, account_id=account_id)
            except Exception:
                logger.exception("Schwab txn parse failed for %s", account_id)
                continue
            if parsed:
                events.append(parsed)
        chunk_start = chunk_end + timedelta(days=1)

    if had_failure:
        # A gap in the window means advancing the cursor would strand it forever;
        # import nothing and let the next run retry the whole window.
        return {
            "account_id": account_id,
            "label": acct.get("label"),
            "from": start.isoformat(),
            "incomplete": True,
            "fetched": len(events),
            "inserted": 0,
            "duplicates": 0,
        }

    result = import_trade_events(
        account_id,
        events,
        source="schwab_api",
        from_date=start.isoformat(),
        to_date=end.isoformat(),
    )
    set_account_last_synced(account_id)
    return {
        "account_id": account_id,
        "label": acct.get("label"),
        "from": start.isoformat(),
        "fetched": len(events),
        "inserted": result.inserted,
        "duplicates": result.duplicates,
    }


def sync_schwab(*, lookback_days: int = 365) -> dict[str, Any]:
    """Read-only: pull Schwab Transactions-API history for EACH linked account,
    attributing every trade to the correct ``trade_accounts`` row by its
    ``broker_external_id`` (the account hash).

    Incremental + rolling per account (see ``_schwab_sync_start``): never
    re-imports the JSON-export history, never drops same-day fills. One shared
    OAuth client + a per-account adapter (works with the multiple linked accounts
    ``SchwabAdapter.from_settings`` cannot). GET-only — never places, cancels, or
    mutates an order. A non-blocking lock prevents overlapping runs.
    """
    if not token_exists():
        return {"broker": "schwab", "skipped": "no token"}
    if not _schwab_sync_lock.acquire(blocking=False):
        return {"broker": "schwab", "skipped": "already running"}
    try:
        accounts = [
            a
            for a in list_accounts(broker="schwab", enabled_only=True)
            if a.get("broker_external_id")
        ]
        if not accounts:
            return {"broker": "schwab", "skipped": "no mapped accounts"}

        client = create_client()
        end = date.today()
        per_account: list[dict[str, Any]] = []
        total_fetched = total_inserted = total_dupes = 0

        for acct in accounts:
            try:
                r = _sync_schwab_account(
                    client, acct, end=end, lookback_days=lookback_days
                )
            except Exception:
                logger.exception("Schwab sync failed for account %s", acct.get("id"))
                per_account.append(
                    {"account_id": acct.get("id"), "error": "account_sync_failed"}
                )
                continue
            per_account.append(r)
            total_fetched += int(r.get("fetched", 0) or 0)
            total_inserted += int(r.get("inserted", 0) or 0)
            total_dupes += int(r.get("duplicates", 0) or 0)

        return {
            "broker": "schwab",
            "fetched": total_fetched,
            "inserted": total_inserted,
            "duplicates": total_dupes,
            "accounts": per_account,
        }
    finally:
        _schwab_sync_lock.release()


def sync_broker(broker: str) -> dict[str, Any]:
    broker = broker.strip().lower()
    if broker == "alpaca":
        return sync_alpaca()
    if broker == "schwab":
        return sync_schwab()
    raise BrokerError(f"Unsupported broker for sync: {broker}")


def sync_all_enabled_brokers() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    names = list(get_enabled_broker_names())
    # Schwab trade-ledger sync is read-only + safe, so run it whenever a Schwab
    # token exists — independent of brokers.schwab.enabled, which also gates the
    # per-broker snapshot poll we intentionally leave off for Schwab.
    if "schwab" not in names and token_exists():
        names.append("schwab")
    for name in names:
        try:
            results.append(sync_broker(name))
        except Exception:
            logger.exception("Trade sync failed for %s", name)
            results.append({"broker": name, "error": "sync_failed"})
    return results


async def async_sync_all_enabled_brokers() -> list[dict[str, Any]]:
    return await asyncio.to_thread(sync_all_enabled_brokers)
