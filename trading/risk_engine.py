"""Deterministic pre-trade risk validation."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from brokers.alpaca import AlpacaAdapter
from brokers.factory import get_broker
from brokers.market_stream import market_hub
from config.settings import is_kill_switch_active, trading_config
from db.accounts import account_is_execution_capable
from db.base import DEFAULT_DB_PATH
from db.order_executions import count_executions_today
from db.sqlite import get_latest_snapshot
from imports.option_symbol import parse_occ_symbol
from trading.models import OrderIntent, RiskCheckResult

_SNAPSHOT_MAX_AGE = timedelta(minutes=5)
_SYMBOL_RE = re.compile(r"^[A-Z]+$")
_OCC_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


class RiskEngine:
    @staticmethod
    async def validate(
        intent: OrderIntent,
        *,
        db_path: Path = DEFAULT_DB_PATH,
    ) -> RiskCheckResult:
        checks: dict[str, str] = {}
        cfg = trading_config()
        is_option = intent.asset_class == "us_option"
        # Short selling is honored ONLY when explicitly enabled AND not live
        # (live shorting stays hard-blocked regardless of the toggle). A short
        # (sell with no held long) is treated like a buy for the exposure caps
        # below, since it INCREASES exposure rather than closing a position.
        short_allowed = bool(cfg.get("allow_short_selling", False)) and cfg.get(
            "execution_mode"
        ) in ("dry_run", "paper")
        is_short_open = False

        # 1. Kill switch
        if is_kill_switch_active():
            checks["kill_switch"] = "fail: kill switch active"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="kill switch active",
            )
        checks["kill_switch"] = "pass"

        # Account must be execution-capable — deny-by-default AT THE CHOKEPOINT.
        # approve_proposal also guards this, but enforcing it here makes submit
        # self-defending regardless of caller. account_id None = legacy/primary path.
        if intent.account_id is not None and not account_is_execution_capable(
            intent.account_id, db_path=db_path
        ):
            checks["account_execution_capable"] = "fail: account not execution-capable"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="account not execution-capable",
            )
        if intent.account_id is not None:
            checks["account_execution_capable"] = "pass"

        concentration_symbol: str

        if is_option:
            # Options: long-only (buy-to-open / sell-to-close), limit-only
            if intent.order_type != "limit" or intent.limit_price is None:
                checks["option_order_type"] = "fail: limit required"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="options require a limit order with an explicit premium",
                )
            checks["option_order_type"] = "pass"

            occ = (intent.occ_symbol or "").strip().upper()
            if not occ or not _OCC_RE.match(occ):
                checks["symbol_sanity"] = "fail: invalid occ symbol"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="invalid symbol",
                )
            checks["symbol_sanity"] = "pass"

            parsed = parse_occ_symbol(occ)
            if parsed is None:
                checks["symbol_sanity"] = "fail: invalid occ symbol"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="invalid symbol",
                )

            today = datetime.now(UTC).date()
            if intent.side == "buy" and parsed.expiration <= today:
                checks["option_expiry"] = "fail: contract expired or expiring today"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="option contract expired or expiring today",
                )
            if intent.side == "sell" and parsed.expiration < today:
                checks["option_expiry"] = "fail: contract expired"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="option contract expired",
                )
            checks["option_expiry"] = "pass"

            try:
                broker = get_broker(intent.broker)
                if not isinstance(broker, AlpacaAdapter):
                    checks["symbol_tradable"] = "fail: broker does not support option lookup"
                    return RiskCheckResult(
                        passed=False,
                        checks=checks,
                        blocked_reason="symbol lookup unavailable",
                    )
                tradable = await asyncio.to_thread(broker.option_contract_exists, occ)
                if not tradable:
                    checks["symbol_tradable"] = f"fail: {occ} not tradable"
                    return RiskCheckResult(
                        passed=False,
                        checks=checks,
                        blocked_reason=f"{occ} not tradable",
                    )
            except Exception as exc:
                checks["symbol_tradable"] = f"fail: lookup error ({exc})"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="symbol lookup failed",
                )
            checks["symbol_tradable"] = "pass"
            concentration_symbol = occ
        else:
            symbol = intent.symbol.strip().upper()

            # 2. Symbol sanity (equity)
            if not symbol or len(symbol) > 5 or not _SYMBOL_RE.match(symbol):
                checks["symbol_sanity"] = "fail: invalid symbol"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="invalid symbol",
                )
            checks["symbol_sanity"] = "pass"

            # 3. Symbol tradable (equity)
            try:
                broker = get_broker(intent.broker)
                if not isinstance(broker, AlpacaAdapter):
                    checks["symbol_tradable"] = "fail: broker does not support symbol lookup"
                    return RiskCheckResult(
                        passed=False,
                        checks=checks,
                        blocked_reason="symbol lookup unavailable",
                    )
                tradable = await asyncio.to_thread(broker.symbol_exists, symbol)
                if not tradable:
                    checks["symbol_tradable"] = f"fail: {symbol} not tradable"
                    return RiskCheckResult(
                        passed=False,
                        checks=checks,
                        blocked_reason=f"{symbol} not tradable",
                    )
            except Exception as exc:
                checks["symbol_tradable"] = f"fail: lookup error ({exc})"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="symbol lookup failed",
                )
            checks["symbol_tradable"] = "pass"
            concentration_symbol = symbol

        # 4. Qty bounds
        qty = intent.qty
        if qty <= Decimal("0") or qty > Decimal("10000"):
            checks["qty_bounds"] = "fail: qty out of bounds"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="qty out of bounds",
            )
        if qty != qty.to_integral_value():
            checks["qty_bounds"] = "fail: fractional shares not supported"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="fractional shares not supported",
            )
        checks["qty_bounds"] = "pass"

        # 5. Snapshot freshness
        snapshot = get_latest_snapshot(
            intent.broker, account_id=intent.account_id, db_path=db_path
        )
        if snapshot is None:
            checks["snapshot_freshness"] = "fail: no portfolio snapshot"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="no portfolio snapshot",
            )
        captured_at_raw = snapshot.get("captured_at")
        if not captured_at_raw:
            checks["snapshot_freshness"] = "fail: snapshot missing captured_at"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="stale or missing snapshot",
            )
        try:
            captured_at = datetime.fromisoformat(str(captured_at_raw))
            if captured_at.tzinfo is None:
                captured_at = captured_at.replace(tzinfo=UTC)
        except ValueError:
            checks["snapshot_freshness"] = "fail: invalid snapshot timestamp"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="stale or missing snapshot",
            )
        age = datetime.now(UTC) - captured_at
        if age > _SNAPSHOT_MAX_AGE:
            checks["snapshot_freshness"] = "fail: snapshot stale"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="snapshot stale",
            )
        checks["snapshot_freshness"] = "pass"

        account = snapshot.get("account") or {}
        buying_power = Decimal(str(account.get("buying_power", 0)))
        portfolio_value = Decimal(str(account.get("portfolio_value", 0)))
        open_orders = snapshot.get("open_orders") or []
        positions = snapshot.get("positions") or []

        if intent.side == "sell":
            pos = None
            for p in positions:
                if str(p.get("symbol", "")).upper() == concentration_symbol:
                    pos = p
                    break
            held_long = (
                pos is not None
                and pos.get("side") == "long"
                and Decimal(str(pos.get("qty", 0))) > Decimal("0")
            )
            if held_long:
                held = Decimal(str(pos["qty"]))
                pending_sell = Decimal("0")
                for o in open_orders:
                    if (
                        str(o.get("symbol", "")).upper() == concentration_symbol
                        and o.get("side") == "sell"
                    ):
                        # Clamp per-order remaining at 0 — a data anomaly where
                        # filled_qty > qty must never inflate `available` into an oversell.
                        pending_sell += max(
                            Decimal("0"),
                            Decimal(str(o.get("qty", 0)))
                            - Decimal(str(o.get("filled_qty", 0))),
                        )
                available = held - pending_sell
                if qty > available:
                    # Selling MORE than the held long would flip into a short
                    # mid-close. Never do that implicitly — a short must start
                    # from flat. Block regardless of the short toggle.
                    checks["sell_position"] = (
                        f"fail: sell qty {qty} exceeds available {available}"
                    )
                    return RiskCheckResult(
                        passed=False,
                        checks=checks,
                        blocked_reason=(
                            f"sell qty exceeds available position "
                            f"(held {held}, pending sells {pending_sell})"
                        ),
                    )
                checks["sell_position"] = f"pass: closing {qty} of {held} long"
            elif short_allowed and not is_option:
                # No held long → this OPENS/extends a short. Allowed only because
                # the toggle is on and we're not live. EQUITY ONLY — options stay
                # long-only (no naked writing), so the toggle must not enable a
                # sell-to-open on an option. Exposure caps (buying power / value /
                # concentration) apply below, same as a buy.
                is_short_open = True
                checks["sell_position"] = f"pass: short-to-open {qty} (no held long)"
            else:
                checks["sell_position"] = "fail: no held long position"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason=(
                        "sell-to-close requires a held long position; "
                        "short selling not enabled"
                    ),
                )
        else:
            checks["sell_position"] = "pass: buy"

        if is_option:
            checks["live_quote"] = "pass: skipped for options"
            spend_price = intent.limit_price  # type: ignore[assignment]
            risk_price = intent.limit_price  # type: ignore[assignment]
            # Chokepoint self-enforces the contract multiplier — never trust the
            # caller-provided intent.multiplier for the risk math.
            multiplier = Decimal("100")
            spend_notional = qty * spend_price * multiplier
            risk_notional = qty * risk_price * multiplier
        else:
            market_price = _live_market_price(concentration_symbol)
            if market_price is None:
                checks["live_quote"] = "fail: no live quote"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="no live quote",
                )
            checks["live_quote"] = "pass"

            if intent.order_type == "limit":
                if intent.limit_price is None:
                    checks["live_quote"] = "fail: limit order missing limit_price"
                    return RiskCheckResult(
                        passed=False,
                        checks=checks,
                        blocked_reason="no live quote",
                    )
                spend_price = intent.limit_price
                risk_price = max(intent.limit_price, market_price)
            else:
                spend_price = market_price
                risk_price = market_price

            spend_notional = qty * spend_price
            risk_notional = qty * risk_price

        # 6. Buying power (buys + short-opens — both consume buying power)
        if intent.side == "buy" or is_short_open:
            max_spend = buying_power * Decimal("0.95")
            if spend_notional > max_spend:
                checks["buying_power"] = "fail: insufficient buying power"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="insufficient buying power",
                )
            checks["buying_power"] = "pass"
        else:
            checks["buying_power"] = "pass: sell"

        # 7. Per-order value cap (buys + short-opens — closes must not be blocked)
        if intent.side == "buy" or is_short_open:
            max_order_value = Decimal(str(cfg["max_order_value_usd"]))
            if risk_notional > max_order_value:
                checks["order_value_cap"] = "fail: order exceeds max value"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="order exceeds max value",
                )
            checks["order_value_cap"] = "pass"
        else:
            checks["order_value_cap"] = "pass: sell"

        # 8. Concentration (buys + short-opens — bound the position size either way)
        if intent.side == "buy" or is_short_open:
            if portfolio_value <= Decimal("0"):
                checks["concentration"] = "fail: portfolio value unknown"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="portfolio value unknown",
                )
            existing_mv = Decimal("0")
            for pos in positions:
                if str(pos.get("symbol", "")).upper() == concentration_symbol:
                    # Use absolute exposure so an existing SHORT (negative market
                    # value) still counts toward the concentration ceiling.
                    existing_mv = abs(Decimal(str(pos.get("market_value", 0))))
                    break
            max_position_pct = Decimal(str(cfg["max_position_pct"]))
            concentration_pct = (existing_mv + risk_notional) / portfolio_value * Decimal("100")
            if concentration_pct > max_position_pct:
                checks["concentration"] = "fail: concentration limit exceeded"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="concentration limit exceeded",
                )
            checks["concentration"] = "pass"
        else:
            checks["concentration"] = "pass: sell"

        # 9. Open-orders cap
        max_open_orders = int(cfg["max_open_orders"])
        if len(open_orders) >= max_open_orders:
            checks["open_orders_cap"] = "fail: too many open orders"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="too many open orders",
            )
        checks["open_orders_cap"] = "pass"

        # 10. Daily-order counter
        max_daily_orders = int(cfg["max_daily_orders"])
        today_count = count_executions_today(db_path=db_path)
        if today_count >= max_daily_orders:
            checks["daily_orders"] = "fail: daily order limit reached"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="daily order limit reached",
            )
        # Per-account ceiling (same threshold): an account can't exceed the daily cap
        # on its own even when the global aggregate still has room.
        if intent.account_id is not None:
            account_today = count_executions_today(
                account_id=intent.account_id, db_path=db_path
            )
            if account_today >= max_daily_orders:
                checks["daily_orders_account"] = "fail: account daily order limit reached"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="account daily order limit reached",
                )
            checks["daily_orders_account"] = "pass"
        checks["daily_orders"] = "pass"

        return RiskCheckResult(
            passed=True,
            checks=checks,
            blocked_reason=None,
            is_short_open=is_short_open,
        )


def _live_market_price(symbol: str) -> Decimal | None:
    quotes = market_hub.quote_snapshot({symbol})
    quote = quotes.get(symbol)
    if not quote:
        return None
    price_raw = quote.get("price")
    if price_raw is None or float(price_raw) <= 0:
        return None
    return Decimal(str(price_raw))
