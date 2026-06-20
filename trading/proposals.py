"""Phone trade-approval proposal lifecycle — pending, approve, reject."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brokers.market_stream import market_hub
from db.base import DEFAULT_DB_PATH
from db.paper_orders import (
    claim_proposal_for_approval,
    create_pending_proposal,
    get_proposal,
    transition_proposal_status,
)
from trading.execution_service import ExecutionService
from trading.models import RiskRejectedError
from trading.order_proposal import (
    build_order_proposal_from_args,
    order_intent_from_fields,
)

logger = logging.getLogger(__name__)

DEFAULT_PROPOSAL_TTL_SECONDS = 300


def _proposal_summary(intent_fields: dict[str, Any]) -> str:
    side = str(intent_fields["side"]).upper()
    qty = intent_fields["qty"]
    symbol = intent_fields["symbol"]
    order_type = intent_fields["order_type"]
    if order_type == "limit" and intent_fields.get("limit_price") is not None:
        return f"{side} {qty} {symbol} @ limit {intent_fields['limit_price']}"
    return f"{side} {qty} {symbol} @ {order_type}"


def _parse_expires_at(expires_at: str | None) -> datetime | None:
    if not expires_at:
        return None
    try:
        return datetime.fromisoformat(expires_at)
    except ValueError:
        return None


def _is_expired(row: dict[str, Any]) -> bool:
    raw = row.get("expires_at")
    if not raw:
        # No TTL recorded → fail safe (treat as expired, never execute).
        return True
    expires = _parse_expires_at(raw)
    if expires is None:
        # Malformed TTL → fail safe.
        return True
    now = datetime.now(UTC)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return now >= expires


async def approve_proposal(
    proposal_id: str,
    *,
    expected_hash: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    row = get_proposal(proposal_id, db_path=db_path)
    if row is None:
        return {"status": "not_found"}

    status = row["status"]
    if status != "pending_phone":
        return {"status": "already_decided", "current": status}

    if _is_expired(row):
        transition_proposal_status(
            proposal_id,
            expect="pending_phone",
            to="expired",
            db_path=db_path,
        )
        return {"status": "expired"}

    if expected_hash is not None and expected_hash != row.get("payload_hash"):
        return {"status": "hash_mismatch"}

    # Atomic claim: status guard + expiry in one UPDATE. Only one caller can move
    # pending_phone -> approving, and only while unexpired — so a double/concurrent
    # approve reaches ExecutionService.submit at most once.
    claim = claim_proposal_for_approval(proposal_id, db_path=db_path)
    if claim == "expired":
        transition_proposal_status(
            proposal_id, expect="pending_phone", to="expired", db_path=db_path
        )
        return {"status": "expired"}
    if claim == "not_found":
        return {"status": "not_found"}
    if claim != "claimed":
        return {"status": "already_decided"}

    stored = row.get("proposal") or {}
    intent = order_intent_from_fields(
        stored,
        proposal_id=proposal_id,
        session_id=row.get("session_id"),
    )

    try:
        result = await ExecutionService.submit(intent, db_path=db_path)
    except RiskRejectedError as exc:
        transition_proposal_status(
            proposal_id,
            expect="approving",
            to="blocked",
            last_error=exc.reason,
            db_path=db_path,
        )
        return {"status": "blocked", "reason": exc.reason}
    except Exception as exc:
        transition_proposal_status(
            proposal_id,
            expect="approving",
            to="failed",
            last_error=str(exc),
            db_path=db_path,
        )
        return {"status": "failed", "reason": str(exc)}

    # The order is PLACED. Persisting the terminal status must never flip a real
    # execution into a user-visible failure — if the write fails, log loudly and
    # still report executed (the order_executions row is the audit source of truth).
    out: dict[str, Any] = {
        "status": "executed",
        "execution_id": result.execution_id,
        "execution_mode": result.execution_mode,
    }
    try:
        persisted = transition_proposal_status(
            proposal_id,
            expect="approving",
            to="executed",
            execution_id=result.execution_id,
            db_path=db_path,
        )
    except Exception:
        logger.exception(
            "Proposal %s executed (exec %s) but status persist raised",
            proposal_id,
            result.execution_id,
        )
        persisted = False
    if not persisted:
        out["warning"] = "state_persist_failed"
        logger.error(
            "Proposal %s executed (exec %s) but status not persisted",
            proposal_id,
            result.execution_id,
        )
    return out


async def reject_proposal(
    proposal_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    row = get_proposal(proposal_id, db_path=db_path)
    if row is None:
        return {"status": "not_found"}

    if row["status"] != "pending_phone":
        return {"status": "already_decided", "current": row["status"]}

    if not transition_proposal_status(
        proposal_id,
        expect="pending_phone",
        to="rejected",
        db_path=db_path,
    ):
        return {"status": "already_decided"}

    return {"status": "rejected"}


async def create_phone_proposal(
    args: dict[str, Any],
    *,
    session_id: str | None = None,
    ttl_seconds: int = DEFAULT_PROPOSAL_TTL_SECONDS,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    built = build_order_proposal_from_args(args, db_path=db_path)
    if "error" in built:
        return built

    session_id_str = session_id or built.get("session_id")
    intent_fields = built["intent_fields"]
    summary = _proposal_summary(intent_fields)

    row = create_pending_proposal(
        proposal=built["proposal"],
        intent_fields=intent_fields,
        session_id=session_id_str,
        ttl_seconds=ttl_seconds,
        summary=summary,
        db_path=db_path,
    )

    try:
        await market_hub.broadcast_alert(
            {
                "type": "alert",
                "source": "proposal",
                "rule_name": "Order proposal",
                "proposal_id": row["id"],
                "action": {
                    "type": "order_proposal",
                    "proposal_id": row["id"],
                    "summary": summary,
                    "expires_at": row["expires_at"],
                },
            }
        )
    except Exception:
        logger.exception("Failed to broadcast phone proposal alert")

    return row
