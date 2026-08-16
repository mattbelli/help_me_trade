"""Housekeeping jobs: the three LLM paper-book ticks (DeepSeek, Matt, dead-man),
DB retention, and the filings feed (EDGAR + congress + initial fill). Extracted
verbatim from ``api/main`` (strangler round 3); ``api/background_jobs`` registers
the periodic ones, ``api/main``'s lifespan starts ``_filings_initial_fill``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from trading.market_hours import _ET

logger = logging.getLogger(__name__)


_FILINGS_FEED_ENABLED = os.environ.get("FILINGS_FEED_ENABLED", "1").strip() not in (
    "0",
    "false",
    "no",
)
FILINGS_INSIDER_MIN_USD = float(os.environ.get("FILINGS_INSIDER_MIN_USD", "100000"))


_db_retention_last_date: dict = {}


async def _book_deepseek_once() -> None:
    """Deep Seek paper-book nightly analyst cycle (research/book_driver.py).

    Self-gates: weekday post-17:05-ET, once per ET day, skips when the DeepSeek
    key is unconfigured. Disabled strategy record = DRY cycle (pack + report +
    lane rows + inbox, no proposals) — the shipped state until the founder arms it."""
    from research.book_driver import run_book_cycle

    result = await run_book_cycle("deepseek")
    if result.get("status") != "skipped":
        logger.info("Deep Seek book cycle: %s", result)


async def _book_matt_once() -> None:
    """Matt + LLM co-managed book — nightly ADVISORY analyst cycle: same research
    session and inbox report, structurally zero proposals and zero lane rows."""
    from research.book_driver import run_book_cycle

    result = await run_book_cycle("matt")
    if result.get("status") != "skipped":
        logger.info("Matt+LLM book cycle: %s", result)


_book_deadman_last_date: dict = {}


async def _book_deadman_once() -> None:
    """Books dead-man (spec §6): any book with no report by 20:00 ET gets flagged
    — inbox + push (severity=warning). Once per ET day; the in-memory stamp
    resetting on restart re-checks, which is the safe direction."""
    from db.reports import report_exists_for_day
    from research.books import BOOKS
    from signals.notify import notify

    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5 or now_et.hour < 20:
        return
    today = now_et.date()
    if _book_deadman_last_date.get("date") == today:
        return
    missing = [
        b.name
        for b in BOOKS.values()
        if not b.paused
        and not report_exists_for_day(
            f"book_{b.book_id}", today, dedup_key=f"book_{b.book_id}"
        )
    ]
    _book_deadman_last_date["date"] = today
    if not missing:
        return
    await notify(
        kind="book_deadman",
        title=f"Book dead-man: {len(missing)} report(s) missing tonight",
        body="No nightly report filed by 20:00 ET: " + ", ".join(sorted(missing)),
        severity="warning",
    )
    logger.warning("book dead-man fired: missing=%s", missing)


async def _db_retention_once() -> None:
    from db.earnings_alerts import prune_earnings_alerts_sent
    from db.equity_tape import prune as prune_equity_tape
    from db.retention import compress_research_captures, prune_poll_history

    now_et = datetime.now(_ET)
    if not (1 <= now_et.hour < 5):
        return
    today = now_et.date()
    if _db_retention_last_date.get("date") == today:
        return
    counts = await asyncio.to_thread(prune_poll_history)
    # RVOL alert-dedup keys grow ~1 row/symbol/day forever — prune the old ones.
    pruned_alerts = await asyncio.to_thread(prune_earnings_alerts_sent)
    # Equity tape events (blocks + dark-pool prints) age out past EVENTS_PRUNE_DAYS.
    pruned_tape = await asyncio.to_thread(prune_equity_tape)
    # Prior sessions' research capture JSONL (print lab + chain samples) gzip
    # losslessly (~14x) once immutable — never downsampled (lab ground truth).
    compressed = await asyncio.to_thread(compress_research_captures)
    _db_retention_last_date["date"] = today
    logger.info(
        "DB retention complete: %s (earnings_alerts_sent pruned=%s, equity_tape "
        "pruned=%s, research_jsonl=%s)",
        counts,
        pruned_alerts,
        pruned_tape,
        compressed,
    )






# ---------------------------------------------------------------------------
# Filings feed: insider Form 4 buys + 13D stakes (EDGAR, near-real-time) and
# congressional trades (STOCK Act, legally up to 45 days stale). A feed the
# founder eyeballs — no scoring, no alerts, no outcome machinery.
# ---------------------------------------------------------------------------
async def _filings_edgar_once() -> None:
    from db.filings import has_uid, insert_events, remember_uid
    from signals.filings.edgar import poll_13d_once, poll_form4_once

    def _arm(poller, label: str) -> tuple[list, bool]:
        try:
            return poller(), True
        except Exception:
            logger.exception("Filings EDGAR %s poll failed", label)
            return [], False

    def _pass() -> None:
        form4, form4_ok = _arm(
            lambda: poll_form4_once(
                seen=has_uid,
                remember=remember_uid,
                min_usd=FILINGS_INSIDER_MIN_USD,
            ),
            "Form 4",
        )
        d13, d13_ok = _arm(lambda: poll_13d_once(seen=has_uid), "13D")
        inserted = insert_events(form4 + d13)
        if inserted:
            logger.info("Filings feed: %d new EDGAR event(s)", inserted)
        if not form4_ok and not d13_ok:
            raise RuntimeError("both Form 4 and 13D pollers failed")

    await asyncio.to_thread(_pass)


async def _filings_congress_once() -> None:
    from db.filings import insert_events
    from signals.filings.congress import poll_congress_once

    def _pass() -> int:
        return insert_events(poll_congress_once())

    inserted = await asyncio.to_thread(_pass)
    if inserted:
        logger.info("Filings feed: %d new congressional trade(s)", inserted)


async def _filings_initial_fill() -> None:
    """Jobs sleep a full interval before their first pass (6 h for congress) —
    seed the feed shortly after boot so a deploy never blanks it."""
    if not _FILINGS_FEED_ENABLED:
        return
    await asyncio.sleep(15)
    for fn in (_filings_congress_once, _filings_edgar_once):
        try:
            await fn()
        except Exception:
            logger.exception("Filings initial fill failed: %s", fn.__name__)
