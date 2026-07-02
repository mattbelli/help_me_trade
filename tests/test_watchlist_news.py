"""Route-level smoke for the aggregated watchlist-news endpoint (wiring only).

Mounts the ticker_router on a fresh FastAPI app and mocks the watchlist state +
Finnhub news fetch so the test touches no network / broker: it confirms the
symbol union across lists, the options/futures skip rule, url-dedup, and
newest-first ordering — never a 500 on a bad symbol.
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


_WATCHLISTS_STATE = {
    "active_id": "main",
    "lists": [
        {"id": "main", "name": "Main", "symbols": ["AAPL", "msft "]},
        {"id": "swing", "name": "Swing", "symbols": ["MSFT", "TSLA", "/ES"]},
    ],
}


async def _fake_news(symbol: str, *, days: int = 7, limit: int = 8):
    if symbol == "AAPL":
        return [
            {
                "headline": "Apple news",
                "source": "Reuters",
                "url": "https://x/apple",
                "datetime": "2026-07-01T12:00:00+00:00",
                "summary": "",
                "image": "",
            },
            {
                "headline": "Shared headline",
                "source": "Reuters",
                "url": "https://x/shared",
                "datetime": "2026-06-30T09:00:00+00:00",
                "summary": "",
                "image": "",
            },
        ]
    if symbol == "MSFT":
        return [
            {
                "headline": "Microsoft news",
                "source": "Bloomberg",
                "url": "https://x/msft",
                "datetime": "2026-07-02T08:00:00+00:00",
                "summary": "",
                "image": "",
            },
            {
                # Same url as the AAPL item above — must be deduped.
                "headline": "Shared headline",
                "source": "Reuters",
                "url": "https://x/shared",
                "datetime": "2026-06-30T09:00:00+00:00",
                "summary": "",
                "image": "",
            },
        ]
    if symbol == "TSLA":
        return []
    raise AssertionError(f"unexpected symbol fetched: {symbol}")


def test_watchlist_news_aggregates_and_dedupes(monkeypatch) -> None:
    monkeypatch.setattr(
        "config.settings.get_watchlists_state",
        lambda: _WATCHLISTS_STATE,
    )
    monkeypatch.setattr(
        "signals.finnhub.afetch_company_news",
        AsyncMock(side_effect=_fake_news),
    )

    resp = TestClient(_app()).get("/api/ticker/watchlist/news?limit=50")
    assert resp.status_code == 200
    data = resp.json()

    # /ES is a futures-style symbol and must never reach afetch_company_news
    # (AssertionError above would have surfaced as a 500 otherwise); union of
    # AAPL/MSFT/TSLA (case/space-normalized) = 3 eligible symbols scanned.
    assert data["symbol_count"] == 3
    assert data["symbols_with_news"] == 2  # AAPL, MSFT had news; TSLA had none

    urls = [item["url"] for item in data["news"]]
    assert urls.count("https://x/shared") == 1  # deduped across AAPL/MSFT

    # Newest-first ordering across all symbols.
    datetimes = [item["datetime"] for item in data["news"]]
    assert datetimes == sorted(datetimes, reverse=True)

    # Each item carries the symbol it was tagged with.
    by_url = {item["url"]: item for item in data["news"]}
    assert by_url["https://x/apple"]["symbol"] == "AAPL"
    assert by_url["https://x/msft"]["symbol"] == "MSFT"


def test_watchlist_news_empty_watchlists(monkeypatch) -> None:
    monkeypatch.setattr(
        "config.settings.get_watchlists_state",
        lambda: {"active_id": "main", "lists": [{"id": "main", "name": "Main", "symbols": []}]},
    )
    monkeypatch.setattr(
        "signals.finnhub.afetch_company_news",
        AsyncMock(side_effect=AssertionError("should not be called")),
    )

    resp = TestClient(_app()).get("/api/ticker/watchlist/news")
    assert resp.status_code == 200
    assert resp.json() == {"news": [], "symbol_count": 0, "symbols_with_news": 0}
