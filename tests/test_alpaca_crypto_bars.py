"""Alpaca crypto bars (v1beta3/crypto/us) — parsing, paging, params, search.

Hermetic: `_aget_data` / `_get` / `_aget` are stubbed; no network. Pins the two
measured facts the endpoint differs on — the payload is ALWAYS keyed by symbol
(even for one pair), and `feed`/`adjustment` are rejected query parameters — plus
the fence that keeps crypto out of `symbol_exists` (RiskEngine's tradability gate).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from brokers.alpaca import AlpacaAdapter


def _bar(t: str, c: float) -> dict:
    return {"t": t, "o": c, "h": c, "l": c, "c": c, "v": 10.0, "n": 5, "vw": c}


def _adapter() -> AlpacaAdapter:
    return AlpacaAdapter(
        base_url="https://paper-api.alpaca.markets",
        key_id="test-key",
        secret="test-secret",
    )


def _stub_pages(adapter: AlpacaAdapter, pages: list[dict]) -> list[dict]:
    """Serve *pages* in order from `_aget_data`; record every params dict."""
    seen: list[dict] = []
    queue = list(pages)

    async def _aget_data(path, *, params=None):
        seen.append({"path": path, **(params or {})})
        return queue.pop(0)

    adapter._aget_data = _aget_data  # type: ignore[method-assign]
    return seen


@pytest.mark.asyncio
async def test_range_parses_symbol_keyed_payload_and_pages() -> None:
    adapter = _adapter()
    seen = _stub_pages(
        adapter,
        [
            {
                "bars": {"BTC/USD": [_bar("2026-08-20T00:00:00Z", 100.0)]},
                "next_page_token": "tok",
            },
            {"bars": {"BTC/USD": [_bar("2026-08-21T00:00:00Z", 110.0)]}, "next_page_token": None},
        ],
    )
    bars = await adapter.aget_crypto_bars_range(
        "btc/usd",
        timeframe="1Day",
        start=datetime(2026, 8, 20, tzinfo=UTC),
        end=datetime(2026, 8, 21, tzinfo=UTC),
    )
    assert [b.close for b in bars] == [100.0, 110.0]
    assert seen[0]["path"] == "/v1beta3/crypto/us/bars"
    assert seen[0]["symbols"] == "BTC/USD"  # symbols=, never a path segment
    assert seen[0]["sort"] == "asc"
    assert seen[1]["page_token"] == "tok"
    for call in seen:
        assert "feed" not in call and "adjustment" not in call


@pytest.mark.asyncio
async def test_range_is_fail_soft() -> None:
    adapter = _adapter()

    async def _boom(path, *, params=None):
        raise RuntimeError("upstream down")

    adapter._aget_data = _boom  # type: ignore[method-assign]
    assert (
        await adapter.aget_crypto_bars_range(
            "BTC/USD",
            timeframe="1Day",
            start=datetime(2026, 8, 20, tzinfo=UTC),
            end=datetime(2026, 8, 21, tzinfo=UTC),
        )
        == []
    )


@pytest.mark.asyncio
async def test_range_unknown_symbol_returns_empty() -> None:
    adapter = _adapter()
    _stub_pages(adapter, [{"bars": {}, "next_page_token": None}])
    assert (
        await adapter.aget_crypto_bars_range(
            "NOPE/USD",
            timeframe="1Day",
            start=datetime(2026, 8, 20, tzinfo=UTC),
            end=datetime(2026, 8, 21, tzinfo=UTC),
        )
        == []
    )


@pytest.mark.asyncio
async def test_latest_is_newest_first_reversed_to_ascending() -> None:
    adapter = _adapter()
    seen = _stub_pages(
        adapter,
        [
            {
                "bars": {
                    "BTC/USD": [
                        _bar("2026-08-21T00:02:00Z", 3.0),
                        _bar("2026-08-21T00:01:00Z", 2.0),
                        _bar("2026-08-21T00:00:00Z", 1.0),
                    ]
                },
                "next_page_token": None,
            }
        ],
    )
    bars, has_more = await adapter.aget_crypto_bars_latest(
        "BTC/USD", timeframe="1Min", limit=10
    )
    assert [b.close for b in bars] == [1.0, 2.0, 3.0]
    assert has_more is False
    assert seen[0]["sort"] == "desc"
    assert seen[0]["start"] == "2016-01-01T00:00:00Z"  # a start is mandatory
    assert seen[0]["limit"] == 10
    assert "end" not in seen[0]
    assert "feed" not in seen[0] and "adjustment" not in seen[0]


@pytest.mark.asyncio
async def test_latest_reports_has_more_when_a_page_token_remains() -> None:
    adapter = _adapter()
    _stub_pages(
        adapter,
        [{"bars": {"BTC/USD": [_bar("2026-08-21T00:00:00Z", 1.0)]}, "next_page_token": "tok"}],
    )
    _bars, has_more = await adapter.aget_crypto_bars_latest(
        "BTC/USD", timeframe="1Min", limit=300
    )
    assert has_more is True  # _LATEST_MAX_PAGES exhausted with history left


@pytest.mark.asyncio
async def test_latest_before_steps_back_one_second() -> None:
    adapter = _adapter()
    seen = _stub_pages(adapter, [{"bars": {}, "next_page_token": None}])
    await adapter.aget_crypto_bars_latest(
        "BTC/USD",
        timeframe="1Min",
        limit=50,
        before=datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC),
    )
    assert seen[0]["end"] == "2026-08-21T11:59:59Z"


@pytest.mark.asyncio
async def test_batch_returns_one_series_per_pair() -> None:
    adapter = _adapter()
    seen = _stub_pages(
        adapter,
        [
            {
                "bars": {
                    "BTC/USD": [_bar("2026-08-21T00:00:00Z", 100.0)],
                    "ETH/USD": [
                        _bar("2026-08-20T00:00:00Z", 9.0),
                        _bar("2026-08-21T00:00:00Z", 10.0),
                    ],
                },
                "next_page_token": None,
            }
        ],
    )
    out = await adapter.aget_crypto_bars_batch(
        ["BTC/USD", "eth/usd", "BTC/USD"], timeframe="1Day", days=12
    )
    assert {k: len(v) for k, v in out.items()} == {"BTC/USD": 1, "ETH/USD": 2}
    assert seen[0]["symbols"] == "BTC/USD,ETH/USD"  # deduped, upper-cased
    assert "feed" not in seen[0] and "adjustment" not in seen[0]


@pytest.mark.asyncio
async def test_batch_is_fail_soft() -> None:
    adapter = _adapter()

    async def _boom(path, *, params=None):
        raise RuntimeError("upstream down")

    adapter._aget_data = _boom  # type: ignore[method-assign]
    assert await adapter.aget_crypto_bars_batch(["BTC/USD"]) == {}
    assert await adapter.aget_crypto_bars_batch([]) == {}


# ---------------------------------------------------------------------------
# Search surfaces crypto; symbol_exists must NOT (RiskEngine tradability gate)
# ---------------------------------------------------------------------------

_CRYPTO_ASSETS = [
    {"symbol": "BTC/USD"},
    {"symbol": "BTC/USDC"},
    {"symbol": "BTC/USDT"},
    {"symbol": "ETH/USD"},
]


def _search_adapter(monkeypatch, equities: list[str]) -> AlpacaAdapter:
    monkeypatch.setattr(AlpacaAdapter, "_assets_cache", None)
    monkeypatch.setattr(AlpacaAdapter, "_crypto_cache", None)
    adapter = _adapter()

    def _get(path: str):
        if "asset_class=crypto" in path:
            return list(_CRYPTO_ASSETS)
        if "asset_class=us_equity" in path:
            return [{"symbol": s, "tradable": True} for s in equities]
        raise AssertionError(f"unexpected GET {path}")

    adapter._get = _get  # type: ignore[method-assign]
    return adapter


def test_search_surfaces_crypto_pairs(monkeypatch) -> None:
    adapter = _search_adapter(monkeypatch, ["AAPL", "MSFT"])
    assert adapter.search_symbols("BTC", limit=5) == [
        "BTC/USD",
        "BTC/USDC",
        "BTC/USDT",
    ]


def test_search_puts_equities_first(monkeypatch) -> None:
    adapter = _search_adapter(monkeypatch, ["BTCS", "AAPL"])
    assert adapter.search_symbols("BTC", limit=5) == [
        "BTCS",
        "BTC/USD",
        "BTC/USDC",
        "BTC/USDT",
    ]


def test_search_reserves_slots_so_an_equity_prefix_storm_cannot_hide_crypto(
    monkeypatch,
) -> None:
    """Measured live 2026-08-21: BTC, BTCC, BTCI, BTCK, BTCL, BTCO, BTCS, BTCT all
    prefix-match "BTC" and filled the whole 8-row window — BTC/USD was unreachable."""
    storm = ["BTC", "BTCC", "BTCI", "BTCK", "BTCL", "BTCO", "BTCS", "BTCT"]
    adapter = _search_adapter(monkeypatch, storm)
    out = adapter.search_symbols("BTC", limit=8)
    assert len(out) == 8
    assert out[:5] == storm[:5]  # equities still lead
    assert out[5:] == ["BTC/USD", "BTC/USDC", "BTC/USDT"]


def test_search_reservation_never_overruns_a_tiny_window(monkeypatch) -> None:
    adapter = _search_adapter(monkeypatch, ["BTC", "BTCC"])
    assert adapter.search_symbols("BTC", limit=1) == ["BTC/USD"]
    assert adapter.search_symbols("BTC", limit=2) == ["BTC/USD", "BTC/USDC"]


def test_search_without_crypto_matches_is_unchanged(monkeypatch) -> None:
    adapter = _search_adapter(monkeypatch, ["AAPL", "AAPLX"])
    assert adapter.search_symbols("AAPL", limit=8) == ["AAPL", "AAPLX"]


def test_search_crypto_lookup_is_fail_soft(monkeypatch) -> None:
    monkeypatch.setattr(AlpacaAdapter, "_assets_cache", None)
    monkeypatch.setattr(AlpacaAdapter, "_crypto_cache", None)
    adapter = _adapter()

    def _get(path: str):
        if "asset_class=crypto" in path:
            raise RuntimeError("assets endpoint down")
        return [{"symbol": "AAPL", "tradable": True}]

    adapter._get = _get  # type: ignore[method-assign]
    assert adapter.search_symbols("AAPL", limit=5) == ["AAPL"]


@pytest.mark.asyncio
async def test_async_search_surfaces_crypto_pairs(monkeypatch) -> None:
    monkeypatch.setattr(AlpacaAdapter, "_assets_cache", None)
    monkeypatch.setattr(AlpacaAdapter, "_crypto_cache", None)
    adapter = _adapter()

    async def _aget(path: str, *, params=None):
        if "asset_class=crypto" in path:
            return list(_CRYPTO_ASSETS)
        return [{"symbol": "AAPL", "tradable": True}]

    adapter._aget = _aget  # type: ignore[method-assign]
    assert await adapter.asearch_symbols("ETH", limit=5) == ["ETH/USD"]


def test_symbol_exists_stays_false_for_crypto(monkeypatch) -> None:
    """RiskEngine gates orders on this — a crypto pair must never read tradable.

    Measured 2026-08-21: `/v2/assets/BTC/USD` DOES resolve, with tradable=true and
    the canonical symbol — so the upstream answer alone would admit crypto orders.
    The stub below returns exactly that live payload; the guard is what denies it.
    """
    adapter = _adapter()
    calls: list[str] = []

    def _get(path: str):
        calls.append(path)
        return {
            "symbol": "BTC/USD",
            "class": "crypto",
            "tradable": True,
            "status": "active",
            "exchange": "CRYPTO",
        }

    adapter._get = _get  # type: ignore[method-assign]
    assert adapter.symbol_exists("BTC/USD") is False
    assert calls == []  # denied before the round trip


def test_symbol_exists_still_accepts_equities(monkeypatch) -> None:
    adapter = _adapter()
    adapter._get = lambda path: {"symbol": "AAPL", "tradable": True}  # type: ignore[method-assign]
    assert adapter.symbol_exists("AAPL") is True
