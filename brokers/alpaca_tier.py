"""Alpaca market-data tier detection (free IEX vs paid SIP entitlement)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from brokers.errors import BrokerError

logger = logging.getLogger(__name__)

DataTier = Literal["paid", "free"]
EffectiveFeed = Literal["sip", "iex"]

PROBE_TTL_SEC = 6 * 3600
_PROBE_PATH = "/v2/stocks/SPY/snapshot"
_PROBE_PARAMS = {"feed": "sip"}

_cache_lock = threading.Lock()
_tier_cache: _TierCacheEntry | None = None
_last_known_tier: DataTier = "free"


@dataclass(frozen=True)
class _TierCacheEntry:
    tier: DataTier
    expires_at: float


def clear_alpaca_tier_cache() -> None:
    """Reset module cache (tests only)."""
    global _tier_cache, _last_known_tier
    with _cache_lock:
        _tier_cache = None
        _last_known_tier = "free"


def _is_subscription_denied(exc: BrokerError) -> bool:
    msg = str(exc).lower()
    return "403" in str(exc) or "subscription" in msg


def _probe_sip_entitlement(get_data: Callable[..., dict]) -> DataTier | None:
    """Return tier on a definitive probe, or None when the result is ambiguous."""
    try:
        get_data(_PROBE_PATH, params=_PROBE_PARAMS)
        return "paid"
    except BrokerError as exc:
        if _is_subscription_denied(exc):
            return "free"
        logger.warning(
            "[alpaca] SIP entitlement probe inconclusive: %s",
            exc,
        )
        return None
    except Exception as exc:
        logger.warning(
            "[alpaca] SIP entitlement probe failed: %s",
            exc,
        )
        return None


def _resolve_auto_tier(get_data: Callable[..., dict]) -> DataTier:
    global _tier_cache, _last_known_tier
    now = time.monotonic()

    with _cache_lock:
        if _tier_cache is not None and now < _tier_cache.expires_at:
            return _tier_cache.tier

    probed = _probe_sip_entitlement(get_data)

    with _cache_lock:
        if probed is not None:
            _last_known_tier = probed
            _tier_cache = _TierCacheEntry(
                tier=probed,
                expires_at=now + PROBE_TTL_SEC,
            )
            feed: EffectiveFeed = "sip" if probed == "paid" else "iex"
            logger.info("[alpaca] data tier: %s (feed=%s)", probed, feed)
            return probed

        if _tier_cache is not None:
            stale_tier = _tier_cache.tier
            _tier_cache = _TierCacheEntry(
                tier=stale_tier,
                expires_at=now + PROBE_TTL_SEC,
            )
            return stale_tier
        return _last_known_tier


def resolve_data_tier(
    *,
    configured_feed: str,
    get_data: Callable[..., dict] | None = None,
) -> DataTier:
    feed = configured_feed.strip().lower()
    if feed == "sip":
        return "paid"
    if feed == "iex":
        return "free"
    if get_data is None:
        return _last_known_tier
    return _resolve_auto_tier(get_data)


def effective_alpaca_feed(
    *,
    configured_feed: str,
    get_data: Callable[..., dict] | None = None,
) -> EffectiveFeed:
    tier = resolve_data_tier(configured_feed=configured_feed, get_data=get_data)
    return "sip" if tier == "paid" else "iex"


def data_tier(adapter: object) -> DataTier:
    """Public seam for tier-aware routing (e.g. overnight/boats brief)."""
    configured = getattr(adapter, "_configured_data_feed", "auto")
    get_data = getattr(adapter, "_get_data", None)
    probe = get_data if configured == "auto" else None
    return resolve_data_tier(configured_feed=str(configured), get_data=probe)
