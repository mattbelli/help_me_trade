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
from typing import Any, NamedTuple

from db.base import DEFAULT_DB_PATH
from db.trades import get_trade_account, list_trade_accounts

logger = logging.getLogger(__name__)

AGGREGATE_REAL = frozenset({None, "", "overview", "all", "all_real"})


class PerfScope(NamedTuple):
    ledger_account_id: str | None
    real_only: bool
    paper_only: bool
    fallback: bool


def resolve_perf_scope(
    workspace_account_id: str | None,
    *,
    get_schwab_accounts: Callable[[], dict[str, Any]] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> PerfScope:
    """Resolve a workspace selector id to scoped ledger query parameters.

    Returns aggregate real ``(None, True, False, False)`` for overview/all_real
    scopes. ``fallback=True`` when a Schwab or ``acct_*`` id cannot be resolved
    and the caller receives aggregate-real data instead.
    """
    if workspace_account_id in AGGREGATE_REAL:
        return PerfScope(None, True, False, False)

    if workspace_account_id == "all_paper":
        return PerfScope(None, False, True, False)

    if workspace_account_id and str(workspace_account_id).startswith("schwab_"):
        return _resolve_schwab_scope(
            str(workspace_account_id),
            get_schwab_accounts=get_schwab_accounts,
            db_path=db_path,
        )

    if workspace_account_id and str(workspace_account_id).startswith("acct_"):
        if get_trade_account(str(workspace_account_id), db_path=db_path):
            return PerfScope(str(workspace_account_id), False, False, False)
        logger.warning(
            "resolve_perf_scope: unknown acct id %r — falling back to aggregate real",
            workspace_account_id,
        )
        return PerfScope(None, True, False, True)

    logger.warning(
        "resolve_perf_scope: unrecognized workspace account id %r — "
        "falling back to aggregate real",
        workspace_account_id,
    )
    return PerfScope(None, True, False, True)


def _resolve_schwab_scope(
    workspace_account_id: str,
    *,
    get_schwab_accounts: Callable[[], dict[str, Any]] | None,
    db_path: Path,
) -> PerfScope:
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
        return PerfScope(None, True, False, True)

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
        return PerfScope(None, True, False, True)

    label = schwab_acct.get("label")
    if not label:
        logger.warning(
            "resolve_perf_scope: Schwab account %r has no label — "
            "falling back to aggregate real",
            workspace_account_id,
        )
        return PerfScope(None, True, False, True)

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
        return PerfScope(None, True, False, True)

    return PerfScope(ledger_row["id"], False, False, False)
