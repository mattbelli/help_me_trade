"""Display-path bar hygiene: clamp self-evidently corrupt OHLC extremes.

Vendor daily/weekly aggregates occasionally bake in an erroneous print —
live case 2026-07-09: Alpaca SIP's SPY 2026-02-02 daily bar carries
``low=69.005`` (decimal-shifted 690.05; the day's VWAP is ~695), drawing a
90% phantom wick on every daily/weekly chart. A wick is deemed corrupt only
against the bar's OWN body: low < LOW_CLAMP_RATIO x min(open, close) or
high > HIGH_CLAMP_RATIO x max(open, close). Corrupt extremes are clamped to
the body edge and logged.

Scope: the chart-serving path ONLY (/api/bars). Strategy/analytics code must
keep seeing raw vendor data — data-quality gates (momentum Inc-3) exist to
DETECT bad bars, not to have them silently laundered. Accepted residual: a
real -50%+ intraday V-wick on a small cap would be clamped here —
display-only, rare, and the raw data is untouched everywhere else.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

LOW_CLAMP_RATIO = 0.5  # low below half the bar body is a corrupt print
HIGH_CLAMP_RATIO = 2.0  # high above twice the bar body is a corrupt print


def sanitize_bar_dicts(symbol: str, bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clamp corrupt low/high extremes in chart-response bar dicts, in place.

    Bars are ``{time, open, high, low, close, volume}`` dicts. Non-numeric or
    non-positive bodies are left untouched (never raise on odd vendor rows).
    """
    for b in bars:
        try:
            o = float(b["open"])
            h = float(b["high"])
            lo = float(b["low"])
            c = float(b["close"])
        except (KeyError, TypeError, ValueError):
            continue
        body_lo = min(o, c)
        body_hi = max(o, c)
        if body_lo <= 0:
            continue
        if lo < body_lo * LOW_CLAMP_RATIO:
            logger.warning(
                "bars hygiene: clamping corrupt %s low %.4f -> %.4f (bar %s)",
                symbol,
                lo,
                body_lo,
                b.get("time"),
            )
            b["low"] = body_lo
        if h > body_hi * HIGH_CLAMP_RATIO:
            logger.warning(
                "bars hygiene: clamping corrupt %s high %.4f -> %.4f (bar %s)",
                symbol,
                h,
                body_hi,
                b.get("time"),
            )
            b["high"] = body_hi
    return bars
