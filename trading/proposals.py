"""Phone trade-approval proposal lifecycle — pending, approve, reject."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from db.accounts import account_is_execution_capable
from db.base import DEFAULT_DB_PATH
from db.paper_orders import (
    claim_proposal_for_approval,
    create_pending_proposal,
    get_proposal,
    list_paper_order_proposals,
    transition_proposal_status,
)
from signals.notify import notify
from trading.execution_service import ExecutionService
from trading.models import RiskRejectedError
from trading.order_proposal import (
    build_order_proposal_from_args,
    order_intent_from_fields,
)

logger = logging.getLogger(__name__)

DEFAULT_PROPOSAL_TTL_SECONDS = 300


def _strategy_id_from_source(source: str | None) -> str | None:
    """Parse the strategy id out of a ``strategy:<id>`` / ``exit:<id>`` proposal source."""
    if not source:
        return None
    for prefix in ("strategy:", "exit:"):
        if source.startswith(prefix):
            return source[len(prefix) :].strip() or None
    return None


def _journal_decision(
    proposal_id: str,
    *,
    decision: str,
    decided_by: str | None = None,
    db_path: Path,
) -> None:
    """Fail-soft DECISION-stage journal write. NEVER raises into the approve/reject path."""
    try:
        from db.decision_journal import set_decision

        set_decision(
            proposal_id, decision=decision, decided_by=decided_by, db_path=db_path
        )
    except Exception:
        logger.warning(
            "decision_journal DECISION hook failed for %s (%s)",
            proposal_id,
            decision,
            exc_info=True,
        )
    # Idea-ledger user_action stamp (idea engine Phase 1): every lifecycle
    # transition flows through here, so this is the one stamp site. Same
    # fail-soft law — the ledger never raises into the approve/reject path.
    # The lookup rides idx_idea_source_ref (expression index on the same
    # json_extract pair) so this stays O(log n) as the ledger grows — this
    # runs before submit on the approve path (review MED).
    action = {"approved": "taken", "rejected": "skipped", "expired": "skipped"}.get(
        decision
    )
    if action:
        try:
            from db.base import connect as _ledger_connect
            from db.base import now_iso as _ledger_now

            with _ledger_connect(db_path) as conn:
                conn.execute(
                    "UPDATE idea_outcomes SET user_action = ?, updated_at = ? "
                    "WHERE json_extract(source_ref, '$.kind') = 'proposal' "
                    "AND json_extract(source_ref, '$.id') = ?",
                    (action, _ledger_now(), str(proposal_id)),
                )
                conn.commit()
        except Exception:
            logger.warning(
                "idea-ledger user_action stamp failed for %s (fail-soft)",
                proposal_id,
                exc_info=True,
            )


# The approval tap is THE safety gate — the summary must surface what the
# founder is actually committing to. book_ext's overnight eligibility (a fill
# can land at 3am, unstopped until the next regular open) and GTC standing
# life are material risk, not decoration (entry-type review D1, 2026-07-24).
_ENTRY_STYLE_MARKERS = {
    "moo": "next open auction",
    "book_limit": "GTC, rests until filled",
    "book_ext": "GTC + overnight/ext-hours",
}


def _proposal_summary(intent_fields: dict[str, Any]) -> str:
    side = str(intent_fields["side"]).upper()
    qty = intent_fields["qty"]
    symbol = intent_fields["symbol"]
    order_type = intent_fields["order_type"]
    if order_type == "limit" and intent_fields.get("limit_price") is not None:
        base = f"{side} {qty} {symbol} @ limit {intent_fields['limit_price']}"
    else:
        base = f"{side} {qty} {symbol} @ {order_type}"
    marker = _ENTRY_STYLE_MARKERS.get(str(intent_fields.get("entry_style") or ""))
    return f"{base} · {marker}" if marker else base


def _parse_expires_at(expires_at: str | None) -> datetime | None:
    if not expires_at:
        return None
    try:
        return datetime.fromisoformat(expires_at)
    except ValueError:
        return None


def _qty_str(qty: Any) -> str:
    """Wire-format qty: integral values without a trailing '.0'."""
    try:
        f = float(qty)
        return str(int(f)) if f.is_integer() else str(qty)
    except (TypeError, ValueError):
        return str(qty or "")


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


async def _try_end_proposal_live_activity(
    proposal_id: str,
    *,
    status: str,
    status_reason: str | None = None,
    proposal: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    try:
        from signals.push import end_proposal_live_activity

        await end_proposal_live_activity(
            proposal_id,
            status=status,
            status_reason=status_reason,
            proposal=proposal,
            db_path=db_path,
        )
    except Exception:
        logger.exception("Failed to end live activity for proposal %s", proposal_id)


async def approve_proposal(
    proposal_id: str,
    *,
    expected_hash: str | None = None,
    decided_by: str = "phone",
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
        _journal_decision(proposal_id, decision="expired", decided_by="system", db_path=db_path)
        await _try_end_proposal_live_activity(
            proposal_id, status="expired", proposal=row, db_path=db_path
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
        _journal_decision(proposal_id, decision="expired", decided_by="system", db_path=db_path)
        await _try_end_proposal_live_activity(
            proposal_id, status="expired", proposal=row, db_path=db_path
        )
        return {"status": "expired"}
    if claim == "not_found":
        return {"status": "not_found"}
    if claim != "claimed":
        return {"status": "already_decided"}

    # The user approved (atomic claim won). Record the DECISION before we touch the
    # chokepoint — fail-soft, so a journal hiccup can never block the order.
    _journal_decision(proposal_id, decision="approved", decided_by=decided_by, db_path=db_path)

    stored = row.get("proposal") or {}
    intent = order_intent_from_fields(
        stored,
        proposal_id=proposal_id,
        session_id=row.get("session_id"),
        db_path=db_path,
    )

    if intent.account_id is not None and not account_is_execution_capable(
        intent.account_id, db_path=db_path
    ):
        transition_proposal_status(
            proposal_id,
            expect="approving",
            to="blocked",
            last_error="account not execution-capable",
            db_path=db_path,
        )
        await _try_end_proposal_live_activity(
            proposal_id,
            status="failed",
            status_reason="account not execution-capable",
            proposal=row,
            db_path=db_path,
        )
        return {"status": "blocked", "reason": "account not execution-capable"}

    # In-flight: the proposal is approving and about to be placed. Surface the
    # "Approved" state on the Live Activity (an UPDATE, not an END) so the widget
    # shows processing before the terminal executed/failed arrives.
    await _try_end_proposal_live_activity(
        proposal_id, status="approved", proposal=row, db_path=db_path
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
        await _try_end_proposal_live_activity(
            proposal_id,
            status="failed",
            status_reason=exc.reason,
            proposal=row,
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
        await _try_end_proposal_live_activity(
            proposal_id,
            status="failed",
            status_reason=str(exc),
            proposal=row,
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
    await _try_end_proposal_live_activity(
        proposal_id, status="executed", proposal=row, db_path=db_path
    )
    return out


async def sweep_expired_proposals(*, db_path: Path = DEFAULT_DB_PATH) -> int:
    """Proactively expire unanswered pending_phone proposals past TTL."""
    expired_count = 0
    try:
        rows = list_paper_order_proposals(
            status="pending_phone", limit=200, db_path=db_path
        )
    except Exception:
        logger.exception("Failed to list pending proposals for expiry sweep")
        return 0

    for row in rows:
        try:
            if not _is_expired(row):
                continue
            proposal_id = str(row["id"])
            if not transition_proposal_status(
                proposal_id,
                expect="pending_phone",
                to="expired",
                db_path=db_path,
            ):
                continue

            expired_count += 1
            _journal_decision(
                proposal_id, decision="expired", decided_by="system", db_path=db_path
            )
            await _try_end_proposal_live_activity(
                proposal_id, status="expired", proposal=row, db_path=db_path
            )

            summary = str(row.get("summary") or "")
            expired_at_iso = row.get("expires_at") or ""
            try:
                await notify(
                    kind="proposal_missed",
                    title="Missed trade proposal",
                    body=f"{summary} expired unanswered",
                    severity="warning",
                    ref_type="proposal",
                    ref_id=proposal_id,
                    data={
                        "type": "proposal_missed",
                        "proposal_id": proposal_id,
                        "symbol": str(row.get("symbol", "")),
                        "side": str(row.get("side", "")),
                        # Contract shows integral qty without ".0" (REAL column
                        # would render "2.0"); mobile parses numerically either way.
                        "qty": _qty_str(row.get("qty")),
                        "summary": summary,
                        "expired_at": expired_at_iso,
                    },
                    push=True,
                    db_path=db_path,
                )
            except Exception:
                logger.exception(
                    "Failed to send proposal_missed push for %s", proposal_id
                )
        except Exception:
            logger.exception(
                "Failed to expire proposal %s in sweep", row.get("id")
            )

    return expired_count


_PUSH_RETRY_MAX_ATTEMPTS = 3
# 300s post-expiry margin assumes worst-case approve->submit latency well under
# it (validate's broker HTTP calls are bounded ~30s timeouts; full flow ~60-90s
# worst case). If broker clients ever gain retry/backoff, re-verify this bound.
_APPROVAL_RECONCILE_MAX_AGE_SECONDS = 300

# canceled/expired count as SUCCESS for proposal resolution: the order verifiably
# reached the broker (same convention as bare `submitted` — the proposal's job
# ended at hand-off; fill outcome is fill_report's concern). Leaving them out
# livelocks the reconciler: fill_tracker can flip submitted->canceled before the
# reconciler's first eligible look, and the in-flight skip would then hold the
# proposal in `approving` forever (adversarial-review finding, 2026-07-03).
_EXECUTION_SUCCESS_STATUSES = frozenset(
    {"submitted", "filled", "partially_filled", "dry_run", "canceled", "expired"}
)
_EXECUTION_FAILURE_STATUSES = frozenset({"rejected", "error"})

# Cross-pass dedup for delivery alarms: a zero-delivery proposal must alarm ONCE,
# not on every 60s sweep until it expires. In-memory (resets on restart → at most
# one duplicate alarm per restart) — proportionate to the LOW-severity finding.
_delivery_alarmed: set[str] = set()


async def retry_undelivered_pushes(*, db_path: Path = DEFAULT_DB_PATH) -> int:
    """Re-send failed proposal pushes for unexpired pending_phone proposals."""
    retried = 0
    alarmed: set[str] = set()
    try:
        rows = list_paper_order_proposals(
            status="pending_phone", limit=200, db_path=db_path
        )
    except Exception:
        logger.exception("Failed to list pending proposals for push retry")
        return 0

    from db.push_deliveries import (
        delivery_channel,
        retries_exhausted_with_no_sent,
        undelivered_tokens,
    )
    from signals.push import _fire_delivery_alarm, send_proposal_push_to_token

    for row in rows:
        try:
            if _is_expired(row):
                continue
            proposal_id = str(row["id"])
            tokens = undelivered_tokens(
                proposal_id, max_attempts=_PUSH_RETRY_MAX_ATTEMPTS, db_path=db_path
            )
            for token in tokens:
                channel = delivery_channel(proposal_id, token, db_path=db_path)
                await send_proposal_push_to_token(
                    row, token, channel=channel, db_path=db_path
                )
                retried += 1

            if (
                proposal_id not in alarmed
                and proposal_id not in _delivery_alarmed
                and retries_exhausted_with_no_sent(
                    proposal_id,
                    max_attempts=_PUSH_RETRY_MAX_ATTEMPTS,
                    db_path=db_path,
                )
            ):
                alarmed.add(proposal_id)
                _delivery_alarmed.add(proposal_id)
                await _fire_delivery_alarm(
                    row,
                    reason="all push retries exhausted with zero deliveries",
                    db_path=db_path,
                )
        except Exception:
            logger.exception("Failed to retry push for proposal %s", row.get("id"))

    return retried


async def reconcile_orphaned_approvals(
    *,
    max_age_seconds: int = _APPROVAL_RECONCILE_MAX_AGE_SECONDS,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Resolve stale approving proposals via execution lookup.

    Age is measured from created_at — claim_proposal_for_approval does not stamp
    a separate column.
    """
    reconciled = 0
    try:
        rows = list_paper_order_proposals(status="approving", limit=200, db_path=db_path)
    except Exception:
        logger.exception("Failed to list approving proposals for reconciliation")
        return 0

    from db.order_executions import get_execution_by_proposal

    now = datetime.now(UTC)
    for row in rows:
        try:
            # Defensive only — status='approving' implies decided_at IS NULL (the
            # terminal transition sets both atomically). The REAL concurrency
            # guard is the expect="approving" CAS on the transition below.
            if row.get("decided_at") is not None:
                continue
            # Eligibility is keyed to EXPIRY + margin, not created_at: a claim can
            # only happen while unexpired, so `now >= expires_at + max_age` proves
            # the claim is at least max_age old and no live approve flow can still
            # be inside its seconds-long claim->submit window. (created_at + max_age
            # alone is unsafe: a claim at second 299 of a 300s TTL would be
            # reconciler-eligible ~1s after claiming.) Missing/malformed expires_at
            # falls back to created_at — such rows can never be claimed anyway
            # (_is_expired fail-safes them), so no live flow exists to race.
            anchor = _parse_expires_at(row.get("expires_at"))
            if anchor is None:
                created_raw = row.get("created_at")
                if not created_raw:
                    continue
                try:
                    anchor = datetime.fromisoformat(str(created_raw))
                except ValueError:
                    continue
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=UTC)
            if (now - anchor).total_seconds() < max_age_seconds:
                continue

            proposal_id = str(row["id"])
            execution = get_execution_by_proposal(proposal_id, db_path=db_path)

            # In-flight execution (pending = audit row written, validate running;
            # or any unrecognized status): do NOT resolve — leave for a later pass
            # once the chokepoint reaches a terminal state. Resolving here could
            # mark a proposal failed while its order still places.
            if execution is not None and (
                execution["status"]
                not in (_EXECUTION_SUCCESS_STATUSES | _EXECUTION_FAILURE_STATUSES)
            ):
                logger.debug(
                    "reconcile: proposal %s execution %s in-flight (%s) — skipping",
                    proposal_id,
                    execution["id"],
                    execution["status"],
                )
                continue

            if execution is not None and execution["status"] in _EXECUTION_SUCCESS_STATUSES:
                terminal_status = "executed"
                extra = {"execution_id": execution["id"]}
                la_status = "executed"
                la_reason = None
                notify_body = (
                    f"Reconciled orphaned approval for {row.get('summary') or proposal_id} "
                    f"→ executed ({execution['id']})"
                )
            elif execution is not None and execution["status"] in _EXECUTION_FAILURE_STATUSES:
                terminal_status = "failed"
                last_error = (
                    str(execution.get("status") or "approval crashed — broker rejected")
                )
                extra = {"last_error": last_error}
                la_status = "failed"
                la_reason = last_error
                notify_body = (
                    f"Reconciled orphaned approval for {row.get('summary') or proposal_id} "
                    f"→ failed ({last_error})"
                )
            else:
                terminal_status = "failed"
                last_error = (
                    "orphaned approving — no execution found; approval must be re-issued"
                )
                extra = {"last_error": last_error}
                la_status = "failed"
                la_reason = last_error
                notify_body = (
                    f"Reconciled orphaned approval for {row.get('summary') or proposal_id} "
                    f"→ failed (no execution)"
                )

            if not transition_proposal_status(
                proposal_id,
                expect="approving",
                to=terminal_status,
                db_path=db_path,
                **extra,
            ):
                continue

            reconciled += 1
            await _try_end_proposal_live_activity(
                proposal_id,
                status=la_status,
                status_reason=la_reason,
                proposal=row,
                db_path=db_path,
            )
            try:
                await notify(
                    kind="proposal_reconciled",
                    title="Proposal approval reconciled",
                    body=notify_body,
                    severity="warning",
                    ref_type="proposal",
                    ref_id=proposal_id,
                    data={
                        "type": "proposal_reconciled",
                        "proposal_id": proposal_id,
                        "status": terminal_status,
                    },
                    push=False,
                    db_path=db_path,
                )
            except Exception:
                logger.exception(
                    "Failed to notify proposal_reconciled for %s", proposal_id
                )
        except Exception:
            logger.exception(
                "Failed to reconcile approving proposal %s", row.get("id")
            )

    return reconciled


