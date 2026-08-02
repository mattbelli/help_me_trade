"""Deterministic pre-trade risk validation."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from brokers.alpaca import AlpacaAdapter
from brokers.factory import get_broker
from brokers.market_stream import market_hub
from config.settings import (
    DEFAULT_SETTINGS,
    apply_settings_patch,
    is_entries_halted,
    is_kill_switch_active,
    trading_config,
)
from db.accounts import account_is_execution_capable
from db.base import DEFAULT_DB_PATH
from db.order_executions import (
    count_executions_today,
    sum_inflight_notional,
    sum_inflight_sell_qty,
    sum_today_notional,
)
from db.risk_breaker import current_stage, get_or_create_baseline, trip
from db.sqlite import get_latest_snapshot
from imports.option_symbol import parse_occ_symbol
from signals.notify import notify
from trading.market_hours import is_market_open
from trading.models import OrderIntent, RiskCheckResult

logger = logging.getLogger(__name__)

_SNAPSHOT_MAX_AGE = timedelta(minutes=5)
_SYMBOL_RE = re.compile(r"^[A-Z]+$")
_OCC_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


def _account_key(intent: OrderIntent) -> str:
    return intent.account_id or f"{intent.broker}:primary"


class RiskEngine:
    @staticmethod
    async def validate(
        intent: OrderIntent,
        *,
        db_path: Path = DEFAULT_DB_PATH,
        exclude_execution_id: str | None = None,
    ) -> RiskCheckResult:
        checks: dict[str, str] = {}
        cfg = {**DEFAULT_SETTINGS["trading"], **trading_config()}
        is_option = intent.asset_class == "us_option"
        # Short selling is honored ONLY when explicitly enabled AND not live
        # (live shorting stays hard-blocked regardless of the toggle). A short
        # (sell with no held long) is treated like a buy for the exposure caps
        # below, since it INCREASES exposure rather than closing a position.
        short_allowed = bool(cfg.get("allow_short_selling", False)) and cfg.get(
            "execution_mode"
        ) in ("dry_run", "paper")
        is_short_open = False
        is_buy_to_cover = False

        # 1. Kill switch
        if is_kill_switch_active():
            checks["kill_switch"] = "fail: kill switch active"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="kill switch active",
            )
        checks["kill_switch"] = "pass"

        # Order-shape sanity — deny-by-default for the new tif/stop surface
        # (books round, 2026-07-23). Everything outside the three known TIFs,
        # or a stop order that isn't a whole-share reduce-only equity sell,
        # is rejected before any pricing happens.
        if intent.time_in_force not in ("day", "opg", "gtc"):
            checks["order_shape"] = f"fail: unknown time_in_force {intent.time_in_force!r}"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason=f"unknown time_in_force {intent.time_in_force!r}",
            )
        if intent.order_type == "stop":
            if (
                intent.side != "sell"
                or is_option
                or intent.stop_price is None
                or intent.stop_price <= Decimal("0")
            ):
                checks["order_shape"] = (
                    "fail: stop orders are protective equity sells with a positive stop_price"
                )
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="invalid stop order shape",
                )
        elif intent.stop_price is not None:
            checks["order_shape"] = "fail: stop_price on a non-stop order"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="stop_price on a non-stop order",
            )
        if intent.time_in_force == "opg" and intent.qty != intent.qty.to_integral_value():
            # Alpaca fractional orders are market+day only — an opg fractional
            # would be rejected at the broker; fail it here with a clear reason.
            checks["order_shape"] = "fail: opg orders must be whole-share"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="opg orders must be whole-share",
            )
        if intent.extended_hours and (
            intent.order_type != "limit"
            or intent.time_in_force not in ("day", "gtc")
            or is_option
            or intent.qty != intent.qty.to_integral_value()
        ):
            # Alpaca extended-hours/overnight eligibility: whole-share equity
            # LIMIT orders, day or gtc TIF. Anything else with the flag is a
            # shape error here, not a broker surprise later.
            checks["order_shape"] = (
                "fail: extended_hours requires a whole-share equity day/gtc limit"
            )
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="extended_hours requires a whole-share equity day/gtc limit",
            )
        checks["order_shape"] = "pass"

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
        # Fractional shares are permitted on two signals, both of which size by dollar
        # weight to match a dollar-weighted backtest and would otherwise leave cash idle
        # and drop every name priced above the per-name budget:
        #   - rebalance=True (momentum rebalance path) — ALSO exempts the count throttles.
        #   - allow_fractional=True (percent-of-equity strategy entries + the exit engine
        #     closing a fractional position) — lifts ONLY this whole-share invariant, no
        #     throttle exemption, and EQUITY ONLY (there are no fractional option contracts;
        #     deny-by-default self-defence against a future caller mis-flagging an option).
        # Every OTHER path keeps the whole-share invariant (deny-by-default). The qty range
        # bound above still applies to both.
        fractional_ok = intent.rebalance or (
            intent.allow_fractional and intent.asset_class == "us_equity"
        )
        if qty != qty.to_integral_value() and not fractional_ok:
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
        # Defense in depth at the chokepoint: never validate an order against a
        # portfolio that is not the account's own. get_latest_snapshot no longer
        # falls back across accounts, so this cannot fire today — it is here
        # because this exact invariant broke once before (trading/exit_engine.py
        # shipped its equivalent guard inverted and skipped every position for
        # weeks), and buying power is what this function gates on.
        if (
            intent.account_id is not None
            and snapshot.get("account_id") != intent.account_id
        ):
            checks["snapshot_freshness"] = "fail: snapshot belongs to another account"
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
                # The snapshot only covers sells the broker has echoed back, and it
                # stays valid for _SNAPSHOT_MAX_AGE — so a sell submitted since the
                # last poll is invisible here and two of them would each validate
                # against the whole position. Reserve those from our own records.
                # Kept SEPARATE from pending_sell on purpose: blocked_by_pending_sells
                # below is the chokepoint's signal that cancelling our own resting
                # stops could unblock this order, and cancelling a stop does nothing
                # about an in-flight sell. Folding the two together sent the release
                # path after a live protective stop it could not help — and the
                # re-stop was then blocked too, leaving the position bare
                # (adversarial review HIGH-2, both reviewers).
                inflight_sell = sum_inflight_sell_qty(
                    symbol=concentration_symbol,
                    account_id=intent.account_id,
                    exclude_execution_id=exclude_execution_id,
                    known_broker_order_ids=[
                        o.get("id") for o in open_orders if o.get("id") is not None
                    ],
                    snapshot_captured_at=captured_at,
                    db_path=db_path,
                )
                available = held - pending_sell - inflight_sell
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
                            f"(held {held}, pending sells {pending_sell}"
                            + (
                                f", in-flight sells {inflight_sell}"
                                if inflight_sell > 0
                                else ""
                            )
                            + ")"
                        ),
                        # Reported ONLY when releasing our resting sells would
                        # actually let this order through. A release is not free:
                        # it cancels live downside protection, and the restore
                        # asks for the stop's FULL original size — so if an
                        # in-flight sell still blocks the order afterwards, the
                        # exit fails anyway AND the re-stop is refused, leaving
                        # the position bare with a critical page. For a
                        # whole-position stop that was every I > 0
                        # (adversarial review B HIGH-1). Only claim releasable
                        # when the order fits in what a release could free.
                        blocked_by_pending_sells=(
                            pending_sell
                            if pending_sell > 0 and qty <= held - inflight_sell
                            else None
                        ),
                    )
                checks["sell_position"] = f"pass: closing {qty} of {held} long"
            elif short_allowed and not is_option and intent.order_type != "stop":
                # No held long → this OPENS/extends a short. Allowed only because
                # the toggle is on and we're not live. EQUITY ONLY — options stay
                # long-only (no naked writing), so the toggle must not enable a
                # sell-to-open on an option. Exposure caps (buying power / value /
                # concentration) apply below, same as a buy. STOP orders are
                # excluded: a protective stop is strictly reduce-only, and letting
                # one validate against a flat position would arm a RESTING
                # short-to-open (adversarial review H1, 2026-07-23) — it must
                # fail below and let the stop guard retry/page instead.
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
            pos = None
            for p in positions:
                if str(p.get("symbol", "")).upper() == concentration_symbol:
                    pos = p
                    break
            if pos is not None:
                pos_side = str(pos.get("side", "long")).lower()
                pos_qty = Decimal(str(pos.get("qty", 0)))
                is_short = pos_side == "short" or pos_qty < Decimal("0")
                if is_short:
                    short_qty = abs(pos_qty)
                    if qty <= short_qty:
                        is_buy_to_cover = True
                        checks["sell_position"] = (
                            f"pass: buy-to-cover {qty} of {short_qty} short"
                        )
            if not is_buy_to_cover:
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
            market_price = await _live_market_price(concentration_symbol)
            if market_price is None and (
                intent.time_in_force == "opg"
                or (intent.book_entry and not is_market_open())
                or (intent.order_type == "stop" and intent.side == "sell")
            ):
                # Narrow, documented exception to the RTH-only quote gate: these
                # shapes fill at a FUTURE market event (the opening auction / the
                # open's release of a dead-zone-approved MOO day order / a stop
                # trigger), never at the current instant, so the honest risk
                # basis off-hours is the last session close. Every cap then
                # bounds against that reference; overnight gap risk is inherent
                # to overnight book entries and was accepted with the program
                # (spec §5.5 + entry-type round). The book_entry arm is
                # additionally closed-market-gated so a mid-session quote
                # outage can never re-price a live order off a stale close.
                # All other order shapes keep the fail-closed no-live-quote
                # gate untouched.
                market_price = await _offhours_reference_price(concentration_symbol)
                if market_price is not None:
                    checks["live_quote"] = "pass: off-hours reference close"
            if market_price is None:
                checks["live_quote"] = "fail: no live quote"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="no live quote",
                )
            checks.setdefault("live_quote", "pass")

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

        exposure_increasing = (intent.side == "buy" and not is_buy_to_cover) or is_short_open

        # Founder 07-23 ("let the deepseek through… i said no caps"): book
        # analysts size their own book — the per-trade value and concentration
        # caps do NOT apply to a book account's entry (any style: moo / limit
        # / ext). Scoped to BOTH signals (book_entry AND an account owned by a
        # book_% strategy); fail-soft to capped. Precisely: any HUMAN-APPROVED
        # book-style buy into a book account is uncapped — every path that can
        # set these signals still ends at a pending phone proposal, never a
        # direct submit (adversarial review, 07-23). The walls that
        # remain for books are physical: buying power (below), the book's own
        # cash, the global daily-notional runaway backstop, the daily-loss
        # breaker, and the founder's approval tap.
        book_uncapped = (
            intent.book_entry
            and intent.account_id is not None
            and intent.account_id in _book_account_ids(db_path)
        )

        # HALT (entries_halted) — blocks every exposure-INCREASING order (strategy
        # entries, adds, short-opens) while app-managed reduce-only EXITS keep
        # flowing. A plain sell closing a long (and a buy-to-cover) is
        # exposure-DECREASING, so it skips this gate entirely — that is the point
        # of HALT vs. the kill switch (gate #1 above, a total freeze that also
        # stops exits). Deny-by-default: is_entries_halted() returns True if the
        # flag is unreadable, so ambiguous state halts entries. This is the first
        # exposure-gated check — ordered before the breaker and buying-power.
        if exposure_increasing and is_entries_halted():
            checks["entries_halted"] = "fail: entries halted"
            return RiskCheckResult(
                passed=False,
                checks=checks,
                blocked_reason="entries halted",
                is_short_open=is_short_open,
                risk_notional=risk_notional,
            )
        checks["entries_halted"] = (
            "pass" if exposure_increasing else "pass: reduce-only exempt"
        )

        # Daily-loss breaker — exposure-increasing orders only; closes/exits never
        # blocked (two-stage: soft halts entries, hard arms HALT — not the kill
        # switch — so protective exits keep flowing on a bleeding day).
        # A bad snapshot (portfolio_value <= 0) must never write a 0/negative
        # baseline — that would make `loss` negative all day and the breaker
        # inert. Skip it; the concentration gate below rejects on the same
        # condition anyway.
        if exposure_increasing and portfolio_value <= Decimal("0"):
            checks["daily_loss_breaker"] = "skipped: portfolio value unknown"
        elif exposure_increasing:
            try:
                account_key = _account_key(intent)
                stage = current_stage(account_key, db_path=db_path)
                if stage in ("soft", "hard"):
                    checks["daily_loss_breaker"] = (
                        "fail: daily loss breaker tripped — new entries halted"
                    )
                    return RiskCheckResult(
                        passed=False,
                        checks=checks,
                        blocked_reason="daily loss breaker tripped — new entries halted",
                        is_short_open=is_short_open,
                        risk_notional=risk_notional,
                    )
                breaker_row = get_or_create_baseline(
                    account_key, portfolio_value, db_path=db_path
                )
                baseline_equity = breaker_row["baseline_equity"]
                loss = baseline_equity - portfolio_value
                max_daily_loss = Decimal(str(cfg["max_daily_loss_usd"]))
                if loss >= max_daily_loss * Decimal("1.5"):
                    trip(account_key, "hard", db_path=db_path)
                    apply_settings_patch({"trading": {"entries_halted": True}})
                    # Hard breaker arms HALT (entries only) — NEVER the kill switch.
                    # Protective exits must keep flowing on a bleeding day; the kill
                    # switch (total freeze incl. exits) stays reachable by human
                    # action only. The per-account breaker stage (trip 'hard' above)
                    # also independently blocks exposure-increasing orders.
                    try:
                        await notify(
                            kind="risk_breaker",
                            title="Daily loss breaker — HARD trip",
                            body=(
                                f"Account {account_key}: loss {loss} >= "
                                f"{max_daily_loss * Decimal('1.5')} — entries halted"
                            ),
                            severity="critical",
                            account_id=intent.account_id,
                            push=True,
                            db_path=db_path,
                        )
                    except Exception:
                        logger.exception("daily loss breaker hard-trip notify failed")
                    checks["daily_loss_breaker"] = "fail: hard daily loss limit breached"
                    return RiskCheckResult(
                        passed=False,
                        checks=checks,
                        blocked_reason="hard daily loss limit breached",
                        is_short_open=is_short_open,
                        risk_notional=risk_notional,
                    )
                if loss >= max_daily_loss:
                    trip(account_key, "soft", db_path=db_path)
                    try:
                        await notify(
                            kind="risk_breaker",
                            title="Daily loss breaker — soft trip",
                            body=(
                                f"Account {account_key}: loss {loss} >= "
                                f"{max_daily_loss} — new entries halted"
                            ),
                            severity="critical",
                            account_id=intent.account_id,
                            push=True,
                            db_path=db_path,
                        )
                    except Exception:
                        logger.exception("daily loss breaker soft-trip notify failed")
                    checks["daily_loss_breaker"] = (
                        "fail: daily loss breaker tripped — new entries halted"
                    )
                    return RiskCheckResult(
                        passed=False,
                        checks=checks,
                        blocked_reason="daily loss breaker tripped — new entries halted",
                        is_short_open=is_short_open,
                        risk_notional=risk_notional,
                    )
                checks["daily_loss_breaker"] = "pass"
            except Exception as exc:
                # Fail-open on breaker DB errors — other caps still bound the order.
                # Fail-closed here would halt ALL trading on a transient DB glitch.
                logger.exception("daily loss breaker gate error")
                checks["daily_loss_breaker"] = f"error: {exc} (not enforced)"
                try:
                    await notify(
                        kind="risk_breaker",
                        title="Daily loss breaker error",
                        body=f"Breaker not enforced: {exc}",
                        severity="warning",
                        account_id=intent.account_id,
                        push=True,
                        db_path=db_path,
                    )
                except Exception:
                    logger.exception("daily loss breaker error-path notify failed")
        else:
            checks["daily_loss_breaker"] = "pass: sell" if intent.side == "sell" else "pass: buy-to-cover"

        # ── C2 CONCURRENCY INVARIANT ──────────────────────────────────────────
        # From the first in-flight/daily-notional sum read below through the
        # post-validate notional_usd write in ExecutionService.submit, there must
        # be NO `await` (this tail is synchronous → loop-atomic), and submit holds
        # its per-loop lock across validate + that write. BOTH properties together
        # close the read-sums/write-notional TOCTOU. Adding any await between here
        # and validate's return silently reopens it — don't.
        # Daily notional cap — applies to rebalance orders too (risk gate, not count throttle).
        if exposure_increasing:
            max_daily_notional = Decimal(str(cfg["max_daily_notional_usd"]))
            # In-flight sums exclude THIS order: pending row exists but notional_usd is
            # written only after validate — no self-count double-jeopardy.
            account_today = sum_today_notional(
                account_id=intent.account_id, db_path=db_path
            )
            if account_today + risk_notional > max_daily_notional:
                checks["daily_notional_cap"] = "fail: daily notional cap exceeded"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="daily notional cap exceeded",
                    is_short_open=is_short_open,
                    risk_notional=risk_notional,
                )
            global_today = sum_today_notional(db_path=db_path)
            if global_today + risk_notional > max_daily_notional:
                checks["daily_notional_cap"] = "fail: global daily notional cap exceeded"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="global daily notional cap exceeded",
                    is_short_open=is_short_open,
                    risk_notional=risk_notional,
                )
            checks["daily_notional_cap"] = "pass"
        else:
            checks["daily_notional_cap"] = (
                "pass: sell" if intent.side == "sell" else "pass: buy-to-cover"
            )

        # 6. Buying power (buys + short-opens — both consume buying power)
        if exposure_increasing:
            inflight_account = sum_inflight_notional(
                account_id=intent.account_id, db_path=db_path
            )
            available = buying_power * Decimal("0.95") - inflight_account
            if spend_notional > available:
                if inflight_account > Decimal("0") and spend_notional <= buying_power * Decimal(
                    "0.95"
                ):
                    checks["buying_power"] = (
                        "fail: insufficient buying power (in-flight orders reserve capital)"
                    )
                    return RiskCheckResult(
                        passed=False,
                        checks=checks,
                        blocked_reason=(
                            "insufficient buying power (in-flight orders reserve capital)"
                        ),
                        is_short_open=is_short_open,
                        risk_notional=risk_notional,
                    )
                checks["buying_power"] = "fail: insufficient buying power"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="insufficient buying power",
                    is_short_open=is_short_open,
                    risk_notional=risk_notional,
                )
            checks["buying_power"] = "pass"
        else:
            checks["buying_power"] = (
                "pass: sell" if intent.side == "sell" else "pass: buy-to-cover"
            )

        # 7. Per-order value cap (buys + short-opens — closes must not be blocked)
        if exposure_increasing and book_uncapped:
            checks["order_value_cap"] = "exempt: book account self-sizing"
        elif exposure_increasing:
            max_order_value = Decimal(str(cfg["max_order_value_usd"]))
            if risk_notional > max_order_value:
                checks["order_value_cap"] = "fail: order exceeds max value"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="order exceeds max value",
                    is_short_open=is_short_open,
                    risk_notional=risk_notional,
                )
            checks["order_value_cap"] = "pass"
        else:
            checks["order_value_cap"] = (
                "pass: sell" if intent.side == "sell" else "pass: buy-to-cover"
            )

        # 8. Concentration (buys + short-opens — bound the position size either way)
        if exposure_increasing and book_uncapped:
            checks["concentration"] = "exempt: book account self-sizing"
        elif exposure_increasing:
            if portfolio_value <= Decimal("0"):
                checks["concentration"] = "fail: portfolio value unknown"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="portfolio value unknown",
                    is_short_open=is_short_open,
                    risk_notional=risk_notional,
                )
            existing_mv = Decimal("0")
            for pos in positions:
                if str(pos.get("symbol", "")).upper() == concentration_symbol:
                    # Use absolute exposure so an existing SHORT (negative market
                    # value) still counts toward the concentration ceiling.
                    existing_mv = abs(Decimal(str(pos.get("market_value", 0))))
                    break
            inflight_symbol = sum_inflight_notional(
                symbol=concentration_symbol,
                account_id=intent.account_id,
                db_path=db_path,
            )
            max_position_pct = Decimal(str(cfg["max_position_pct"]))
            concentration_pct = (
                (existing_mv + inflight_symbol + risk_notional) / portfolio_value * Decimal("100")
            )
            if concentration_pct > max_position_pct:
                checks["concentration"] = "fail: concentration limit exceeded"
                return RiskCheckResult(
                    passed=False,
                    checks=checks,
                    blocked_reason="concentration limit exceeded",
                    is_short_open=is_short_open,
                    risk_notional=risk_notional,
                )
            checks["concentration"] = "pass"
        else:
            checks["concentration"] = (
                "pass: sell" if intent.side == "sell" else "pass: buy-to-cover"
            )

        # 9 & 10. Order-COUNT throttles (open-orders cap + daily-order counter).
        # Portfolio rebalances are EXEMPT from these two — and ONLY these two — because a
        # monthly 90+-name rebalance legitimately exceeds them, and they are throttles, not
        # risk gates. Every REAL gate above (kill switch, snapshot freshness, qty bounds,
        # per-order value cap, buying power, concentration) already ran for this order and is
        # NOT bypassed. Reachable as rebalance=True only from the momentum rebalance path.
        if intent.rebalance:
            checks["open_orders_cap"] = "exempt: portfolio rebalance"
            checks["daily_orders"] = "exempt: portfolio rebalance"
            checks["daily_orders_account"] = "exempt: portfolio rebalance"
        elif intent.order_type == "stop":
            # Protective stops are strictly reduce-only (shape gate + sell gate
            # above) and idempotent at the stop guard, and each book position
            # legitimately costs two orders (entry + resting GTC stop) with the
            # stop HOLDING an open-order slot for the position's life. Counting
            # them would let the throttles block a protective order on exactly
            # the day it matters (adversarial review M3, 2026-07-23). Same
            # doctrine as HALT exempting exits: throttles bound exposure-adding
            # runaways; a reduce-only stop adds none.
            checks["open_orders_cap"] = "exempt: protective stop"
            checks["daily_orders"] = "exempt: protective stop"
            checks["daily_orders_account"] = "exempt: protective stop"
        else:
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
            risk_notional=risk_notional,
        )


async def _rest_quote_price(symbol: str) -> float | None:
    """Fresh REST quote for `symbol` when the stream hub doesn't carry it.

    The in-process stream only subscribes symbols a browser is viewing or that have an
    active price alert, so autonomous strategies would otherwise never get a price for an
    un-watched symbol and could never propose. **RTH-only by design:** outside regular
    hours we return None so the "no live quote" gate stays fail-closed exactly as before
    — a provider's "regular last" is the prior session's (stale) close off-hours, and
    letting it through would weaken the chokepoint. Fail-soft: returns None on ANY error,
    never raises into RiskEngine.validate. Any value returned is therefore a fresh RTH price.
    """
    # Function-local imports to avoid any import-time cycle with brokers.factory.
    from brokers.factory import get_market_data_provider
    from trading.market_hours import is_market_open

    # The autonomous proposal paths are already RTH-gated; this also guards the
    # phone-approval path (ExecutionService.submit → validate) from stale off-hours prices.
    if not is_market_open():
        return None

    try:
        provider = get_market_data_provider()
    except Exception:
        return None
    # Prefer the real-time regular-session quote.
    try:
        if hasattr(provider, "aget_quotes"):
            quote = (await provider.aget_quotes([symbol])).get(symbol) or {}
            last = quote.get("regular_last")
            if last is not None and float(last) > 0:
                return float(last)
    except Exception:
        logger.warning("REST quote fallback (quotes) failed for %s", symbol, exc_info=True)
    # Secondary: latest intraday (5-min) bar close — fresh during RTH (the only time we
    # reach here). NOT daily bars: an in-progress daily bar is often absent, so its last
    # close would be the PRIOR day's — stale enough to mis-size on an overnight gap.
    # Guard the at-the-open window the same way: before today's first 5-min bar prints,
    # bars[-1] is still yesterday's close, so only trust a bar dated today (ET).
    try:
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        bars = (
            await provider.aget_stock_bars_batch([symbol], timeframe="5Min", days=1)
        ).get(symbol) or []
        if bars and bars[-1].close is not None and float(bars[-1].close) > 0:
            bar_dt = datetime.fromisoformat(bars[-1].time.replace("Z", "+00:00"))
            if bar_dt.astimezone(et).date() == datetime.now(et).date():
                return float(bars[-1].close)
    except Exception:
        logger.warning("REST quote fallback (bars) failed for %s", symbol, exc_info=True)
    return None


def _book_account_ids(db_path: Path) -> frozenset[str]:
    """Accounts owned by LLM book strategies (`book_%`). Fail-soft to EMPTY —
    on any error the caps apply as normal (deny-by-default)."""
    try:
        from db.base import connect

        with connect(db_path) as conn:
            rows = conn.execute(
                "SELECT account_id FROM strategies "
                "WHERE id LIKE 'book_%' AND account_id IS NOT NULL"
            ).fetchall()
        return frozenset(str(row[0]) for row in rows)
    except Exception:
        logger.warning("book account lookup failed — caps apply", exc_info=True)
        return frozenset()


async def _offhours_reference_price(symbol: str) -> Decimal | None:
    """Last completed daily close — the off-hours risk basis for auction (opg)
    entries and resting protective stops ONLY (see the callsite comment in
    validate). Refuses anything older than 5 calendar days so a stale data
    pipeline fails closed rather than pricing risk off ancient bars. Fail-soft:
    returns None on any error, never raises into validate."""
    from brokers.factory import get_market_data_provider

    try:
        provider = get_market_data_provider()
        bars = (
            await provider.aget_stock_bars_batch([symbol], timeframe="1Day", days=7)
        ).get(symbol) or []
        if not bars or bars[-1].close is None or float(bars[-1].close) <= 0:
            return None
        bar_dt = datetime.fromisoformat(str(bars[-1].time).replace("Z", "+00:00"))
        if bar_dt.tzinfo is None:
            bar_dt = bar_dt.replace(tzinfo=UTC)
        if datetime.now(UTC) - bar_dt > timedelta(days=5):
            return None
        return Decimal(str(bars[-1].close))
    except Exception:
        logger.warning("off-hours reference price failed for %s", symbol, exc_info=True)
        return None


async def _live_market_price(symbol: str) -> Decimal | None:
    quotes = market_hub.quote_snapshot({symbol})
    quote = quotes.get(symbol)
    price_raw = quote.get("price") if quote else None
    if price_raw is None or float(price_raw) <= 0:
        # Hub miss (symbol not currently streamed) — fall back to a REST quote so the
        # agent proposes without a browser open. Fail-soft (returns None on error).
        price_raw = await _rest_quote_price(symbol)
    if price_raw is None or float(price_raw) <= 0:
        return None
    return Decimal(str(price_raw))
