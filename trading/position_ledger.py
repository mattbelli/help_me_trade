"""Bridge fills → the managed-position ledger (strategy attribution at fill time).

When a strategy-sourced BUY fills, record an OPEN managed position tagged with the
owning strategy + a snapshot of its exit_mode/params, so the exit engine knows
whose rules to apply. Bookkeeping only — it never touches the broker or the
execution record, and every failure is swallowed (a ledger hiccup must never
break fill reconciliation).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.managed_positions import record_open_position
from db.paper_orders import get_proposal
from db.strategies import get_strategy

logger = logging.getLogger(__name__)

_STRATEGY_SOURCE_PREFIX = "strategy:"


def _strategy_id_from_source(source: str | None) -> str | None:
    if not source or not source.startswith(_STRATEGY_SOURCE_PREFIX):
        return None
    strategy_id = source[len(_STRATEGY_SOURCE_PREFIX) :].strip()
    return strategy_id or None


def record_strategy_fill(
    execution_row: dict[str, Any],
    *,
    filled_qty: float | None,
    status: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """If a strategy BUY filled, open a managed-position row. Returns its id or None.

    Fail-closed and silent: any problem logs and returns None — the caller's fill
    reconciliation continues untouched.
    """
    try:
        if status not in {"filled", "partially_filled"}:
            return None
        side = str(execution_row.get("side", "")).strip().lower()
        if side != "buy":
            return None  # exits/sells are never managed entries

        proposal_id = execution_row.get("proposal_id")
        if not proposal_id:
            return None
        proposal = get_proposal(str(proposal_id), db_path=db_path)
        if proposal is None:
            return None
        strategy_id = _strategy_id_from_source(proposal.get("source"))
        if strategy_id is None:
            return None  # not a strategy-originated fill (manual/rule/etc.)

        strategy = get_strategy(strategy_id, db_path=db_path)
        if strategy is None:
            logger.warning(
                "Filled strategy order references unknown strategy %r", strategy_id
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
            logger.warning(
                "Cannot record managed position — no account_id for strategy %s",
                strategy_id,
            )
            return None

        symbol = str(execution_row.get("symbol", "")).strip().upper()
        if not symbol:
            return None

        qty = str(filled_qty) if filled_qty is not None else None
        return record_open_position(
            account_id=str(account_id),
            symbol=symbol,
            strategy_id=strategy_id,
            qty=qty,
            exit_mode=str(strategy.get("exit_mode") or "propose"),
            params=dict(strategy.get("params") or {}),
            entry_execution_id=str(execution_row.get("id") or "") or None,
            db_path=db_path,
        )
    except Exception:
        logger.exception("Failed to record managed position from fill")
        return None
