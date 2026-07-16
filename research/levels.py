"""Support/resistance level detection and point-in-time distance features."""

from __future__ import annotations

from signals.indicators import Bar


def swing_pivots(
    bars: list[Bar],
    idx: int,
    *,
    window: int = 5,
    lookback: int = 252,
) -> tuple[list[float], list[float]]:
    """Find swing highs/lows using only bars[max(0, idx-lookback):idx+1].

    A swing HIGH at j means bar[j]['h'] is strictly the maximum high over
    [j-window, j+window]; swing LOW is the strict minimum low. Both sides need
    `window` confirmed bars, so the most recent `window` bars cannot be pivots
    (no look-ahead).
    """
    start = max(0, idx - lookback)
    support: list[float] = []
    resistance: list[float] = []

    first_j = start + window
    last_j = idx - window
    if first_j > last_j:
        return support, resistance

    for j in range(first_j, last_j + 1):
        lo = j - window
        hi = j + window
        pivot_high = bars[j]["h"]
        pivot_low = bars[j]["l"]
        is_high = all(pivot_high > bars[k]["h"] for k in range(lo, hi + 1) if k != j)
        is_low = all(pivot_low < bars[k]["l"] for k in range(lo, hi + 1) if k != j)
        if is_high:
            resistance.append(pivot_high)
        if is_low:
            support.append(pivot_low)

    support.sort()
    resistance.sort()
    return support, resistance


def cluster_levels(prices: list[float], *, tol_pct: float = 1.0) -> list[float]:
    """Merge prices within tol_pct of each other into a single level (cluster mean)."""
    if not prices:
        return []

    sorted_prices = sorted(prices)
    clusters: list[list[float]] = [[sorted_prices[0]]]

    for price in sorted_prices[1:]:
        cluster_mean = sum(clusters[-1]) / len(clusters[-1])
        if abs(price - cluster_mean) / cluster_mean * 100.0 <= tol_pct:
            clusters[-1].append(price)
        else:
            clusters.append([price])

    return sorted(sum(cluster) / len(cluster) for cluster in clusters)


def level_features(
    bars: list[Bar],
    idx: int,
    *,
    window: int = 5,
    lookback: int = 252,
    tol_pct: float = 1.0,
    include_prices: bool = False,
) -> dict:
    """Point-in-time S/R features at bars[idx] using only history up to idx.

    With include_prices=True, adds the nearest actual level prices
    (support_prices below close, resistance_prices above) for consumers that
    need concrete levels rather than engineered features.
    """
    if idx < 0 or idx >= len(bars):
        return {}
    if idx + 1 < window * 2 + 1:
        return {}

    close = bars[idx]["c"]
    if close <= 0:
        return {}

    raw_support, raw_resistance = swing_pivots(
        bars, idx, window=window, lookback=lookback
    )
    support_levels = cluster_levels(raw_support, tol_pct=tol_pct)
    resistance_levels = cluster_levels(raw_resistance, tol_pct=tol_pct)

    features: dict = {
        "n_support": len(support_levels),
        "n_resistance": len(resistance_levels),
    }

    supports_below = [level for level in support_levels if level < close]
    resistances_above = [level for level in resistance_levels if level > close]

    nearest_support = max(supports_below) if supports_below else None
    nearest_resistance = min(resistances_above) if resistances_above else None

    if nearest_support is not None:
        dist_to_support_pct = (close - nearest_support) / close * 100.0
        features["dist_to_support_pct"] = dist_to_support_pct
        features["near_support"] = 1 if dist_to_support_pct <= tol_pct else 0

    if nearest_resistance is not None:
        dist_to_resistance_pct = (nearest_resistance - close) / close * 100.0
        features["dist_to_resistance_pct"] = dist_to_resistance_pct
        features["near_resistance"] = 1 if dist_to_resistance_pct <= tol_pct else 0

    if nearest_support is not None and nearest_resistance is not None:
        span = nearest_resistance - nearest_support
        if span > 0:
            features["range_position"] = (close - nearest_support) / span

    if include_prices:
        features["support_prices"] = [
            round(p, 2) for p in sorted(supports_below, reverse=True)[:3]
        ]
        features["resistance_prices"] = [
            round(p, 2) for p in sorted(resistances_above)[:3]
        ]

    return features
