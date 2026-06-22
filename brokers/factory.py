"""Select broker adapter from settings or environment.

Adapters are cached by broker name (legacy single-connection path) and by
connection id (per-connection credentials for multi-account Alpaca).
"""

from __future__ import annotations

import json

from brokers.alpaca import AlpacaAdapter
from brokers.base import BrokerAdapter
from brokers.errors import BrokerError
from brokers.schwab import SchwabAdapter
from config.settings import (
    alpaca_config_for_suffix,
    broker_is_configured,
    enabled_brokers,
    primary_broker,
)

_broker_cache: dict[str, BrokerAdapter] = {}
_broker_by_connection: dict[str, BrokerAdapter] = {}


def active_broker_name() -> str:
    return primary_broker()


def get_enabled_broker_names() -> list[str]:
    return enabled_brokers()


def _close_adapters(adapters: dict[str, BrokerAdapter]) -> None:
    for broker in adapters.values():
        close = getattr(broker, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


async def _aclose_adapters(adapters: dict[str, BrokerAdapter]) -> None:
    for broker in adapters.values():
        aclose = getattr(broker, "aclose", None)
        if callable(aclose):
            try:
                await aclose()
            except Exception:
                pass
        close = getattr(broker, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def clear_broker_cache() -> None:
    _close_adapters(_broker_cache)
    _broker_cache.clear()
    _close_adapters(_broker_by_connection)
    _broker_by_connection.clear()


async def aclose_broker_cache() -> None:
    await _aclose_adapters(_broker_cache)
    _broker_cache.clear()
    await _aclose_adapters(_broker_by_connection)
    _broker_by_connection.clear()


def _connection_env_suffix(connection: dict) -> str | None:
    try:
        creds = json.loads(connection.get("credentials_json") or "{}")
    except json.JSONDecodeError:
        creds = {}
    suffix = creds.get("env_suffix")
    if suffix is None:
        return None
    text = str(suffix).strip()
    return text or None


def connection_is_configured(connection: dict) -> bool:
    broker = str(connection.get("broker", "")).strip().lower()
    if broker == "alpaca":
        cfg = alpaca_config_for_suffix(_connection_env_suffix(connection))
        return bool(
            cfg["api_key_id"] and cfg["api_secret_key"] and cfg["api_base_url"]
        )
    if broker == "schwab":
        return broker_is_configured("schwab")
    return False


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


def get_broker_for_connection(connection: dict) -> BrokerAdapter:
    connection_id = str(connection["id"])
    cached = _broker_by_connection.get(connection_id)
    if cached is not None:
        return cached

    broker = str(connection["broker"]).strip().lower()
    if broker == "alpaca":
        if not connection_is_configured(connection):
            raise BrokerError(
                f"Alpaca connection {connection_id!r} is not configured."
            )
        cfg = alpaca_config_for_suffix(_connection_env_suffix(connection))
        adapter = AlpacaAdapter.from_settings(cfg)
    elif broker == "schwab":
        if not broker_is_configured("schwab"):
            raise BrokerError(
                "Schwab is not configured. Add app credentials in Settings or .env."
            )
        adapter = SchwabAdapter.from_settings()
    else:
        raise BrokerError(f"Unknown broker {broker!r}. Use alpaca or schwab.")

    _broker_by_connection[connection_id] = adapter
    return adapter


def get_broker_for_account(account_id: str) -> BrokerAdapter:
    """Resolve an account to its broker adapter via its connection."""
    from db.accounts import get_account, get_connection

    account = get_account(account_id)
    if account is None:
        raise BrokerError(f"unknown account {account_id!r}")

    connection_id = account.get("connection_id")
    if connection_id:
        connection = get_connection(str(connection_id))
        if connection is not None:
            return get_broker_for_connection(connection)

    broker = str(account["broker"]).strip().lower()
    return get_broker(broker)