async def reject_proposal(
    proposal_id: str,
    *,
    decided_by: str = "phone",
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

    # Journal the decline (outcome='never_filled' set inside) — the richest learning
    # signal per the founder decision. Fail-soft.
    _journal_decision(proposal_id, decision="rejected", decided_by=decided_by, db_path=db_path)

    await _try_end_proposal_live_activity(
        proposal_id, status="rejected", proposal=row, db_path=db_path
    )
    return {"status": "rejected"}


async def create_phone_proposal(
    args: dict[str, Any],
    *,
    session_id: str | None = None,
    source: str | None = None,
    rule_id: str | None = None,
    score: bool = True,
    thesis: str | None = None,
    entry_ref_price: str | None = None,
    ttl_seconds: int = DEFAULT_PROPOSAL_TTL_SECONDS,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    # Book entry styles (moo/GTC-limit/ext) come with cap exemptions and the
    # stop guard — only the nightly book driver may mint them. Chat surfaces
    # (web + mobile) land here too and must not create uncapped standing
    # orders (entry-type review D5, 2026-07-24).
    entry_style = str(args.get("entry_style") or "immediate").lower()
    if entry_style != "immediate" and not str(source or "").startswith("book_"):
        return {"error": "entry_style is reserved for the nightly book driver"}

    built = build_order_proposal_from_args(args, db_path=db_path)
    if "error" in built:
        return built

    session_id_str = session_id or built.get("session_id")
    intent_fields = built["intent_fields"]
    summary = _proposal_summary(intent_fields)

    score_result = None
    if score:
        # A validated strategy's backtested stats are the HONEST conviction; prefer them
        # over the generic per-symbol scorer (calibrated but edgeless). Display/context
        # only — not read by RiskEngine or the execution chokepoint.
        if rule_id:
            try:
                from signals.strategy_stats import strategy_conviction, strategy_plan

                score_result = strategy_conviction(rule_id)
                if score_result is not None:
                    plan = strategy_plan(rule_id, db_path=db_path)
                    if plan is not None:
                        score_result["plan"] = plan
            except Exception:
                logger.exception("Failed to load strategy stats for %s", rule_id)
        if score_result is None:
            try:
                from signals.decision_scorer import score_symbol

                score_result = score_symbol(
                    intent_fields["symbol"],
                    side=intent_fields["side"],
                    db_path=db_path,
                )
            except Exception:
                logger.exception(
                    "Failed to score proposal for %s", intent_fields.get("symbol")
                )

    account_id = intent_fields.get("account_id")
    row = create_pending_proposal(
        proposal=built["proposal"],
        intent_fields=intent_fields,
        session_id=session_id_str,
        ttl_seconds=ttl_seconds,
        summary=summary,
        source=source,
        rule_id=rule_id,
        score=score_result,
        thesis=thesis,
        account_id=account_id,
        db_path=db_path,
    )

    # Decision Journal CONTEXT (write-once, immutable) — the reasoning captured BEFORE the
    # outcome is known. Fail-soft: a journal write must never break proposal creation.
    try:
        from db.decision_journal import record_decision

        plan = score_result.get("plan") if isinstance(score_result, dict) else None
        limit_raw = intent_fields.get("limit_price")
        mult_raw = intent_fields.get("multiplier")
        record_decision(
            proposal_id=str(row["id"]),
            symbol=str(intent_fields["symbol"]),
            side=str(intent_fields["side"]),
            qty=str(intent_fields["qty"]),
            order_type=str(intent_fields["order_type"]),
            limit_price=str(limit_raw) if limit_raw is not None else None,
            source=source,
            strategy_id=_strategy_id_from_source(source),
            rule_id=rule_id,
            entry_ref_price=str(entry_ref_price) if entry_ref_price is not None else None,
            thesis=thesis,
            score_json=json.dumps(score_result) if score_result is not None else None,
            plan_json=json.dumps(plan) if plan is not None else None,
            expires_at=row.get("expires_at"),
            # Options economics: us_option carries multiplier=100 so realized P&L is correct.
            asset_class=intent_fields.get("asset_class"),
            multiplier=str(mult_raw) if mult_raw is not None else None,
            created_at=row.get("created_at"),
            db_path=db_path,
        )
    except Exception:
        logger.warning(
            "decision_journal CONTEXT hook failed for %s", row.get("id"), exc_info=True
        )

    side = str(intent_fields["side"]).upper()
    qty = intent_fields["qty"]
    symbol = intent_fields["symbol"]
    try:
        await notify(
            kind="proposal",
            title=f"Trade proposal: {side} {qty} {symbol}",
            body=summary,
            severity="info",
            account_id=account_id,
            ref_type="proposal",
            ref_id=str(row["id"]),
            action_url=f"/approvals/{row['id']}",
            data={
                "proposal_id": row["id"],
                "summary": summary,
                "expires_at": row["expires_at"],
            },
            push=False,
            db_path=db_path,
        )
    except Exception:
        logger.exception("Failed to broadcast phone proposal alert")

    try:
        from signals.push import send_proposal_push

        await send_proposal_push(row, db_path=db_path)
    except Exception:
        logger.exception("Failed to send proposal push")

    try:
        from signals.push import start_proposal_live_activity

        await start_proposal_live_activity(row, db_path=db_path)
    except Exception:
        logger.exception("Failed to start live activity for proposal %s", row.get("id"))

    return row
