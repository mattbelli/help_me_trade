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
    order_type: str  # "market" | "limit"
    limit_price: Decimal | None
    broker: str


@dataclass(frozen=True)
class RiskCheckResult:
    passed: bool
    checks: dict[str, str]
    blocked_reason: str | None


@dataclass(frozen=True)
class ExecutionResult:
    execution_id: str
    broker_order_id: str | None
    execution_mode: str
    status: str
    dry_run_log: str | None
