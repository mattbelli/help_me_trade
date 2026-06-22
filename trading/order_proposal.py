"""Shared order proposal validation and OrderIntent field construction."""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from pathlib import Path
from typing import Any

from config.settings import primary_broker
from db.accounts import default_execution_account, get_account
from db.base import DEFAULT_DB_PATH
from db.sqlite import get_latest_snapshot
from imports.option_symbol import (
    OptionSymbol,
    parse_occ_symbol,
    parse_option_symbol,
    to_occ_symbol,
)
from trading.models import OrderIntent


def order_intent_from_fields(
    intent_fields: dict[str, Any],
    *,
    proposal_id: str,
    session_id: str | None,
    db_path: Path = DEFAULT_DB_PATH,
) -> OrderIntent:
    limit_raw = intent_fields.get("limit_price")
    broker = str(intent_fields["broker"])
    account_id = intent_fields.get("account_id")
    if not account_id:
        default = default_execution_account(db_path=db_path)
        if default is not None:
            account_id = default["id"]
            # Keep routing + audit broker in lockstep with the resolved account so
            # ExecutionService routes by account_id and the recorded broker can
            # never diverge from the account actually traded.
            broker = str(default["broker"]).strip().lower()
        else:
            account_id = None
    return OrderIntent(
        proposal_id=proposal_id,
        session_id=session_id,
        symbol=str(intent_fields["symbol"]),
        side=str(intent_fields["side"]),
        qty=Decimal(str(intent_fields["qty"])),
        order_type=str(intent_fields["order_type"]),
        limit_price=Decimal(str(limit_raw)) if limit_raw is not None else None,
        broker=broker,
        asset_class=str(intent_fields.get("asset_class", "us_equity")),
        occ_symbol=intent_fields.get("occ_symbol"),
        multiplier=int(str(intent_fields.get("multiplier", "1"))),
        account_id=account_id,
    )


def intent_fields_from_order(
    *,
    symbol: str,
    side: str,
    qty: Decimal,
    order_type: str,
    limit_price: Decimal | None,
    broker: str,
    asset_class: str,
    occ_symbol: str | None,
    multiplier: int,
    account_id: str | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "side": side,
        "qty": str(qty),
        "order_type": order_type,
        "limit_price": str(limit_price) if limit_price is not None else None,
        "broker": broker,
        "asset_class": asset_class,
        "occ_symbol": occ_symbol,
        "multiplier": str(multiplier),
        "account_id": account_id,
    }


def build_order_proposal_from_args(
    args: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Validate order args; return proposal display dict + canonical intent fields."""
    symbol = str(args.get("symbol", "")).strip().upper()
    side = str(args.get("side", "buy")).strip().lower()
    qty_raw = args.get("qty", 0)
    order_type = str(args.get("order_type", "market")).strip().lower()
    limit_price_raw = args.get("limit_price")
    session_id = args.get("session_id") or args.get("sessionId")
    session_id_str = str(session_id).strip() if session_id else None

    option_underlying = args.get("option_underlying")
    option_expiration = args.get("option_expiration")
    option_strike = args.get("option_strike")
    option_right = args.get("option_right")

    opt: OptionSymbol | None = None
    if option_underlying or option_expiration or option_strike or option_right:
        if (
            not option_underlying
            or not option_expiration
            or option_strike is None
            or not option_right
        ):
            return {
                "error": (
                    "option orders require option_underlying, option_expiration "
                    "(YYYY-MM-DD), option_strike, and option_right (C|P)"
                ),
            }
        try:
            exp = date_type.fromisoformat(str(option_expiration).strip())
        except ValueError:
            return {"error": "option_expiration must be YYYY-MM-DD"}
        try:
            strike = Decimal(str(option_strike))
        except Exception:
            return {"error": "option_strike must be a number"}
        right = str(option_right).strip().upper()
        if right not in ("C", "P"):
            return {"error": "option_right must be C or P"}
        opt = OptionSymbol(
            underlying=str(option_underlying).strip().upper(),
            expiration=exp,
            strike=strike,
            right=right,
        )
    elif symbol:
        opt = parse_option_symbol(symbol) or parse_occ_symbol(symbol)

    asset_class = "us_equity"
    occ_symbol: str | None = None
    multiplier = 1

    if opt is not None:
        order_type = "limit"
        if limit_price_raw is None:
            return {
                "error": (
                    "options require limit_price (per-share premium, e.g. 3.50)"
                ),
            }
        asset_class = "us_option"
        occ_symbol = to_occ_symbol(opt)
        symbol = opt.underlying
        multiplier = 100

    if not symbol:
        return {"error": "symbol is required"}
    if side not in ("buy", "sell"):
        return {"error": "side must be buy or sell"}
    try:
        qty = Decimal(str(qty_raw))
    except Exception:
        return {"error": "qty must be a number"}
    if qty <= 0:
        return {"error": "qty must be positive"}
    if order_type not in ("market", "limit"):
        return {"error": "order_type must be market or limit"}

    limit_price: Decimal | None = None
    if limit_price_raw is not None:
        try:
            limit_price = Decimal(str(limit_price_raw))
        except Exception:
            return {"error": "limit_price must be a number"}

    proposal_payload: dict[str, object] = {
        "symbol": symbol,
        "side": side,
        "qty": float(qty),
        "order_type": order_type,
        "limit_price": float(limit_price) if limit_price is not None else None,
        "asset_class": asset_class,
    }
    account_id_arg = args.get("account_id")
    if account_id_arg:
        account = get_account(str(account_id_arg), db_path=db_path)
        if account is None:
            return {"error": f"unknown account {account_id_arg!r}"}
    else:
        account = default_execution_account(db_path=db_path)

    if account is not None:
        broker = str(account["broker"]).strip().lower()
        resolved_account_id = account["id"]
    else:
        broker = primary_broker()
        resolved_account_id = None

    if opt is not None:
        proposal_payload.update(
            {
                "occ_symbol": occ_symbol,
                "option_underlying": opt.underlying,
                "option_expiration": opt.expiration.isoformat(),
                "option_strike": float(opt.strike),
                "option_right": opt.right,
                "multiplier": multiplier,
            }
        )

    if side == "sell":
        position_symbol = (occ_symbol or symbol).upper()
        snapshot = get_latest_snapshot(
            broker, account_id=resolved_account_id, db_path=db_path
        )
        held_qty: float | None = None
        if snapshot:
            for pos in snapshot.get("positions") or []:
                if str(pos.get("symbol", "")).upper() == position_symbol:
                    if pos.get("side") == "long":
                        held_qty = float(pos.get("qty", 0))
                    break
        if held_qty is not None:
            proposal_payload["held_qty"] = held_qty
        else:
            return {
                "error": (
                    f"no held long position for {position_symbol}; "
                    "sell-to-close requires an existing long"
                ),
            }

    intent_fields = intent_fields_from_order(
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=order_type,
        limit_price=limit_price,
        broker=broker,
        asset_class=asset_class,
        occ_symbol=occ_symbol,
        multiplier=multiplier,
        account_id=resolved_account_id,
    )

    return {
        "proposal": proposal_payload,
        "intent_fields": intent_fields,
        "session_id": session_id_str,
    }
