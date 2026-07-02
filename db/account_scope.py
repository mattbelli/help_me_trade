"""Read-only workspace account scope resolver for performance/ledger queries.

Maps the frontend workspace account selector values (``overview``, ``all_real``,
``schwab_<last4>``, ``acct_*``, etc.) to the ``(ledger_account_id, real_only,
paper_only)`` tuple used to slice trade events and analytics.

This module NEVER writes to ``trade_accounts`` or any ledger table, and is NOT
used to authorize execution — only to filter read-only performance queries.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.trades import get_trade_account, list_trade_accounts

logger = logging.getLogger(__name__)

_AGGREGATE_REAL = frozenset({None, "", "overview", "all", "all_real"})


def resolve_perf_scope(
    workspace_account_id: str | None,
    *,
    get_schwab_accounts: Callable[[], dict[str, Any]] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[str | None, bool, bool]:
    """Resolve a workspace selector id to ``(ledger_account_id, real_only, paper_only)``.

    Returns ``(None, True, False)`` for aggregate real scopes and as the safe
    fallback when a Schwab or ``acct_*`` id cannot be resolved.
    """
    if workspace_account_id in _AGGREGATE_REAL:
        return None, True, False

    if workspace_account_id == "all_paper":
        return None, False, True

    if workspace_account_id and str(workspace_account_id).startswith("schwab_"):
        return _resolve_schwab_scope(
            str(workspace_account_id),
            get_schwab_accounts=get_schwab_accounts,
            db_path=db_path,
        )

    if workspace_account_id and str(workspace_account_id).startswith("acct_"):
        if get_trade_account(str(workspace_account_id), db_path=db_path):
            return str(workspace_account_id), False, False
        logger.warning(
            "resolve_perf_scope: unknown acct id %r — falling back to aggregate real",
            workspace_account_id,
        )
        return None, True, False

    logger.warning(
        "resolve_perf_scope: unrecognized workspace account id %r — "
        "falling back to aggregate real",
        workspace_account_id,
    )
    return None, True, False


def _resolve_schwab_scope(
    workspace_account_id: str,
    *,
    get_schwab_accounts: Callable[[], dict[str, Any]] | None,
    db_path: Path,
) -> tuple[str | None, bool, bool]:
    if get_schwab_accounts is None:
        from brokers.schwab_accounts import get_schwab_accounts_sync

        get_schwab_accounts = get_schwab_accounts_sync

    try:
        data = get_schwab_accounts()
    except Exception:
        logger.warning(
            "resolve_perf_scope: Schwab accounts lookup failed for %r — "
            "falling back to aggregate real",
            workspace_account_id,
            exc_info=True,
        )
        return None, True, False

    accounts = data.get("accounts") or []
    last4 = workspace_account_id.removeprefix("schwab_")
    schwab_acct = next(
        (
            a
            for a in accounts
            if a.get("id") == workspace_account_id or a.get("last4") == last4
        ),
        None,
    )
    if not schwab_acct:
        logger.warning(
            "resolve_perf_scope: no Schwab account for %r — "
            "falling back to aggregate real",
            workspace_account_id,
        )
        return None, True, False

    label = schwab_acct.get("label")
    if not label:
        logger.warning(
            "resolve_perf_scope: Schwab account %r has no label — "
            "falling back to aggregate real",
            workspace_account_id,
        )
        return None, True, False

    ledger_row = next(
        (
            row
            for row in list_trade_accounts(db_path=db_path)
            if row.get("broker") == "schwab" and row.get("label") == label
        ),
        None,
    )
    if not ledger_row:
        logger.warning(
            "resolve_perf_scope: no trade_accounts row for Schwab label %r "
            "(from %r) — falling back to aggregate real",
            label,
            workspace_account_id,
        )
        return None, True, False

    return ledger_row["id"], False, False
