"""Composite volume profile and point-in-time profile features from daily OHLCV bars.

True volume profile requires intraday volume-at-price; we only have daily OHLCV.
Each bar's volume is distributed uniformly across its [low, high] range into price
bins spanning [min(low), max(high)] — a standard daily-bar approximation. A
1-tick bar (high == low) places all volume in the bin containing that price.
"""

from __future__ import annotations

import numpy as np

from signals.indicators import Bar, highs, lows, volumes


def build_volume_profile(
    bars: list[Bar],
    *,
    n_bins: int = 100,
    value_area_pct: float = 0.70,
) -> dict:
    """Composite profile over the given bars.

    Returns a dict with bin_edges, bin_centers, bin_volume, poc_price, vah, val,
    hvn_prices, and lvn_prices. Guards: fewer than 2 bars or zero total volume
    returns poc=last close with empty node lists.
    """
    empty_nodes: list[float] = []
    fallback_close = float(bars[-1]["c"]) if bars else 0.0

    if len(bars) < 2:
        return _empty_profile(fallback_close, n_bins, empty_nodes)

    bar_lows = np.asarray(lows(bars), dtype=float)
    bar_highs = np.asarray(highs(bars), dtype=float)
    bar_volumes = np.asarray(volumes(bars), dtype=float)

    if bar_volumes.sum() <= 0:
        return _empty_profile(fallback_close, n_bins, empty_nodes)

    price_lo = float(bar_lows.min())
    price_hi = float(bar_highs.max())
    bin_edges, bin_centers, bin_volume = _distribute_volume(
        bar_lows, bar_highs, bar_volumes, price_lo, price_hi, n_bins
    )

    total_volume = float(bin_volume.sum())
    if total_volume <= 0:
        return _empty_profile(fallback_close, n_bins, empty_nodes)

    poc_idx = int(np.argmax(bin_volume))
    poc_price = float(bin_centers[poc_idx])
    val, vah = _value_area_bounds(
        bin_edges, bin_volume, poc_idx, total_volume, value_area_pct
    )
    hvn_prices, lvn_prices = _high_low_volume_nodes(bin_volume, bin_centers)

    return {
        "bin_edges": bin_edges,
        "bin_centers": bin_centers,
        "bin_volume": bin_volume,
        "poc_price": poc_price,
        "vah": vah,
        "val": val,
        "hvn_prices": hvn_prices,
        "lvn_prices": lvn_prices,
    }


def compute_profile_features(
    bars: list[Bar],
    idx: int,
    *,
    window: int = 60,
    n_bins: int = 100,
    value_area_pct: float = 0.70,
    node_tol_pct: float = 0.25,
) -> dict:
    """Point-in-time profile features for bar ``idx`` using only ``bars[idx-window:idx]``.

    The current bar and all later bars are excluded (no look-ahead). Entry price
    features use ``bars[idx]`` (close, low). Returns sensible None/False defaults
    when ``idx < window`` or the window has no volume.
    """
    sensible = _sensible_features()
    if idx < 0 or idx >= len(bars) or idx < window:
        return sensible

    window_bars = bars[idx - window : idx]
    if not window_bars:
        return sensible

    profile = build_volume_profile(
        window_bars, n_bins=n_bins, value_area_pct=value_area_pct
    )
    bin_edges = profile["bin_edges"]
    bin_volume = profile["bin_volume"]
    poc = profile["poc_price"]
    vah = profile["vah"]
    val = profile["val"]
    hvn_prices: list[float] = profile["hvn_prices"]
    lvn_prices: list[float] = profile["lvn_prices"]

    if float(np.asarray(bin_volume).sum()) <= 0:
        return sensible

    close = float(bars[idx]["c"])

    va_width_pct = None
    if poc != 0 and vah is not None and val is not None:
        va_width_pct = (vah - val) / poc * 100.0

    dist_to_poc_pct = None
    if close != 0:
        dist_to_poc_pct = (close - poc) / close * 100.0

    in_value_area = False
    value_position = None
    below_value = False
    if vah is not None and val is not None:
        in_value_area = val <= close <= vah
        below_value = close < val
        span = vah - val
        if span > 0:
            value_position = float(np.clip((close - val) / span, 0.0, 1.0))

    at_hvn_support = _near_node(
        close, [p for p in hvn_prices if p <= close], node_tol_pct
    )
    at_lvn = _near_node(close, lvn_prices, node_tol_pct)

    window_low = min(float(b["l"]) for b in window_bars)
    is_poor_low = _is_poor_low(
        bar=bars[idx],
        window_low=window_low,
        bin_edges=bin_edges,
        bin_volume=bin_volume,
        poc_bin_volume=float(bin_volume[int(np.argmax(bin_volume))]),
    )

    return {
        "poc": poc,
        "vah": vah,
        "val": val,
        "va_width_pct": va_width_pct,
        "dist_to_poc_pct": dist_to_poc_pct,
        "in_value_area": in_value_area,
        "value_position": value_position,
        "at_hvn_support": at_hvn_support,
        "at_lvn": at_lvn,
        "below_value": below_value,
        "is_poor_low": is_poor_low,
    }


def _empty_profile(
    fallback_close: float,
    n_bins: int,
    empty_nodes: list[float],
) -> dict:
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    return {
        "bin_edges": edges,
        "bin_centers": centers,
        "bin_volume": np.zeros(n_bins, dtype=float),
        "poc_price": fallback_close,
        "vah": fallback_close,
        "val": fallback_close,
        "hvn_prices": list(empty_nodes),
        "lvn_prices": list(empty_nodes),
    }


