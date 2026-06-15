"""Select broker adapter from settings or environment."""

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


def active_broker_name() -> str:
    return primary_broker()


def get_enabled_broker_names() -> list[str]:
    return enabled_brokers()


def get_broker(name: str | None = None) -> BrokerAdapter:
    broker = (name or active_broker_name()).strip().lower()
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
