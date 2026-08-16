"""Earnings jobs: calendar refresh (daily), EDGAR release poll (5 min), and the
earnings-alert tick (+ the catch-up kick a watchlist edit fires). Extracted
verbatim from ``api/main`` (strangler round 3). ``api/main``'s lifespan starts
the two loops; ``api/background_jobs`` registers ``_earnings_alert_once``;
``api/watchlist_routes`` calls ``_kick_earnings_alert_catchup``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime

from api.jobs import mark_alive
from db.earnings_calendar import earnings_hour, last_earnings_date, next_earnings_date
from db.strategies import list_strategies
from signals.earnings_refresh import arefresh_earnings_calendar
from signals.strategy_kinds import strategy_kind
from signals.strategy_universe import resolve_universe_spec
from signals.universe import all_watchlist_symbols as _all_watchlist_symbols
from trading.market_hours import _ET

logger = logging.getLogger(__name__)


EARNINGS_REFRESH_INTERVAL_SECONDS = 24 * 3600
# The EDGAR "current filings" feed is a rolling window of the latest 100 8-Ks.
# Measured on a heavy earnings evening it held 74 minutes of filings, so polling
# every 5 minutes leaves ~15x headroom before a burst could push one off the
# window unseen. Cheap: one atom request, then two requests per NEW listed filer.
EARNINGS_RELEASE_POLL_SECONDS = 300


EARNINGS_ALERT_INTERVAL_SECONDS = max(
    60, int(os.environ.get("EARNINGS_ALERT_INTERVAL_SECONDS", str(6 * 3600)))
)
_EARNINGS_ALERTS_ENABLED = os.environ.get("EARNINGS_ALERTS_ENABLED", "1").strip() not in (
    "0",
    "false",
    "no",
)


async def _earnings_refresh_once() -> int:
    """Refresh the earnings calendar for the universes of ENABLED earnings-kind strategies.

    One Finnhub call covers all companies; it filters to the union of those universes. Returns
    rows upserted (0 if nothing enabled / Finnhub unconfigured).
    """
    symbols: set[str] = set()
    for s in list_strategies(enabled_only=True):
        if strategy_kind(s["id"]) == "earnings":
            symbols |= set(resolve_universe_spec(s.get("universe")))
    # Also cover the watchlist so its fundamentals panel shows a next-earnings date
    # (Schwab gives only lastEarningsDate; the forward date is Finnhub/calendar-sourced).
    symbols |= set(_all_watchlist_symbols())
    if not symbols:
        return 0
    return await arefresh_earnings_calendar(sorted(symbols))


async def _earnings_refresh_loop() -> None:
    while True:
        try:
            n = await _earnings_refresh_once()
            if n:
                logger.info("Earnings calendar refresh upserted %d rows", n)
        except Exception:
            logger.exception("Earnings refresh loop iteration failed")
        else:
            mark_alive("earnings_refresh")
        await asyncio.sleep(EARNINGS_REFRESH_INTERVAL_SECONDS)


async def _earnings_release_loop() -> None:
    """Pull new Item 2.02 earnings releases off EDGAR and store their exhibit text.

    Separate from `_earnings_refresh_loop` (which is the daily calendar of WHEN
    companies report): this is the release itself, and it is the only source we
    hold for segment results and guidance — the XBRL corpus strips dimensions.
    """
    from signals.earnings_exhibit import poll_once

    while True:
        try:
            summary = await asyncio.to_thread(poll_once)
            if summary["stored"]:
                logger.info(
                    "Earnings releases stored: %d (failed %d)",
                    summary["stored"],
                    summary["failed"],
                )
        except Exception:
            logger.exception("Earnings release poll failed")
        else:
            mark_alive("earnings_release")
        await asyncio.sleep(EARNINGS_RELEASE_POLL_SECONDS)


def _upcoming_earnings_date(symbol: str, today_iso: str) -> str | None:
    """Next watchlist earnings on or after today (includes day-of)."""
    if last_earnings_date(symbol, today_iso) == today_iso:
        return today_iso
    return next_earnings_date(symbol, today_iso)


def _earnings_hour_suffix(hour: str | None, *, day_of: bool) -> str:
    if not hour:
        return ""
    h = hour.lower()
    if day_of:
        if h == "amc":
            return ", after close"
        if h == "bmo":
            return ", before open"
        return ""
    if h == "amc":
        return ", AMC"
    if h == "bmo":
        return ", BMO"
    return ""


def _t0_still_ahead(hour: str | None, now_et: datetime) -> bool:
    """True while a day-of 'reports TODAY' alert is still a heads-up, not a post-mortem.

    BMO prints before the open → useless after 09:30 ET. AMC (and unknown hour)
    print at/after the close → useless once the regular session has ended.
    Without this gate, adding a name to the watchlist after the print (or a 6h
    loop landing post-close) still fires 'reports TODAY' hours late.
    """
    from trading.market_hours import session_close_time

    minutes = now_et.hour * 60 + now_et.minute
    h = (hour or "").strip().lower()
    if h == "bmo":
        return minutes < 9 * 60 + 30
    close_h, close_m = session_close_time(now_et.date())
    return minutes < close_h * 60 + close_m


def _kick_earnings_alert_catchup() -> None:
    """Fire-and-forget a pass so a mid-day watchlist add doesn't wait up to 6h."""

    async def _run() -> None:
        try:
            await _earnings_alert_once()
        except Exception:
            logger.exception("Earnings alert catch-up after watchlist edit failed")

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        # Sync context with no loop (tests) — the periodic job will pick it up.
        pass


