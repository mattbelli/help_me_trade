"""Bridge fills → the managed-position ledger (strategy attribution at fill time).

When a strategy-sourced BUY fills, record an OPEN managed position tagged with the
owning strategy + a snapshot of its exit_mode/params, so the exit engine knows
whose rules to apply. Bookkeeping only — it never touches the broker or the
execution record, and it never blocks or rolls back the fill (the fill already
happened).

SAFETY (safety-M1): a strategy BUY that fills but cannot be attributed here would
otherwise become an ORPHAN — real (paper) shares held with NO stop-loss / exit
rules, because the exit engine only walks ``managed_positions``. So the write is
retried on transient DB contention, and a persistent failure is NOT swallowed: it
is durably quarantined (``db.fill_attribution``) and the founder is paged
(``flush_fill_attribution_orphans`` → critical notification). Deny-by-default:
when we cannot prove a fill is unmanaged-by-design, we surface it loudly.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.fill_attribution import record_orphan
from db.managed_positions import record_open_position
from db.paper_orders import get_proposal
from db.strategies import get_strategy

logger = logging.getLogger(__name__)

_STRATEGY_SOURCE_PREFIX = "strategy:"

# Bounded retry for the managed-position write on transient DB contention
# (SQLite "database is locked"); a persistent failure quarantines instead.
_WRITE_RETRIES = 3
_WRITE_RETRY_SLEEP_S = 0.05

# How far back the unmanaged-fill sweep looks (by execution created_at). 30 days
# covers any realistic outage (adversarial F2: 7 days missed an order created just
# before a longer-than-a-week downtime) while keeping the per-cycle scan cheap at
# our volume. KNOWN RESIDUAL horizon: a fill stranded across an outage LONGER than
# this is permanently outside the sweep's window.
_SWEEP_LOOKBACK_DAYS = 30


def _strategy_id_from_source(source: str | None) -> str | None:
    if not source or not source.startswith(_STRATEGY_SOURCE_PREFIX):
        return None
    strategy_id = source[len(_STRATEGY_SOURCE_PREFIX) :].strip()
    return strategy_id or None


def _resolve_strategy_id(proposal_id: str, *, db_path: Path) -> str | None:
    """Map a fill's proposal_id to the owning strategy id, or None if not
    strategy-attributable (a non-strategy source, or a missing proposal row).

    Single source of truth for "which strategy owns this fill", shared by
    ``record_strategy_fill`` (attribution at fill time) and the unmanaged-fill sweep
    (backstop) so the two can never disagree on what counts as strategy-owned."""
    if proposal_id.startswith("auto_entry|"):
        parts = proposal_id.split("|")
        return parts[1] if len(parts) >= 2 else None
    proposal = get_proposal(proposal_id, db_path=db_path)
    if proposal is None:
        return None
    return _strategy_id_from_source(proposal.get("source"))


def _journal_entry_fill(
    proposal_id: str,
    execution_row: dict[str, Any],
    *,
    filled_qty: float | None,
    filled_avg_price: float | None,
    db_path: Path,
) -> None:
    """Fail-soft entry-OUTCOME journal link. NEVER raises into fill reconciliation.

    No-op when the proposal has no journal row (e.g. synthetic auto_entry ids, which are
    never journaled at propose)."""
    try:
        from db.decision_journal import set_entry_fill

        set_entry_fill(
            proposal_id,
            entry_execution_id=str(execution_row.get("id") or "") or None,
            entry_fill_price=(
                str(filled_avg_price) if filled_avg_price is not None else None
            ),
            qty_filled=str(filled_qty) if filled_qty is not None else None,
            db_path=db_path,
        )
    except Exception:
        logger.warning(
            "decision_journal entry-fill hook failed for %s", proposal_id, exc_info=True
        )


def _quarantine_orphan_fill(
    *,
    execution_id: str | None,
    reason: str,
    symbol: str | None = None,
    account_id: str | None = None,
    strategy_id: str | None = None,
    proposal_id: str | None = None,
    qty: str | None = None,
    detail: str | None = None,
    db_path: Path,
) -> None:
    """Loudly record an unattributable strategy fill: error log + durable orphan row.

    Returns None (the caller's contract) — but the orphan row is what
    ``flush_fill_attribution_orphans`` later turns into the founder's critical page.
    """
    logger.error(
        "UNMANAGED FILL quarantined — execution=%s symbol=%s strategy=%s reason=%s "
        "detail=%s: a strategy BUY filled but attribution failed, so NO exit "
        "management is attached until this is repaired",
        execution_id, symbol, strategy_id, reason, detail,
    )
    record_orphan(
        execution_id=execution_id,
        reason=reason,
        symbol=symbol,
        account_id=account_id,
        strategy_id=strategy_id,
        proposal_id=proposal_id,
        qty=qty,
        detail=detail,
        db_path=db_path,
    )


def record_strategy_fill(
    execution_row: dict[str, Any],
    *,
    filled_qty: float | None,
    status: str,
    filled_avg_price: float | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """If a strategy BUY filled, open a managed-position row. Returns its id or None.

    Never raises into fill reconciliation and never touches the broker. But once a
    fill is established as a strategy-attributable BUY, a failure to open its managed
    row is NOT swallowed: it is retried (transient) then quarantined + paged, so the
    position can never be silently left unmanaged. Non-managed fills (sells,
    non-strategy sources, non-terminal status) still return None quietly.
    """
    execution_id = str(execution_row.get("id") or "") or None
    symbol = str(execution_row.get("symbol", "")).strip().upper() or None
    raw_proposal_id = execution_row.get("proposal_id")
    qty = str(filled_qty) if filled_qty is not None else None
    try:
        if status not in {"filled", "partially_filled"}:
            return None
        side = str(execution_row.get("side", "")).strip().lower()
        if side != "buy":
            return None  # exits/sells are never managed entries

        if not raw_proposal_id:
            return None
        proposal_id = str(raw_proposal_id)

        # Decision Journal entry OUTCOME — link the fill to the proposal's journal row.
        # Fail-soft + wrapped so it can never abort managed-position recording below.
        _journal_entry_fill(
            proposal_id,
            execution_row,
            filled_qty=filled_qty,
            filled_avg_price=filled_avg_price,
            db_path=db_path,
        )

        strategy_id = _resolve_strategy_id(proposal_id, db_path=db_path)
        if strategy_id is None:
            return None
        # From here on this IS a strategy-attributable BUY fill. Any failure to open
        # its managed row is an ORPHAN → quarantine + page, never a silent None.

        strategy = get_strategy(strategy_id, db_path=db_path)
        if strategy is None:
            _quarantine_orphan_fill(
                execution_id=execution_id,
                reason="unknown_strategy",
                symbol=symbol,
                strategy_id=strategy_id,
                proposal_id=proposal_id,
                qty=qty,
                detail=f"filled strategy order references unknown strategy {strategy_id!r}",
                db_path=db_path,
            )
            return None

        # The shares live in the EXECUTION's account — that's authoritative for
        # the ledger (the exit engine must act where the position actually is).
        # Fall back to the strategy's bound account only if the execution didn't
        # record one. Surface a divergence rather than silently masking it.
        exec_account = execution_row.get("account_id")
        strat_account = strategy.get("account_id")
        if exec_account and strat_account and exec_account != strat_account:
            logger.warning(
                "Fill account %s != strategy %s bound account %s — recording under "
                "the execution account (where the shares are)",
                exec_account,
                strategy_id,
                strat_account,
            )
        account_id = exec_account or strat_account
        if not account_id:
            _quarantine_orphan_fill(
                execution_id=execution_id,
                reason="no_account",
                symbol=symbol,
                strategy_id=strategy_id,
                proposal_id=proposal_id,
                qty=qty,
                detail="no account_id on execution or strategy",
                db_path=db_path,
            )
            return None

        if not symbol:
            _quarantine_orphan_fill(
                execution_id=execution_id,
                reason="no_symbol",
                account_id=str(account_id),
                strategy_id=strategy_id,
                proposal_id=proposal_id,
                qty=qty,
                detail="execution row carried no symbol",
                db_path=db_path,
            )
            return None

        # Transient-safe write; a persistent failure quarantines rather than orphaning.
        last_exc: Exception | None = None
        for attempt in range(_WRITE_RETRIES):
            try:
                return record_open_position(
                    account_id=str(account_id),
                    symbol=symbol,
                    strategy_id=strategy_id,
                    qty=qty,
                    exit_mode=str(strategy.get("exit_mode") or "propose"),
                    params=dict(strategy.get("params") or {}),
                    entry_execution_id=execution_id,
                    db_path=db_path,
                )
            except sqlite3.OperationalError as exc:
                last_exc = exc
                if attempt < _WRITE_RETRIES - 1:
                    time.sleep(_WRITE_RETRY_SLEEP_S)
                    continue
            except Exception as exc:
                last_exc = exc
                break  # deterministic failure — retrying won't help
        _quarantine_orphan_fill(
            execution_id=execution_id,
            reason="write_failed",
            symbol=symbol,
            account_id=str(account_id),
            strategy_id=strategy_id,
            proposal_id=proposal_id,
            qty=qty,
            detail=f"record_open_position failed: {last_exc!r}",
            db_path=db_path,
        )
        return None
    except Exception as exc:
        # Unexpected failure while resolving attribution — we cannot prove this fill
        # is NOT a managed strategy entry, so surface loudly rather than assume-managed.
        logger.exception("Failed to record managed position from fill")
        _quarantine_orphan_fill(
            execution_id=execution_id,
            reason="resolution_error",
            symbol=symbol,
            proposal_id=str(raw_proposal_id) if raw_proposal_id else None,
            qty=qty,
            detail=f"attribution resolution error: {exc!r}",
            db_path=db_path,
        )
        return None


async def flush_fill_attribution_orphans(*, db_path: Path = DEFAULT_DB_PATH) -> int:
    """Page the founder for any unmanaged-fill orphans not yet notified.

    Called from the fill reconcile loop (the existing hook — no new background
    loop). Durable: an orphan quarantined before a crash is picked up on the next
    poll. Returns the number of critical notifications sent this pass.
    """
    from db.fill_attribution import list_open_orphans, mark_orphan_notified
    from signals.notify import notify

    paged = 0
    for orphan in list_open_orphans(db_path=db_path):
        symbol = orphan.get("symbol") or "?"
        execution_id = orphan.get("execution_id")
        sent = await notify(
            kind="attribution_orphan",
            title=f"Unmanaged fill — {symbol}",
            body=(
                f"A strategy BUY filled ({symbol}) but could not be attributed to a "
                "managed position — NO stop-loss / exit rules are attached. This needs "
                f"manual repair (reason: {orphan.get('reason')})."
            ),
            severity="critical",
            account_id=orphan.get("account_id"),
            strategy_id=orphan.get("strategy_id"),
            ref_type="execution",
            ref_id=execution_id,
            action_url=f"/executions/{execution_id}" if execution_id else None,
            data={
                "type": "attribution_orphan",
                "orphan_id": orphan.get("id"),
                "execution_id": execution_id,
                "symbol": symbol,
                "strategy_id": orphan.get("strategy_id"),
                "reason": orphan.get("reason"),
            },
            push=True,
            db_path=db_path,
        )
        # notify() swallows persist failures and returns {} — if the inbox row
        # never landed, leave the orphan 'open' so it re-pages next tick (a
        # duplicate page beats a permanently lost one). Push-only failures
        # still return the persisted row and correctly mark-and-move-on.
        if sent:
            mark_orphan_notified(str(orphan["id"]), db_path=db_path)
            paged += 1
    return paged


async def sweep_unmanaged_terminal_fills(
    *,
    lookback_days: int = _SWEEP_LOOKBACK_DAYS,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Backstop (audit H1/M1): quarantine + page any strategy BUY holding real filled
    shares that has NEITHER a managed-position row NOR an orphan row.

    ``record_strategy_fill`` only fires on a status transition the poller actually
    observes. So a partial fill that jumps straight to canceled/expired within one poll
    gap, or a fill whose attribution write is lost to a restart between the status write
    and the attribution call (deploys kill the process at arbitrary times), leaves real
    shares held with NO exit rules and NO page — the exact silent orphan safety-M1 was
    built to make impossible. This sweep closes that seam durably by scanning terminal
    BUYs with real fills and quarantining any that attribution never accounted for.

    Idempotent: a fill already attributed (managed row) or already quarantined (orphan
    row of ANY status) is skipped, so re-running across restarts never double-quarantines
    or double-pages. Backfill-safe: the first run may find historical rows — it pages
    ONCE with a count, not once per row. Returns the number of NEW orphans quarantined
    this pass. Never raises into the caller (best-effort per row)."""
    from db.fill_attribution import orphan_exists_for_execution
    from db.managed_positions import is_execution_managed
    from db.order_executions import list_terminal_partial_buys

    cutoff = (datetime.now(UTC) - timedelta(days=lookback_days)).isoformat()
    new_orphans: list[dict[str, Any]] = []
    for row in list_terminal_partial_buys(since_iso=cutoff, db_path=db_path):
        try:
            execution_id = str(row.get("id") or "") or None
            if not execution_id:
                continue
            filled_qty = row.get("filled_qty")
            try:
                if Decimal(str(filled_qty)) <= 0:
                    continue  # no real shares — nothing to manage
            except (InvalidOperation, TypeError, ValueError):
                continue
            raw_proposal_id = row.get("proposal_id")
            if not raw_proposal_id:
                continue  # non-strategy order — unmanaged by design
            proposal_id = str(raw_proposal_id)
            strategy_id = _resolve_strategy_id(proposal_id, db_path=db_path)
            if strategy_id is None:
                continue  # not strategy-attributable — legitimately has no managed row
            symbol = str(row.get("symbol", "")).strip().upper() or None

            # Shares live in the EXECUTION's account (fall back to the strategy's bound
            # account) — same authority order as record_strategy_fill.
            account_id = row.get("account_id")
            if not account_id:
                strategy = get_strategy(strategy_id, db_path=db_path)
                account_id = strategy.get("account_id") if strategy else None
            account_id = str(account_id) if account_id else None

            # Belt to the candidate SQL's NOT EXISTS orphan exclusion (F3) — cheap,
            # and keeps idempotency even if the SQL screen ever regresses.
            if orphan_exists_for_execution(execution_id, db_path=db_path):
                continue  # already quarantined (open/notified/repaired) — idempotent
            if symbol and is_execution_managed(
                execution_id,
                account_id=account_id,
                symbol=symbol,
                strategy_id=strategy_id,
                exec_created_at=str(row.get("created_at") or ""),
                db_path=db_path,
            ):
                continue  # attribution opened a row, or the fill folded into a
                # triple row alive at the execution's creation (even if since closed)

            orphan_id = record_orphan(
                execution_id=execution_id,
                reason="unmanaged_terminal_fill",
                symbol=symbol,
                account_id=account_id,
                strategy_id=strategy_id,
                proposal_id=proposal_id,
                qty=str(filled_qty),
                detail=(
                    f"sweep: terminal BUY status={row.get('status')} "
                    f"filled_qty={filled_qty} with no managed row and no orphan "
                    "(poll-gap / restart attribution seam)"
                ),
                db_path=db_path,
            )
            if orphan_id is not None:
                new_orphans.append(
                    {
                        "orphan_id": orphan_id,
                        "execution_id": execution_id,
                        "symbol": symbol or "?",
                    }
                )
        except Exception:
            logger.exception(
                "Unmanaged-fill sweep failed on execution %s", row.get("id")
            )

    if new_orphans:
        await _page_sweep_batch(new_orphans, db_path=db_path)
    return len(new_orphans)


async def _page_sweep_batch(
    orphans: list[dict[str, Any]], *, db_path: Path
) -> None:
    """Send ONE critical page for a batch of sweep-quarantined fills, then mark them
    notified so the per-row ``flush_fill_attribution_orphans`` does not re-page them.

    On a notify persist failure (notify() returns {}), leave them 'open' so the per-row
    flush still pages them next tick — degraded but never lost, matching the module's
    'a duplicate page beats a permanently lost one' stance."""
    from db.fill_attribution import mark_orphan_notified
    from signals.notify import notify

    count = len(orphans)
    symbols = sorted({o["symbol"] for o in orphans})
    sample = ", ".join(symbols[:8]) + ("…" if len(symbols) > 8 else "")
    plural = "s" if count != 1 else ""
    sent = await notify(
        kind="attribution_orphan",
        title=f"{count} unmanaged fill{plural} found by sweep",
        body=(
            f"{count} strategy BUY fill{plural} hold real shares with NO exit "
            f"management — detected by the poll-gap/restart sweep ({sample}). Each "
            "needs manual repair."
        ),
        severity="critical",
        ref_type="execution",
        ref_id=orphans[0]["execution_id"],
        data={
            "type": "attribution_orphan_sweep",
            "count": count,
            "orphans": orphans,
        },
        push=True,
        db_path=db_path,
    )
    # notify() swallows persist failures and returns {} — only mark notified once the
    # page is durably persisted, else leave 'open' for the per-row flush fallback.
    if sent:
        for orphan in orphans:
            mark_orphan_notified(str(orphan["orphan_id"]), db_path=db_path)


def record_session_node_managed_fill(
    execution_row: dict[str, Any],
    *,
    filled_qty: float | None,
    status: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """Tag session-node SPY fills for exit-prompt tracking. Never executes."""
    try:
        from signals.session_node_strategy import record_session_node_fill

        return record_session_node_fill(
            execution_row,
            filled_qty=filled_qty,
            status=status,
            db_path=db_path,
        )
    except Exception:
        logger.exception("Failed to record session-node managed position from fill")
        return None
