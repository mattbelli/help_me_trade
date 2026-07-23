"""Protective-stop guard for book entries — the analysts' only intraday defense.

Book analysts convene once a night; between cycles nobody watches their
positions. Every advanced book pick states a stop (the falsifier), and this
guard turns it into a RESTING GTC stop sell at the broker as soon as the entry
fills — protection that holds even if this process dies. Runs on the account
poll cadence right after fill reconciliation, so the snapshot it validates
against was captured seconds earlier, already showing the new position.

Idempotency is durable, not in-memory: the stop's proposal_id is
``stop|<entry_proposal_id>`` and a non-failed execution row under that id means
the stop exists — a restart re-derives everything from the DB. Failed attempts
(snapshot lag right at the open, transient broker errors) retry on later ticks
behind an in-memory backoff; exhausting the attempt budget pages the founder
once rather than hammering the daily order counters forever.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.order_executions import get_execution_by_proposal
from db.paper_orders import list_paper_order_proposals
from signals.notify import notify
from trading.execution_service import ExecutionService
from trading.models import OrderIntent, RiskRejectedError

logger = logging.getLogger(__name__)

_LOOKBACK_DAYS = 7
# Entry fill states that leave real shares needing protection: a full fill, or
# a terminal partial (the order died at day end / was canceled with shares in).
# Non-terminal "partially_filled" stays in the fill tracker's reconcile pool —
# the stop waits for the final share count so it never undersizes. EXCEPT: a
# partial whose row hasn't changed in _PARTIAL_SETTLE_SECONDS is protected at
# its CURRENT share count — Alpaca's done_for_day maps here and gets no more
# fills today, so waiting means an unprotected position all session
# (adversarial review M1-lifecycle, 2026-07-23). If a slow partial later fills
# more, the resting stop under-covers the new shares (frozen-qty semantics,
# same as a founder trim) — partial protection beats none.
_PROTECTED_ENTRY_STATUSES = frozenset({"filled", "canceled", "expired"})
_PARTIAL_SETTLE_SECONDS = 900.0
# A stop execution in any of these states means placement FAILED and a retry is
# allowed; anything else (pending/submitted/filled/…) means the stop exists.
_STOP_RETRYABLE_STATUSES = frozenset({"rejected", "error"})

_RETRY_COOLOFF_SECONDS = 300.0
_MAX_ATTEMPTS = 10

# proposal_id → (attempts, last_attempt_monotonic). In-memory on purpose: a
# restart resets the budget and the DB check above still prevents duplicates.
_attempts: dict[str, tuple[int, float]] = {}
_paged: set[str] = set()


def _wire_qty(qty: Decimal) -> Decimal:
    """Whole-number fills wire as integer-format Decimals (the exit-engine rule:
    "2", never "2.0", or a non-fractionable symbol's stop gets rejected)."""
    return Decimal(int(qty)) if qty == qty.to_integral_value() else qty


def _partial_settled(execution: dict[str, Any]) -> bool:
    """True when a partially_filled row has been quiet long enough to protect
    at its current share count (done_for_day shape — no more fills coming)."""
    raw = execution.get("updated_at") or execution.get("created_at")
    if not raw:
        return False
    try:
        updated = datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return (datetime.now(UTC) - updated).total_seconds() >= _PARTIAL_SETTLE_SECONDS


def _entry_filled_qty(execution: dict[str, Any]) -> Decimal | None:
    raw = execution.get("filled_qty")
    if raw is None and execution.get("status") == "filled":
        # Older rows may predate fill-economics persistence; a full fill's
        # share count is the requested qty by definition.
        raw = execution.get("qty")
    try:
        qty = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return qty if qty > 0 else None


async def place_protective_stops(
    broker_name: str,
    *,
    account_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Place missing GTC protective stops for filled book entries. Returns the
    number placed this pass. Never raises — this runs inside the poll loop."""
    placed = 0
    try:
        since = (datetime.now(UTC) - timedelta(days=_LOOKBACK_DAYS)).isoformat()
        proposals = list_paper_order_proposals(
            status="executed", since=since, limit=200, db_path=db_path
        )
    except Exception:
        logger.exception("stop guard: failed to list executed proposals")
        return 0

    for row in proposals:
        try:
            source = str(row.get("source") or "")
            if not source.startswith("book_"):
                continue
            if str(row.get("side") or "") != "buy":
                continue
            if account_id is not None and row.get("account_id") != account_id:
                continue
            stored = row.get("proposal") or {}
            stop_raw = stored.get("stop_price")
            if stop_raw is None:
                continue
            proposal_id = str(row["id"])

            entry = get_execution_by_proposal(proposal_id, db_path=db_path)
            if entry is None:
                continue
            status = entry.get("status")
            if status not in _PROTECTED_ENTRY_STATUSES and not (
                status == "partially_filled" and _partial_settled(entry)
            ):
                continue
            filled = _entry_filled_qty(entry)
            if filled is None:
                continue

            stop_pid = f"stop|{proposal_id}"
            existing = get_execution_by_proposal(stop_pid, db_path=db_path)
            if existing is not None and (
                existing.get("status") not in _STOP_RETRYABLE_STATUSES
            ):
                continue

            attempts, last = _attempts.get(proposal_id, (0, 0.0))
            if attempts >= _MAX_ATTEMPTS:
                if proposal_id not in _paged:
                    _paged.add(proposal_id)
                    await _page_unprotected(row, db_path=db_path)
                continue
            if last and time.monotonic() - last < _RETRY_COOLOFF_SECONDS:
                continue
            _attempts[proposal_id] = (attempts + 1, time.monotonic())

            intent = OrderIntent(
                proposal_id=stop_pid,
                session_id=None,
                symbol=str(row["symbol"]).upper(),
                side="sell",
                qty=_wire_qty(filled),
                order_type="stop",
                limit_price=None,
                stop_price=Decimal(str(stop_raw)),
                time_in_force="gtc",
                broker=str(entry.get("broker") or broker_name),
                asset_class="us_equity",
                account_id=entry.get("account_id"),
            )
            try:
                result = await ExecutionService.submit(intent, db_path=db_path)
            except RiskRejectedError as exc:
                logger.warning(
                    "stop guard: %s stop for %s rejected (attempt %d): %s",
                    row["symbol"], proposal_id, attempts + 1, exc,
                )
                continue
            placed += 1
            _attempts.pop(proposal_id, None)
            logger.info(
                "stop guard: placed GTC stop %s for %s %s @ %s (entry %s)",
                result.execution_id, row["symbol"], filled, stop_raw, proposal_id,
            )
        except Exception:
            logger.exception("stop guard: failed for proposal %s", row.get("id"))
    return placed


async def _page_unprotected(row: dict[str, Any], *, db_path: Path) -> None:
    """One critical page when a filled book entry could not be protected."""
    try:
        await notify(
            kind="risk_block",
            title=f"UNPROTECTED position: {row.get('symbol')}",
            body=(
                f"Book entry {row.get('id')} filled but its protective stop "
                f"could not be placed after {_MAX_ATTEMPTS} attempts — "
                "place a stop manually or flatten."
            ),
            severity="critical",
            account_id=row.get("account_id"),
            ref_type="proposal",
            ref_id=str(row.get("id")),
            push=True,
            db_path=db_path,
        )
    except Exception:
        logger.exception("stop guard: failed to page for %s", row.get("id"))
