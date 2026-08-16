"""Shared "is Alpaca configured?" guard for the read-only Alpaca-backed routes.

Returns the live ``AlpacaAdapter`` or a ready-to-return ``JSONResponse`` error
(501 not configured / 502 broker error). Callers do ``if isinstance(x,
JSONResponse): return x``. Lives here so ``api/bars_routes``, ``api/flow_routes``
and ``api/main`` (portfolio history) share one definition without any of them
importing the others.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse

from brokers.alpaca import AlpacaAdapter
from brokers.errors import BrokerError
from brokers.factory import get_broker
from config.settings import broker_is_configured


def _require_alpaca() -> AlpacaAdapter | JSONResponse:
    if not broker_is_configured("alpaca"):
        return JSONResponse(
            {"error": "Alpaca is not configured. Add API keys in Settings."},
            status_code=501,
        )
    try:
        broker = get_broker("alpaca")
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    if not isinstance(broker, AlpacaAdapter):
        return JSONResponse({"error": "Alpaca adapter unavailable"}, status_code=501)
    return broker
