"""Corporate-action split factors for FIFO round-trip normalization."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Iterable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SplitEvent:
    effective_date: date
    ratio: Decimal  # new_shares / old_shares (10:1 forward split → 10)


SplitFactorsFn = Callable[[Iterable[str]], dict[str, list[SplitEvent]]]


# Curated splits — one line per symbol; ratio = new_shares / old_shares.
KNOWN_SPLITS: dict[str, list[tuple[date, Decimal]]] = {
    "NVDA": [(date(2024, 6, 10), Decimal("10"))],
}

_alpaca_cache: dict[str, list[SplitEvent]] = {}


def _known_splits_for_symbol(symbol: str) -> list[SplitEvent]:
    entries = KNOWN_SPLITS.get(symbol.upper(), [])
    return [SplitEvent(effective_date=d, ratio=r) for d, r in entries]


def _merge_split_lists(
    primary: list[SplitEvent],
    supplemental: list[SplitEvent],
) -> list[SplitEvent]:
    merged: dict[date, Decimal] = {s.effective_date: s.ratio for s in primary}
    for split in supplemental:
        if split.effective_date not in merged:
            merged[split.effective_date] = split.ratio
    return sorted(
        [SplitEvent(d, r) for d, r in merged.items()],
        key=lambda s: s.effective_date,
    )


def _fetch_alpaca_splits(symbols: list[str]) -> dict[str, list[SplitEvent]]:
    """Fetch forward splits from Alpaca; returns empty dict on any failure."""
    uncached = [s for s in symbols if s.upper() not in _alpaca_cache]
    if uncached:
        try:
            from brokers.alpaca import AlpacaAdapter

            adapter = AlpacaAdapter.from_env()
            fetched = adapter.get_forward_splits(uncached)
            for sym in uncached:
                tuples = fetched.get(sym.upper(), [])
                _alpaca_cache[sym.upper()] = [
                    SplitEvent(effective_date=d, ratio=r) for d, r in tuples
                ]
        except Exception:
            logger.debug("Alpaca forward-split fetch skipped or failed", exc_info=True)
            for sym in uncached:
                _alpaca_cache[sym.upper()] = []

    return {sym.upper(): list(_alpaca_cache.get(sym.upper(), [])) for sym in symbols}


def get_split_factors(symbols: Iterable[str]) -> dict[str, list[SplitEvent]]:
    """Return split events per symbol (curated table merged with optional Alpaca data)."""
    unique = sorted({s.upper() for s in symbols if s})
    if not unique:
        return {}

    alpaca = _fetch_alpaca_splits(unique)
    out: dict[str, list[SplitEvent]] = {}
    for sym in unique:
        known = _known_splits_for_symbol(sym)
        remote = alpaca.get(sym, [])
        merged = _merge_split_lists(known, remote)
        if merged:
            out[sym] = merged
    return out


def cumulative_split_ratio(
    splits: list[SplitEvent],
    event_date: date,
) -> Decimal:
    """Compose ratios for all splits effective after event_date."""
    ratio = Decimal("1")
    for split in splits:
        if event_date < split.effective_date:
            ratio *= split.ratio
    return ratio
