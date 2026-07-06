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


def _strategy_id_from_source(source: str | None) -> str | None:
    if not source or not source.startswith(_STRATEGY_SOURCE_PREFIX):
        return None
    strategy_id = source[len(_STRATEGY_SOURCE_PREFIX) :].strip()
    return strategy_id or None


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

        if proposal_id.startswith("auto_entry|"):
            parts = proposal_id.split("|")
            strategy_id = parts[1] if len(parts) >= 2 else None
        else:
            proposal = get_proposal(proposal_id, db_path=db_path)
            if proposal is None:
                return None
            strategy_id = _strategy_id_from_source(proposal.get("source"))
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