async def _earnings_alert_once() -> None:
    """Check watchlist symbols and push earnings milestone alerts (t14/t1/t0)."""
    from db.earnings_alerts import mark_sent, was_sent
    from signals.notify import notify

    now_et = datetime.now(_ET)
    today = now_et.date()
    today_iso = today.isoformat()
    symbols = _all_watchlist_symbols()
    if not symbols:
        return

    for sym in symbols:
        try:
            earnings_date_str = _upcoming_earnings_date(sym, today_iso)
            if not earnings_date_str:
                continue
            earnings_date = date.fromisoformat(earnings_date_str)
            if earnings_date < today:
                continue
            days_until = (earnings_date - today).days
            hour = earnings_hour(sym, earnings_date_str)
            hour_t1 = _earnings_hour_suffix(hour, day_of=False)
            hour_t0 = _earnings_hour_suffix(hour, day_of=True)

            milestones: list[tuple[str, str]] = []
            if 1 < days_until <= 14:
                milestones.append(("t14", f"📅 {sym} earnings in 2 weeks — {earnings_date_str}"))
            if 0 < days_until <= 1:
                milestones.append(
                    (
                        "t1",
                        f"⏰ {sym} reports tomorrow ({earnings_date_str}{hour_t1})",
                    )
                )
            if days_until == 0 and _t0_still_ahead(hour, now_et):
                milestones.append(
                    (
                        "t0",
                        f"🔔 {sym} reports TODAY ({earnings_date_str}{hour_t0})",
                    )
                )

            for milestone, body in milestones:
                key = f"{sym}:{earnings_date_str}:{milestone}"
                if was_sent(key):
                    continue
                await notify(
                    kind="earnings",
                    title=f"Earnings: {sym} — {milestone}",
                    body=body,
                    severity="info",
                    action_url=f"/watchlist/{sym}",
                    data={
                        "symbol": sym,
                        "earnings_date": earnings_date_str,
                        "milestone": milestone,
                    },
                )
                mark_sent(key)
                logger.info(
                    "Earnings alert sent: %s milestone=%s date=%s",
                    sym,
                    milestone,
                    earnings_date_str,
                )
        except Exception:
            logger.exception("Earnings alert: error processing symbol %s", sym)
