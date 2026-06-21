"""Select broker adapter from settings or environment.

The adapter cache is keyed by broker name (one real connection per broker today).
Re-keying by ``connection_id`` happens when per-connection credentials land (Phase B).
"""

from __future__ import annotations

from brokers.alpaca import AlpacaAdapter
from brokers.base import BrokerAdapter
from brokers.errors import BrokerError
from brokers.schwab import SchwabAdapter
from config.settings import (
    broker_is_configured,
    enabled_brokers,
    primary_broker,
)

_broker_cache: dict[str, BrokerAdapter] = {}


def active_broker_name() -> str:
    return primary_broker()


def get_enabled_broker_names() -> list[str]:
    return enabled_brokers()


def clear_broker_cache() -> None:
    for broker in _broker_cache.values():
        close = getattr(broker, "close", None)
        if callable(close):
            close()
    _broker_cache.clear()


async def aclose_broker_cache() -> None:
    for broker in _broker_cache.values():
        aclose = getattr(broker, "aclose", None)
        if callable(aclose):
            await aclose()
        close = getattr(broker, "close", None)
        if callable(close):
            close()
    _broker_cache.clear()


def _create_broker(broker: str) -> BrokerAdapter:
    if broker == "alpaca":
        if not broker_is_configured("alpaca"):
            raise BrokerError(
                "Alpaca is not configured. Add API keys in Settings or .env."
            )
        return AlpacaAdapter.from_settings()
    if broker == "schwab":
        if not broker_is_configured("schwab"):
            raise BrokerError(
                "Schwab is not configured. Add app credentials in Settings or .env."
            )
        return SchwabAdapter.from_settings()
    raise BrokerError(f"Unknown broker {broker!r}. Use alpaca or schwab.")


def get_broker(name: str | None = None) -> BrokerAdapter:
    broker = (name or active_broker_name()).strip().lower()
    cached = _broker_cache.get(broker)
    if cached is not None:
        return cached
    adapter = _create_broker(broker)
    _broker_cache[broker] = adapter
    return adapter


def get_broker_for_account(account_id: str) -> BrokerAdapter:
    """Resolve an account to its broker adapter (account-aware entry point).

    Phase A sub-step 2: credentials still come from settings via the existing
    per-broker path, so an account resolves to its broker's adapter (the default
    account == today's adapter exactly). Per-CONNECTION distinct credentials
    (needed for a 2nd Alpaca key) plug in HERE when connection-management lands
    (Phase B) — that's the documented seam.
    """
    from db.accounts import get_account

    account = get_account(account_id)
    if account is None:
        raise BrokerError(f"unknown account {account_id!r}")
    broker = str(account["broker"]).strip().lower()
    return get_broker(broker)
