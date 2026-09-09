"""Accelerated Schwab ledger refresh from live account-change signals.

The hourly ``_trade_sync_loop`` is the catch-all; this is the fast lane. Two
read-only signals feed it:

* Schwab Account Activity websocket messages request a sweep for the account
  named by the event (or all accounts if it cannot be mapped). This is the
  primary fill signal and catches fast round trips (notably futures) that open
  and close between account snapshots.
* A **fresh, successful** account snapshot (``brokers.schwab_accounts``) whose
  signed holdings changed requests a sweep narrowed to that account hash.

Both requests run off the caller's thread, so a fill the founder just took shows
up in the ledger in seconds instead of up to an hour.

Read-only throughout: the only downstream call is ``sync_schwab``, which is
GET-only. Mark-price and day-P&L drift never enter the signature, so the normal
120s snapshot poll cannot fan out Transactions calls; a per-account cooldown
plus a debounce window bounds the rest. A refresh that fails is simply logged —
the hourly sync still covers it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

# Coalesce a burst of snapshots (and give Schwab time to publish the fill)
# before spending a Transactions sweep.
_DEBOUNCE_S = 20.0
# Floor between two accelerated sweeps for the same account. The snapshot poll
# runs every 120s, so this caps the fast lane at ~1 sweep per account per 5min
# no matter how often positions move.
_COOLDOWN_S = 300.0

_QTY_STEP = Decimal("0.000001")
_ALL_ACCOUNTS = "*"

_lock = threading.Lock()
_baseline: dict[str, tuple[tuple[str, str], ...]] = {}  # hash -> last seen signature
_pending: set[str] = set()  # account hashes (or _ALL_ACCOUNTS) awaiting a sweep
_next_allowed: dict[str, float] = {}  # hash -> monotonic time the cooldown ends
_worker: threading.Thread | None = None


def observe_account_activity(external_ids: Iterable[str] | None = None) -> bool:
    """Queue a refresh after a Schwab Account Activity websocket message.

    ``external_ids`` contains Schwab's opaque account hashes when the stream's
    account number mapped cleanly. An absent/empty mapping fails safe to an
    all-account sweep; transaction IDs deduplicate it. The existing debounce and
    five-minute per-account cooldown bound bursty fill/order messages. Returns
    whether this call added any new pending request.
    """
    queued = False
    try:
        keys = {
            str(external_id).strip()
            for external_id in (external_ids or [])
            if str(external_id).strip()
        }
        if not keys:
            keys = {_ALL_ACCOUNTS}
        with _lock:
            new_keys = keys - _pending
            if new_keys:
                _pending.update(new_keys)
                queued = True
            _ensure_worker()
    except Exception:  # noqa: BLE001 — never break the market-data stream
        logger.exception("Schwab account-activity observation failed")
        return False
    if queued:
        logger.info("Schwab account activity — accelerated ledger refresh queued")
    return queued


def _signature(positions: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, str], ...]:
    """(symbol, signed qty) pairs — and nothing else.

    Price, market value and day P&L are deliberately excluded: they move on
    every snapshot and are not evidence of a transaction. Symbols are upper-cased
    and same-symbol rows summed (Schwab can split a holding across rows), so
    formatting churn alone never reads as a change.
    """
    totals: dict[str, Decimal] = {}
    for pos in positions or []:
        symbol = str(pos.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        try:
            qty = Decimal(str(pos.get("qty") or 0))
        except (InvalidOperation, ValueError):
            continue
        totals[symbol] = totals.get(symbol, Decimal("0")) + qty
    out = []
    for symbol in sorted(totals):
        qty = totals[symbol].quantize(_QTY_STEP)
        if qty == 0:
            continue
        out.append((symbol, format(qty, "f")))
    return tuple(out)


def observe_positions(
    positions_by_hash: Mapping[str, Iterable[Mapping[str, Any]]],
) -> list[str]:
    """Record a fresh snapshot; queue a ledger refresh for accounts that moved.

    Only call this with a **real upstream success** — a cache hit, stale
    fallback or error would either re-fire an old state or look like every
    position vanished. The first observation of an account is the baseline and
    never fires. Returns the account hashes queued (for tests/logging).
    """
    queued: list[str] = []
    try:
        with _lock:
            for account_hash, positions in (positions_by_hash or {}).items():
                key = str(account_hash or "")
                if not key:
                    continue
                signature = _signature(positions)
                previous = _baseline.get(key)
                _baseline[key] = signature
                # No baseline yet (backend boot / newly linked account), or the
                # holdings are unchanged, or this exact state is already queued
                # — nothing to sweep for.
                if previous is None or signature == previous:
                    continue
                _pending.add(key)
                queued.append(key)
            if queued:
                _ensure_worker()
    except Exception:  # noqa: BLE001 — never break the snapshot read path
        logger.exception("Schwab position-change observation failed")
        return []
    if queued:
        logger.info(
            "Schwab positions changed on %d account(s) — accelerated ledger refresh queued",
            len(queued),
        )
    return queued


def _ensure_worker() -> None:
    """Start the single refresh worker if it isn't running (caller holds _lock)."""
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    _worker = threading.Thread(
        target=_run, name="schwab-ledger-accelerate", daemon=True
    )
    _worker.start()


def _run() -> None:
    """Debounce, then sweep every account whose cooldown has expired.

    One worker at a time, so accelerated sweeps never overlap each other; the
    hourly job stays safe because ``sync_schwab`` holds its own non-blocking
    lock. An account still cooling down keeps its pending state and is swept on
    a later pass rather than dropped.
    """
    global _worker
    while True:
        time.sleep(_DEBOUNCE_S)
        with _lock:
            now = time.monotonic()
            due = [h for h in _pending if now >= _next_allowed.get(h, 0.0)]
            if not due:
                if not _pending:
                    _worker = None
                    return
                continue
            # An Account Activity request sweeps every mapped account, so it
            # subsumes any account-specific position requests already pending.
            if _ALL_ACCOUNTS in due:
                due = [_ALL_ACCOUNTS]
                _pending.clear()
            for account_hash in due:
                _pending.discard(account_hash)
                _next_allowed[account_hash] = now + _COOLDOWN_S
        _refresh(due)


def _refresh(hashes: list[str]) -> None:
    """Run the read-only Transactions sync for all or the changed accounts."""
    try:
        from trading.sync import sync_schwab

        external_ids = None if _ALL_ACCOUNTS in hashes else hashes
        result = sync_schwab(external_ids=external_ids)
    except Exception:
        logger.exception(
            "Accelerated Schwab ledger refresh failed (%d account(s))", len(hashes)
        )
        return
    logger.info("Accelerated Schwab ledger refresh: %s", result)
