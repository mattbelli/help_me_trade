"""Shared order proposal validation and OrderIntent field construction."""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from pathlib import Path
from typing import Any

from config.settings import primary_broker, short_selling_allowed, trading_config
from db.accounts import (
    account_is_execution_capable,
    default_execution_account,
    get_account,
)
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
    # Book entries: TIF/session eligibility is decided at INTENT-BUILD time —
    # i.e. at approval, against the clock right then — never frozen at
    # proposal creation.
    #   moo  → opg before the ~9:28 ET auction cutoff, day market after
    #          (market-at-approval, spec §5.5);
    #   limit → GTC limit at the analyst's price, regular hours only;
    #   ext  → GTC limit + extended_hours (pre/post/overnight-eligible — act
    #          near the decision price, accepting gap exposure before the
    #          stop can first act at the next open).
    # GTC working orders are the desk's standing orders: the nightly pack
    # shows them and the desk cancels/adjusts (founder 07-24) — they never
    # silently die at a session boundary.
    entry_style = str(intent_fields.get("entry_style") or "")
    book_entry = entry_style in ("moo", "book_limit", "book_ext")
    extended_hours = entry_style == "book_ext"
    if entry_style == "moo":
        from trading.market_hours import moo_time_in_force

        # Paper routes to Alpaca's paper endpoint, which never fills opg
        # (no opening auction) — force a queued market DAY fill there instead.
        paper = trading_config()["execution_mode"] == "paper"
        time_in_force = moo_time_in_force(paper=paper)
    elif entry_style in ("book_limit", "book_ext"):
        time_in_force = "gtc"
    else:
        time_in_force = "day"
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
        allow_fractional=bool(intent_fields.get("allow_fractional", False)),
        time_in_force=time_in_force,
        extended_hours=extended_hours,
        book_entry=book_entry,
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
    allow_fractional: bool = False,
    entry_style: str = "immediate",
    stop_price: Decimal | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
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
        "allow_fractional": allow_fractional,
    }
    if entry_style != "immediate":
        fields["entry_style"] = entry_style
    if stop_price is not None:
        # Metadata, not an entry-order param: the stop guard places this as a
        # resting GTC stop AFTER the entry fills. Never set on the entry intent.
        fields["stop_price"] = str(stop_price)
    return fields


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

    entry_style = str(args.get("entry_style", "immediate")).strip().lower()
    if entry_style not in ("immediate", "moo", "book_limit", "book_ext"):
        return {"error": "entry_style must be immediate, moo, book_limit, or book_ext"}
    # Book styles are whole-share EQUITY shapes; the standing GTC styles are
    # buys only (their cap exemptions + overnight eligibility are entry
    # semantics). A moo SELL is the book's trim — market at the next open,
    # priced off the reference close after hours like a moo buy
    # (trading/book_orders.propose_trim).
    if entry_style != "immediate" and opt is not None:
        return {"error": "book entries are whole-share equity orders"}
    if entry_style in ("book_limit", "book_ext") and side != "buy":
        return {"error": "GTC book entries are BUYS"}
    if entry_style == "moo" and order_type != "market":
        return {"error": "moo entries are market orders (opening auction)"}

    stop_price: Decimal | None = None
    stop_raw = args.get("stop_price")
    if stop_raw is not None:
        if side != "buy" or opt is not None:
            return {"error": "stop_price only applies to equity buy entries"}
        try:
            stop_price = Decimal(str(stop_raw))
        except Exception:
            return {"error": "stop_price must be a number"}
        if stop_price <= 0:
            return {"error": "stop_price must be positive"}

    limit_price: Decimal | None = None
    if limit_price_raw is not None:
        try:
            limit_price = Decimal(str(limit_price_raw))
        except Exception:
            return {"error": "limit_price must be a number"}

    if entry_style in ("book_limit", "book_ext") and (
        order_type != "limit" or limit_price is None or limit_price <= 0
    ):
        return {"error": "book limit/ext entries require a positive limit_price"}

    proposal_payload: dict[str, object] = {
        "symbol": symbol,
        "side": side,
        "qty": float(qty),
        "order_type": order_type,
        "limit_price": float(limit_price) if limit_price is not None else None,
        "asset_class": asset_class,
    }
    if entry_style != "immediate":
        proposal_payload["entry_style"] = entry_style
    if stop_price is not None:
        proposal_payload["stop_price"] = float(stop_price)
    account_id_arg = args.get("account_id")
    if account_id_arg:
        account = get_account(str(account_id_arg), db_path=db_path)
        if account is None:
            return {"error": f"unknown account {account_id_arg!r}"}
        # Deny-by-default at the shared proposal intake: a proposal may only ever
        # target an execution-capable (paper/Alpaca) account. Real read-only
        # Schwab accounts are the founder's funded money and are never tradeable —
        # reject them here, before any proposal row is created, on EVERY surface
        # that resolves an account (chat tool + mobile phone proposal both flow
        # through here). ExecutionService/RiskEngine remain the backstop; this
        # stops an un-executable Schwab proposal from ever being created/shown.
        if not account_is_execution_capable(str(account_id_arg), db_path=db_path):
            return {
                "error": (
                    f"account {account_id_arg!r} is read-only real money (e.g. a "
                    "Schwab account) — orders can only target a paper account "
                    "(STRAT1/STRAT2)"
                )
            }
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
        elif short_selling_allowed() and not occ_symbol:
            # No held long, but shorting is enabled (paper/dry_run) — this is a
            # sell-to-open. EQUITY ONLY — options stay long-only (no naked
            # writing). The RiskEngine re-checks at submit and applies the
            # buy-style exposure caps to the short.
            proposal_payload["short_open"] = True
        else:
            return {
                "error": (
                    f"no held long position for {position_symbol}; "
                    "sell-to-close requires an existing long"
                ),
            }

    # Fractional shares are an equity-only concept (no fractional options contracts).
    allow_fractional = bool(args.get("allow_fractional", False)) and asset_class == "us_equity"

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
        allow_fractional=allow_fractional,
        entry_style=entry_style,
        stop_price=stop_price,
    )

    return {
        "proposal": proposal_payload,
        "intent_fields": intent_fields,
        "session_id": session_id_str,
    }
