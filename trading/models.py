"""Frozen dataclasses for order execution and risk validation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class RiskRejectedError(Exception):
    """Raised when risk validation or execution pre-checks block an order."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class OrderIntent:
    proposal_id: str
    session_id: str | None
    symbol: str
    side: str  # "buy" | "sell"
    qty: Decimal
    order_type: str  # "market" | "limit" | "stop"
    limit_price: Decimal | None
    broker: str
    # Trigger price for order_type="stop" (protective stop sells placed by the
    # stop guard after a book entry fills). None for market/limit orders.
    stop_price: Decimal | None = None
    # "day" (default), "opg" (opening-auction MOO — book approvals before the
    # ~9:28 ET cutoff), or "gtc" (resting protective stops).
    time_in_force: str = "day"
    # Extended-hours-eligible order (book "ext" entries): Alpaca fills day
    # LIMIT orders in pre/post sessions when this is set. The RiskEngine
    # shape gate enforces limit+day+whole-share+equity for it.
    extended_hours: bool = False
    # Book entry (any style: moo / limit / ext). Distinct from time_in_force:
    # these are approved-later overnight objects, so validate may price a
    # closed-market one off the reference close (adversarial review
    # H1-lifecycle, 2026-07-23), and the book-account cap exemption keys on
    # it. Was `moo_entry` before the entry-type round generalized it.
    book_entry: bool = False
    asset_class: str = "us_equity"
    occ_symbol: str | None = None
    multiplier: int = 1
    account_id: str | None = None
    # Portfolio-rebalance order. Exempts ONLY the order-COUNT throttles in RiskEngine
    # (open-orders cap + daily-order count) — a monthly 90+-name rebalance legitimately
    # exceeds them. Every real risk gate (value cap, buying power, concentration, kill
    # switch, snapshot freshness) still runs. Set ONLY by the momentum rebalance path.
    rebalance: bool = False
    # Permit a FRACTIONAL share qty — WITHOUT the rebalance throttle exemption above.
    # Set by percent-of-equity-sized strategy entries (which deploy an exact dollar
    # fraction, so flooring to whole shares strands cash and drops every name priced
    # above the per-name budget) and by the exit engine when closing a fractional
    # position. Distinct from `rebalance` on purpose: this lifts ONLY the whole-share
    # invariant, never a count throttle. Every real risk gate still runs.
    allow_fractional: bool = False


@dataclass(frozen=True)
class RiskCheckResult:
    passed: bool
    checks: dict[str, str]
    blocked_reason: str | None
    # True when a passing SELL has no held long to close — i.e. it opens/extends a
    # short (only reachable when short selling is enabled + paper/dry_run). The
    # chokepoint uses this to send position_intent="sell_to_open" to the broker.
    is_short_open: bool = False
    # Pre-trade notional used for exposure caps (qty × risk_price × multiplier).
    risk_notional: Decimal | None = None


@dataclass(frozen=True)
class ExecutionResult:
    execution_id: str
    broker_order_id: str | None
    execution_mode: str
    status: str
    dry_run_log: str | None
