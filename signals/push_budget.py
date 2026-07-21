"""Shared daily push-budget slots — one pool across every phone-tier lane.

Lifted verbatim from ``signals/options_flow/convergence.py`` (Fusion C5): the flow
convergence lane and the fusion idea lane draw from ONE daily budget
(``alerts.max_push_per_day``) with history continuity. The ``of_`` key prefix is
legacy-named (options_flow was the first consumer) but the pool is SHARED BY
DESIGN — slots a day's convergence pushes already consumed are visible to the
idea lane and vice versa. Same ``of_push:{day}:{n}`` key format, same
deny-by-default (any error → no slot), same store (the earnings_alerts dedup
memory).

This is a BEST-EFFORT SOFT CAP, not an atomic guarantee: peek→push→mark is three
steps with no lock across them, and the consumers live in separate OS processes
sharing one SQLite file (backend fusion lane; flow-collector convergence lane),
so two concurrent consumers can both peek the same free slot and both push —
worst-case overrun ≈ one push per concurrent consumer. Accepted: it guards phone
attention, not money, and the pool becomes single-consumer when the convergence
push path retires at Fusion C6.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def peek_push_slot(today_et: str, *, db_path: Path) -> str | None:
    """First free daily-budget slot key, or None. Deny-by-default: any error → None."""
    try:
        from config.settings import alerts_config
        from db.earnings_alerts import was_sent

        for n in range(1, alerts_config()["max_push_per_day"] + 1):
            key = f"of_push:{today_et}:{n}"
            if not was_sent(key, db_path=db_path):
                return key
        return None
    except Exception:
        logger.debug("push budget peek failed", exc_info=True)
        return None


def mark_push_slot(key: str, *, db_path: Path) -> None:
    try:
        from db.earnings_alerts import mark_sent

        mark_sent(key, db_path=db_path)
    except Exception:
        logger.debug("push budget mark failed for %s", key, exc_info=True)
