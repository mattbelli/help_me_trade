"""Equal-weight watchlist return index — the math behind the aggregate panel.

Pure computation over per-symbol daily closes; /api/watchlist/aggregate feeds
it from the shared bars cache. Equal-weight and daily-rebalanced: each day's
index move is the plain average of that day's member returns. Price return
only (no dividends, no costs) — the UI labels it as such.
"""

from __future__ import annotations


def equal_weight_index(
    closes_by_symbol: dict[str, list[tuple[str, float]]],
) -> tuple[list[tuple[str, float]], dict[str, float]]:
    """Compound an equal-weight index over the union of member dates.

    Input: per symbol, (iso_date, close) pairs. Output: (points, per_symbol)
    where points = [(date, cumulative_return_pct)] with 0.0 at the first union
    date, and per_symbol maps symbol -> its own first-to-last return pct
    (symbols with fewer than two usable closes are omitted).

    A symbol missing a bar some day sits out that day's average; when it
    reappears, its full since-last-close return enters that day's average —
    the curve keeps the move, credited on the reappearance day. Non-positive
    closes are dropped (bars hygiene should prevent them; never divide by 0).
    """
    series = {
        sym: sorted((d, c) for d, c in closes if c is not None and c > 0)
        for sym, closes in closes_by_symbol.items()
    }
    by_date: dict[str, dict[str, float]] = {}
    for sym, closes in series.items():
        for d, c in closes:
            by_date.setdefault(d, {})[sym] = c
    if not by_date:
        return [], {}

    last_close: dict[str, float] = {}
    index = 1.0
    points: list[tuple[str, float]] = []
    for d in sorted(by_date):
        rets = []
        for sym, close in by_date[d].items():
            prev = last_close.get(sym)
            if prev is not None:
                rets.append(close / prev - 1.0)
            last_close[sym] = close
        if rets:
            index *= 1.0 + sum(rets) / len(rets)
        points.append((d, (index - 1.0) * 100.0))

    per_symbol = {
        sym: (closes[-1][1] / closes[0][1] - 1.0) * 100.0
        for sym, closes in series.items()
        if len(closes) >= 2
    }
    return points, per_symbol