def _distribute_volume(
    bar_lows: np.ndarray,
    bar_highs: np.ndarray,
    bar_volumes: np.ndarray,
    price_lo: float,
    price_hi: float,
    n_bins: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if price_hi <= price_lo:
        price_hi = price_lo + 1e-9

    bin_edges = np.linspace(price_lo, price_hi, n_bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
    bin_volume = np.zeros(n_bins, dtype=float)
    bin_width = (price_hi - price_lo) / n_bins

    for low, high, volume in zip(bar_lows, bar_highs, bar_volumes, strict=True):
        if volume <= 0:
            continue
        if high <= low:
            bin_idx = _price_to_bin(float(low), price_lo, bin_width, n_bins)
            bin_volume[bin_idx] += volume
            continue

        first_bin = _price_to_bin(float(low), price_lo, bin_width, n_bins)
        last_bin = _price_to_bin(float(high), price_lo, bin_width, n_bins)
        bar_range = high - low
        for bin_idx in range(first_bin, last_bin + 1):
            overlap = min(high, bin_edges[bin_idx + 1]) - max(low, bin_edges[bin_idx])
            if overlap > 0:
                bin_volume[bin_idx] += volume * overlap / bar_range

    return bin_edges, bin_centers, bin_volume


def _price_to_bin(price: float, price_lo: float, bin_width: float, n_bins: int) -> int:
    if bin_width <= 0:
        return 0
    raw = int((price - price_lo) / bin_width)
    return int(np.clip(raw, 0, n_bins - 1))


def _value_area_bounds(
    bin_edges: np.ndarray,
    bin_volume: np.ndarray,
    poc_idx: int,
    total_volume: float,
    value_area_pct: float,
) -> tuple[float, float]:
    included = {poc_idx}
    current_volume = float(bin_volume[poc_idx])
    target = total_volume * value_area_pct

    while current_volume < target:
        candidates: list[tuple[int, float]] = []
        left = min(included) - 1
        right = max(included) + 1
        if left >= 0:
            candidates.append((left, float(bin_volume[left])))
        if right < len(bin_volume):
            candidates.append((right, float(bin_volume[right])))
        if not candidates:
            break
        next_idx = max(candidates, key=lambda item: item[1])[0]
        included.add(next_idx)
        current_volume += float(bin_volume[next_idx])

    low_idx = min(included)
    high_idx = max(included)
    return float(bin_edges[low_idx]), float(bin_edges[high_idx + 1])


def _high_low_volume_nodes(
    bin_volume: np.ndarray,
    bin_centers: np.ndarray,
) -> tuple[list[float], list[float]]:
    n = len(bin_volume)
    if n < 2:
        return [], []

    mean_volume = float(bin_volume.mean())
    hvn_prices: list[float] = []
    lvn_prices: list[float] = []

    for i in range(n):
        center = float(bin_volume[i])
        left = float(bin_volume[i - 1]) if i > 0 else -1.0
        right = float(bin_volume[i + 1]) if i < n - 1 else -1.0
        is_peak = center > left and center > right
        is_trough = center < left and center < right
        if is_peak and center >= mean_volume:
            hvn_prices.append(float(bin_centers[i]))
        if is_trough and center <= mean_volume:
            lvn_prices.append(float(bin_centers[i]))

    return hvn_prices, lvn_prices


def _near_node(price: float, node_prices: list[float], tol_pct: float) -> bool:
    if price == 0:
        return False
    for node in node_prices:
        if abs(price - node) / price * 100.0 <= tol_pct:
            return True
    return False


def _volume_at_price(
    price: float,
    bin_edges: np.ndarray,
    bin_volume: np.ndarray,
) -> float:
    if price < float(bin_edges[0]) or price > float(bin_edges[-1]):
        return 0.0
    bin_idx = int(np.searchsorted(bin_edges, price, side="right") - 1)
    bin_idx = int(np.clip(bin_idx, 0, len(bin_volume) - 1))
    return float(bin_volume[bin_idx])


def _is_poor_low(
    *,
    bar: Bar,
    window_low: float,
    bin_edges: np.ndarray,
    bin_volume: np.ndarray,
    poc_bin_volume: float,
) -> bool:
    bar_low = float(bar["l"])
    if bar_low > window_low:
        return False
    if poc_bin_volume <= 0:
        return False

    at_low = _volume_at_price(bar_low, bin_edges, bin_volume)
    at_low += _current_bar_volume_at_low(bar, bar_low, bin_edges)
    return at_low < 0.20 * poc_bin_volume


def _current_bar_volume_at_low(bar: Bar, bar_low: float, bin_edges: np.ndarray) -> float:
    low = float(bar["l"])
    high = float(bar["h"])
    volume = float(bar["v"])
    if volume <= 0:
        return 0.0
    if bar_low < float(bin_edges[0]):
        return volume
    if high <= low:
        return volume
    span = high - low
    bin_idx = int(np.searchsorted(bin_edges, bar_low, side="right") - 1)
    bin_idx = int(np.clip(bin_idx, 0, len(bin_edges) - 2))
    overlap = min(high, float(bin_edges[bin_idx + 1])) - max(low, float(bin_edges[bin_idx]))
    if overlap <= 0:
        return 0.0
    return volume * overlap / span


def _sensible_features() -> dict:
    return {
        "poc": None,
        "vah": None,
        "val": None,
        "va_width_pct": None,
        "dist_to_poc_pct": None,
        "in_value_area": False,
        "value_position": None,
        "at_hvn_support": False,
        "at_lvn": False,
        "below_value": False,
        "is_poor_low": False,
    }
