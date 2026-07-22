"""Route-level smoke for the aggregated watchlist-news endpoint (wiring only).

Mounts the ticker_router on a fresh FastAPI app, seeds the (test-redirected)
news store, and mocks the watchlist state so the test touches no network /
broker: it confirms the symbol union across lists, the options/futures skip
rule, story-id dedup, newest-first ordering, and symbol tagging.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.ticker_routes as ticker_routes
from db.news import init_db, upsert_news_item


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(ticker_routes.router)
    return app


# Unique symbols per test module — the suite shares one throwaway DEFAULT_DB
# (see conftest), so distinctive names keep other tests' rows out of the query.
_WATCHLISTS_STATE = {
    "active_id": "main",
    "lists": [
        {"id": "main", "name": "Main", "symbols": ["WNAAPL", "wnmsft "]},
        {"id": "swing", "name": "Swing", "symbols": ["WNMSFT", "WNTSLA", "/ES"]},
    ],
}


def _seed_store() -> None:
    init_db()
    rows = [
        {
            "id": "wn-apple-1",
            "headline": "Apple news",
            "source": "benzinga",
            "url": "https://x/apple",
            "symbols": ["WNAAPL"],
            "published_at": "2026-07-01T12:00:00Z",
        },
        {
            # One story mentioning BOTH symbols — must appear exactly once.
            "id": "wn-shared-1",
            "headline": "Shared headline",
            "source": "benzinga",
            "url": "https://x/shared",
            "symbols": ["WNAAPL", "WNMSFT"],
            "published_at": "2026-06-30T09:00:00Z",
        },
        {
            "id": "wn-msft-1",
            "headline": "Microsoft news",
            "source": "benzinga",
            "url": "https://x/msft",
            "symbols": ["WNMSFT"],
            "published_at": "2026-07-02T08:00:00Z",
        },
        {
            # Unrelated symbol — must never surface for this watchlist.
            "id": "wn-other-1",
            "headline": "Other story",
            "source": "benzinga",
            "url": "https://x/other",
            "symbols": ["WNOTHER"],
            "published_at": "2026-07-03T08:00:00Z",
        },
    ]
    for row in rows:
        upsert_news_item(row)


def test_watchlist_news_aggregates_and_dedupes(monkeypatch) -> None:
    _seed_store()
    monkeypatch.setattr(
        "config.settings.get_watchlists_state",
        lambda: _WATCHLISTS_STATE,
    )
    monkeypatch.setattr(
        "signals.news._rest_topup",
        AsyncMock(side_effect=AssertionError("store is warm — no REST call")),
    )

    resp = TestClient(_app()).get("/api/ticker/watchlist/news?limit=50")
    assert resp.status_code == 200
    data = resp.json()

    # /ES (futures-style) is skipped; union of WNAAPL/WNMSFT/WNTSLA
    # (case/space-normalized) = 3 eligible symbols.
    assert data["symbol_count"] == 3
    assert data["symbols_with_news"] == 2  # WNAAPL, WNMSFT; WNTSLA had none

    urls = [item["url"] for item in data["news"]]
    assert "https://x/other" not in urls
    assert urls.count("https://x/shared") == 1  # one story, two symbols

    # Newest-first ordering across all symbols.
    datetimes = [item["datetime"] for item in data["news"]]
    assert datetimes == sorted(datetimes, reverse=True)

    # Each item carries a watchlist symbol it mentions.
    by_url = {item["url"]: item for item in data["news"]}
    assert by_url["https://x/apple"]["symbol"] == "WNAAPL"
    assert by_url["https://x/msft"]["symbol"] == "WNMSFT"
    assert by_url["https://x/shared"]["symbol"] in ("WNAAPL", "WNMSFT")


def test_watchlist_news_empty_watchlists(monkeypatch) -> None:
    monkeypatch.setattr(
        "config.settings.get_watchlists_state",
        lambda: {"active_id": "main", "lists": [{"id": "main", "name": "Main", "symbols": []}]},
    )
    monkeypatch.setattr(
        "signals.news._rest_topup",
        AsyncMock(side_effect=AssertionError("should not be called")),
    )

    resp = TestClient(_app()).get("/api/ticker/watchlist/news")
    assert resp.status_code == 200
    assert resp.json() == {"news": [], "symbol_count": 0, "symbols_with_news": 0}


def test_news_scope_all_is_unfiltered(monkeypatch) -> None:
    """scope=all serves the whole store — including non-watchlist symbols."""
    _seed_store()
    monkeypatch.setattr(
        "config.settings.get_watchlists_state",
        lambda: _WATCHLISTS_STATE,
    )
    monkeypatch.setattr(
        "signals.news._rest_topup",
        AsyncMock(side_effect=AssertionError("store is warm — no REST call")),
    )

    resp = TestClient(_app()).get("/api/ticker/watchlist/news?scope=all&limit=250")
    assert resp.status_code == 200
    urls = [item["url"] for item in resp.json()["news"]]
    assert "https://x/other" in urls  # WNOTHER is off-watchlist but in scope
    # Tag = first symbol on the story, whatever it is.
    by_url = {item["url"]: item for item in resp.json()["news"]}
    assert by_url["https://x/other"]["symbol"] == "WNOTHER"


def test_news_scope_sp500_filters_to_universe(monkeypatch) -> None:
    """scope=sp500 keeps only stories mentioning a universe symbol, tags in-set."""
    _seed_store()
    monkeypatch.setattr(
        "config.settings.get_watchlists_state",
        lambda: _WATCHLISTS_STATE,
    )
    monkeypatch.setattr(
        "signals.strategy_universe.resolve_universe_spec",
        lambda spec: ["WNOTHER", "WNMSFT"] if spec == "sp500" else [],
    )
    monkeypatch.setattr(
        "signals.news._rest_topup",
        AsyncMock(side_effect=AssertionError("sp500 scope never tops up")),
    )

    resp = TestClient(_app()).get("/api/ticker/watchlist/news?scope=sp500&limit=250")
    assert resp.status_code == 200
    news = resp.json()["news"]
    urls = {item["url"] for item in news}
    assert urls == {"https://x/other", "https://x/msft", "https://x/shared"}
    by_url = {item["url"]: item for item in news}
    # The shared story mentions WNAAPL first — the tag must be the IN-SET one.
    assert by_url["https://x/shared"]["symbol"] == "WNMSFT"


def test_news_scope_unknown_falls_back_to_watchlist(monkeypatch) -> None:
    _seed_store()
    monkeypatch.setattr(
        "config.settings.get_watchlists_state",
        lambda: _WATCHLISTS_STATE,
    )
    monkeypatch.setattr("signals.news._rest_topup", AsyncMock())

    resp = TestClient(_app()).get("/api/ticker/watchlist/news?scope=bogus")
    assert resp.status_code == 200
    urls = [item["url"] for item in resp.json()["news"]]
    assert "https://x/other" not in urls  # watchlist behavior, not firehose
