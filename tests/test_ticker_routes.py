"""Route-level smoke for the per-symbol company-info endpoint (wiring + aggregation).

Mounts the ticker_router on a fresh FastAPI app and mocks every source so the test
touches no network / broker: it confirms the five sources are aggregated into one
fail-soft payload and that a failed source degrades to an empty section (never 500).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.ticker_routes as ticker_routes


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(ticker_routes.router)
    return app


def test_ticker_info_aggregates_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        "signals.finnhub.afetch_company_profile",
        AsyncMock(return_value={"name": "Apple Inc", "exchange": "NASDAQ"}),
    )
    monkeypatch.setattr(
        "signals.finnhub.afetch_company_metrics",
        AsyncMock(return_value={"pe_ttm": 28.5}),
    )
    monkeypatch.setattr(
        "signals.finnhub.afetch_company_news",
        AsyncMock(return_value=[{"headline": "News", "url": "https://x"}]),
    )
    monkeypatch.setattr(
        "ai.perception.context.build_symbol_context",
        AsyncMock(return_value={"symbol": "AAPL", "quote": {"price": 190.0}}),
    )
    monkeypatch.setattr(
        "signals.finra.afetch_short_interest",
        AsyncMock(return_value={"short_shares": 1000.0, "days_to_cover": 2.1}),
    )

    resp = TestClient(_app()).get("/api/ticker/aapl")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "AAPL"  # normalized upper
    assert data["profile"]["name"] == "Apple Inc"
    assert data["metrics"]["pe_ttm"] == 28.5
    assert data["news"][0]["headline"] == "News"
    assert data["facts"]["quote"]["price"] == 190.0
    assert data["short_interest"]["days_to_cover"] == 2.1


def test_ticker_info_fail_soft_when_sources_empty(monkeypatch) -> None:
    """A missing Finnhub key / failed facts → empty sections, still a 200."""
    monkeypatch.setattr(
        "signals.finnhub.afetch_company_profile", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        "signals.finnhub.afetch_company_metrics", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        "signals.finnhub.afetch_company_news", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "ai.perception.context.build_symbol_context",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr(
        "signals.finra.afetch_short_interest", AsyncMock(return_value={})
    )

    resp = TestClient(_app()).get("/api/ticker/MSFT")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "symbol": "MSFT",
        "profile": {},
        "metrics": {},
        "news": [],
        "facts": {},  # facts failure swallowed → empty, not a 500
        "short_interest": {},
    }
