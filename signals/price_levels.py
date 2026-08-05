"""Named price levels derived from daily bars — deterministic, never LLM arithmetic.

Completes the level ladder the copilot kept reaching for. Session nodes cover the
day (POC/VAH/VAL/PDH/PDL) and ``compute_session_levels`` adds the prior week
(PWH/PWL); this module adds month and year. Kept OUT of ``compute_session_levels``
on purpose: that function's output feeds the fusion proximity path
(``nearest_level`` → structure dots → dossier claims), so adding levels there
would silently change which level is "nearest" on a live lane.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from brokers.alpaca import StockBar
from research.intraday_profile import to_ny

LEVEL_KINDS: dict[str, str] = {
    "PMH": "prior_month_high",
    "PML": "prior_month_low",
    "52WH": "fifty_two_week_high",
    "52WL": "fifty_two_week_low",
}

# A 52-week level computed off 3 months of bars is a 3-month level wearing the wrong
# name — worse than omitting it, because the label is what the reader trusts.
_MIN_52W_SPAN_DAYS = 350


def _dated_bars(daily_bars: list[StockBar]) -> list[tuple[date, StockBar]]:
    out: list[tuple[date, StockBar]] = []
    for bar in daily_bars:
        try:
            out.append((to_ny(bar.time).date(), bar))
        except (AttributeError, TypeError, ValueError):
            continue
    return out


def compute_extended_levels(daily_bars: list[StockBar]) -> list[dict[str, Any]]:
    """PMH/PML (prior COMPLETED calendar month) + 52-week high/low from daily bars.

    Takes bars the caller ALREADY holds — the perception path fetches 420 daily bars
    for technicals, so these levels cost no extra network call.

    Two DIFFERENT period conventions, deliberately:
      * PMH/PML are the prior COMPLETED calendar month, mirroring PWH/PWL's "prior
        completed week" contract — the current partial month is excluded.
      * 52WH/52WL are TRAILING and INCLUDE today's (possibly still-forming) bar,
        because that is what "52-week high" universally means. One that stopped at
        last month's close would read wrong on the day a name breaks out.

    Failure-soft by contract: a missing/short history omits the affected level rather
    than raising or guessing.
    """
    dated = _dated_bars(daily_bars)
    if not dated:
        return []

    # Anchor on the real calendar day, not the last bar — a stale fetch must not
    # relabel July as "the prior month" while we are standing in August. Mirrors
    # session_node_strategy._prior_week_levels.
    today = to_ny(datetime.now(UTC).isoformat()).date()
    levels: list[dict[str, Any]] = []

    first_of_month = today.replace(day=1)
    before_this_month = [(day, bar) for day, bar in dated if day < first_of_month]
    if before_this_month:
        # Most recent completed month only — a gap in history must not weld two months.
        last_prior_day = max(day for day, _ in before_this_month)
        month_start = last_prior_day.replace(day=1)
        month = [bar for day, bar in before_this_month if day >= month_start]
        levels.append(
            {
                "name": "PMH",
                "price": max(bar.high for bar in month),
                "kind": LEVEL_KINDS["PMH"],
            }
        )
        levels.append(
            {
                "name": "PML",
                "price": min(bar.low for bar in month),
                "kind": LEVEL_KINDS["PML"],
            }
        )

    span_days = (today - min(day for day, _ in dated)).days
    window = [bar for day, bar in dated if day >= today - timedelta(days=365)]
    if window and span_days >= _MIN_52W_SPAN_DAYS:
        levels.append(
            {
                "name": "52WH",
                "price": max(bar.high for bar in window),
                "kind": LEVEL_KINDS["52WH"],
            }
        )
        levels.append(
            {
                "name": "52WL",
                "price": min(bar.low for bar in window),
                "kind": LEVEL_KINDS["52WL"],
            }
        )

    return levels
