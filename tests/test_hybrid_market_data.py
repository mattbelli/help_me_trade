"""HybridMarketData — per-symbol REST routing, batch partition+merge, deny-by-default.

Hermetic: children are hand-rolled fakes; no Alpaca/Schwab/network.
"""

from __future__ import annotations

import pytest

from brokers.errors import BrokerError
from brokers.factory import _create_market_data_provider
from brokers.hybrid_market_data import HybridMarketData


class _FakeChild:
    def __init__(self, name: str) -> None:
        self.name = name
        self.batch_calls: list[list[str]] = []
        self.single_calls: list[str] = []
        self.quote_calls: list[list[str]] = []
        self.ext_quote_calls: list[list[str]] = []

    async def aget_stock_bars(self, symbol, *, timeframe="1Day", days=180, extended_hours=False):
        self.single_calls.append(symbol)
        return [f"{self.name}:{symbol}"]

    async def aget_stock_bars_batch(self, symbols, *, timeframe="1Day", days=12):
        self.batch_calls.append(list(symbols))
        return {s: [f"{self.name}:{s}"] for s in symbols}

    async def aget_ext_quotes(self, symbols, *, feed=None):
        self.ext_quote_calls.append((list(symbols), feed))
        return {
            s: {"ext_last": 100.0, "ext_change_pct": 1.5}
            for s in symbols
        }

    async def aget_quotes(self, symbols):
        self.quote_calls.append(list(symbols))
        return {
            s: {
                "src": self.name,
                "pe_ratio": 25.0,
                "total_volume": 1_000_000,
            }
            for s in symbols
        }


def _hybrid() -> tuple[HybridMarketData, _FakeChild, _FakeChild]:
    alpaca = _FakeChild("alpaca")
    schwab = _FakeChild("schwab")
    return HybridMarketData(alpaca=alpaca, schwab=schwab), alpaca, schwab


@pytest.mark.asyncio
async def test_batch_partitions_and_merges() -> None:
    hybrid, alpaca, schwab = _hybrid()
    out = await hybrid.aget_stock_bars_batch(["AAPL", "SPY", "/ESU25", "/NQZ25"])

    # Each child saw ONLY its own slice.
    assert alpaca.batch_calls == [["AAPL", "SPY"]]
    assert schwab.batch_calls == [["/ESU25", "/NQZ25"]]
    # Merged result covers all four with the right provenance.
    assert set(out) == {"AAPL", "SPY", "/ESU25", "/NQZ25"}
    assert out["AAPL"] == ["alpaca:AAPL"]
    assert out["/ESU25"] == ["schwab:/ESU25"]


@pytest.mark.asyncio
async def test_batch_all_equities_never_touches_schwab() -> None:
    hybrid, alpaca, schwab = _hybrid()
    await hybrid.aget_stock_bars_batch(["AAPL", "SPY", "QQQ"])
    assert alpaca.batch_calls == [["AAPL", "SPY", "QQQ"]]
    assert schwab.batch_calls == []


@pytest.mark.asyncio
async def test_single_bar_routes_per_symbol() -> None:
    hybrid, alpaca, schwab = _hybrid()
    assert await hybrid.aget_stock_bars("SPY") == ["alpaca:SPY"]
    assert await hybrid.aget_stock_bars("/ESU25") == ["schwab:/ESU25"]
    assert alpaca.single_calls == ["SPY"]
    assert schwab.single_calls == ["/ESU25"]


@pytest.mark.asyncio
async def test_quotes_route_equities_to_alpaca_drop_futures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pin a daytime session so the ext feed is the default (None), not overnight.
    monkeypatch.setattr(
        "brokers.hybrid_market_data.market_session", lambda: "regular"
    )
    hybrid, alpaca, schwab = _hybrid()
    out = await hybrid.aget_quotes(["AAPL", "/ESU25", "SPY"])
    # Extended price from Alpaca SIP; Schwab only for supplemental fields.
    assert alpaca.ext_quote_calls == [(["AAPL", "SPY"], None)]
    assert schwab.quote_calls == [["AAPL", "SPY"]]
    assert alpaca.quote_calls == []
    assert set(out) == {"AAPL", "SPY"}
    assert out["AAPL"]["ext_last"] == 100.0
    assert out["AAPL"]["ext_change_pct"] == 1.5
    assert out["AAPL"]["pe_ratio"] == 25.0
    assert out["AAPL"]["total_volume"] == 1_000_000


@pytest.mark.asyncio
async def test_quotes_survive_schwab_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: 2026-07-08 Schwab OAuth death must not blank equity ext prices."""
    monkeypatch.setattr(
        "brokers.hybrid_market_data.market_session", lambda: "regular"
    )
    hybrid, alpaca, schwab = _hybrid()

    async def _schwab_boom(symbols):
        schwab.quote_calls.append(list(symbols))
        raise BrokerError("Schwab token stale")

    schwab.aget_quotes = _schwab_boom  # type: ignore[method-assign]

    out = await hybrid.aget_quotes(["AAPL", "MSFT"])
    assert alpaca.ext_quote_calls == [(["AAPL", "MSFT"], None)]
    assert schwab.quote_calls == [["AAPL", "MSFT"]]
    assert out["AAPL"]["ext_last"] == 100.0
    assert out["AAPL"]["ext_change_pct"] == 1.5
    assert out["MSFT"]["ext_last"] == 100.0
    assert "pe_ratio" not in out["AAPL"]  # Schwab leg failed — no supplemental merge


@pytest.mark.asyncio
async def test_quotes_only_futures_short_circuits() -> None:
    hybrid, alpaca, schwab = _hybrid()
    assert await hybrid.aget_quotes(["/ESU25", "/NQZ25"]) == {}
    assert schwab.quote_calls == []
    assert alpaca.ext_quote_calls == []


class _RaisingChild(_FakeChild):
    async def aget_stock_bars_batch(self, symbols, *, timeframe="1Day", days=12):
        raise BrokerError("Schwab WAF cooldown active")


@pytest.mark.asyncio
async def test_batch_faults_isolate_partial_serve() -> None:
    # A Schwab (futures) failure must NOT discard the already-fetched equity bars
    # or propagate — the whole watchlist would 502 + retry-storm otherwise.
    alpaca = _FakeChild("alpaca")
    schwab = _RaisingChild("schwab")
    hybrid = HybridMarketData(alpaca=alpaca, schwab=schwab)
    out = await hybrid.aget_stock_bars_batch(["AAPL", "SPY", "/ESU25"])
    assert set(out) == {"AAPL", "SPY"}  # equities served
    assert "/ESU25" not in out  # futures leg failed → skipped, not fatal
    assert out["AAPL"] == ["alpaca:AAPL"]


@pytest.mark.asyncio
async def test_batch_skips_crypto_option_never_hits_stock_endpoint() -> None:
    # crypto/option have no REST bar endpoint yet — they must be surfaced as a gap,
    # NOT silently forwarded to the Alpaca stock endpoint (which returns empty).
    hybrid, alpaca, schwab = _hybrid()
    out = await hybrid.aget_stock_bars_batch(
        ["AAPL", "BTC/USD", "AAPL240119C00190000", "/ESU25"]
    )
    assert alpaca.batch_calls == [["AAPL"]]  # only the equity, never the crypto/option
    assert schwab.batch_calls == [["/ESU25"]]
    assert set(out) == {"AAPL", "/ESU25"}


@pytest.mark.asyncio
async def test_single_crypto_returns_empty_without_touching_children() -> None:
    hybrid, alpaca, schwab = _hybrid()
    assert await hybrid.aget_stock_bars("BTC/USD") == []
    assert alpaca.single_calls == []
    assert schwab.single_calls == []


def test_deny_by_default_when_alpaca_unconfigured(monkeypatch) -> None:
    monkeypatch.setattr(
        "brokers.factory.broker_is_configured", lambda name: False
    )
    with pytest.raises(BrokerError, match="Hybrid market data requires Alpaca"):
        _create_market_data_provider("hybrid")


def test_factory_builds_hybrid_when_alpaca_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        "brokers.factory.broker_is_configured", lambda name: name == "alpaca"
    )
    provider = _create_market_data_provider("hybrid")
    assert isinstance(provider, HybridMarketData)


def test_alpaca_parse_ext_quote_snapshot_post_market() -> None:
    from brokers.alpaca import AlpacaAdapter

    adapter = AlpacaAdapter(
        base_url="https://paper-api.alpaca.markets",
        key_id="test-key",
        secret="test-secret",
    )
    snap = {
        "latestTrade": {"p": 195.5, "t": "2026-07-08T21:00:00Z"},
        "latestQuote": {"bp": 195.4, "ap": 195.6, "t": "2026-07-08T21:00:00Z"},
        "dailyBar": {
            "t": "2026-07-08T04:00:00Z",
            "o": 190.0,
            "h": 196.0,
            "l": 189.0,
            "c": 194.0,
            "v": 1_000_000,
        },
        "prevDailyBar": {
            "t": "2026-07-07T04:00:00Z",
            "o": 188.0,
            "h": 192.0,
            "l": 187.0,
            "c": 190.0,
            "v": 900_000,
        },
    }
    parsed = adapter._parse_ext_quote_snapshot(snap)
    assert parsed is not None
    assert parsed["ext_last"] == 195.5
    # Post-market move vs today's RTH close (194.0), not prev (190.0).
    assert parsed["ext_change_pct"] == round((195.5 - 194.0) / 194.0 * 100, 2)


@pytest.mark.asyncio
async def test_overnight_quotes_use_boats_feed_on_paid_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hybrid, alpaca, _schwab = _hybrid()
    monkeypatch.setattr(
        "brokers.hybrid_market_data.market_session", lambda: "overnight"
    )
    monkeypatch.setattr(
        "brokers.hybrid_market_data._overnight_quote_feed", lambda _a: "boats"
    )
    monkeypatch.setattr(
        "brokers.hybrid_market_data._overnight_delayed", lambda _a: False
    )

    await hybrid.aget_quotes(["AAPL"])

    assert alpaca.ext_quote_calls == [(["AAPL"], "boats")]


@pytest.mark.asyncio
async def test_overnight_quotes_use_overnight_feed_and_delayed_flag_on_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hybrid, alpaca, _schwab = _hybrid()
    monkeypatch.setattr(
        "brokers.hybrid_market_data.market_session", lambda: "overnight"
    )
    monkeypatch.setattr(
        "brokers.hybrid_market_data._overnight_quote_feed",
        lambda _a: "overnight",
    )
    monkeypatch.setattr(
        "brokers.hybrid_market_data._overnight_delayed", lambda _a: True
    )

    out = await hybrid.aget_quotes(["AAPL"])

    assert alpaca.ext_quote_calls == [(["AAPL"], "overnight")]
    assert out["AAPL"]["overnight_delayed"] is True


@pytest.mark.asyncio
async def test_overnight_quotes_fallback_to_default_feed_when_overnight_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hybrid, alpaca, _schwab = _hybrid()

    async def _empty_then_default(symbols, *, feed=None):
        alpaca.ext_quote_calls.append((list(symbols), feed))
        if feed == "boats":
            return {}
        return {s: {"ext_last": 99.0, "ext_change_pct": 0.5} for s in symbols}

    alpaca.aget_ext_quotes = _empty_then_default  # type: ignore[method-assign]
    monkeypatch.setattr(
        "brokers.hybrid_market_data.market_session", lambda: "overnight"
    )
    monkeypatch.setattr(
        "brokers.hybrid_market_data._overnight_quote_feed", lambda _a: "boats"
    )
    monkeypatch.setattr(
        "brokers.hybrid_market_data._overnight_delayed", lambda _a: False
    )

    out = await hybrid.aget_quotes(["AAPL"])

    assert alpaca.ext_quote_calls == [
        (["AAPL"], "boats"),
        (["AAPL"], None),
    ]
    assert out["AAPL"]["ext_last"] == 99.0


@pytest.mark.asyncio
async def test_overnight_ext_change_pct_recomputed_from_regular_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overnight the boats snapshot's dailyBar ≈ the latest trade, so aget_ext_quotes
    returns ext_change_pct ≈ 0. Hybrid must recompute the overnight move against the
    real regular close (regular_last), not pass the ~0 through."""
    monkeypatch.setattr(
        "brokers.hybrid_market_data.market_session", lambda: "overnight"
    )
    monkeypatch.setattr(
        "brokers.hybrid_market_data._overnight_quote_feed", lambda _a: "boats"
    )
    monkeypatch.setattr(
        "brokers.hybrid_market_data._overnight_delayed", lambda _a: False
    )
    hybrid, alpaca, schwab = _hybrid()

    async def _ext(symbols, *, feed=None):
        alpaca.ext_quote_calls.append((list(symbols), feed))
        return {s: {"ext_last": 100.0, "ext_change_pct": 0.0} for s in symbols}

    async def _schwab_quotes(symbols):
        schwab.quote_calls.append(list(symbols))
        return {s: {"regular_last": 98.0, "prev_close": 90.0} for s in symbols}

    alpaca.aget_ext_quotes = _ext  # type: ignore[method-assign]
    schwab.aget_quotes = _schwab_quotes  # type: ignore[method-assign]

    out = await hybrid.aget_quotes(["AAPL"])
    # Recomputed vs regular_last (98.0) — not the ~0 input, not prev_close (90.0).
    assert out["AAPL"]["ext_change_pct"] == round((100.0 - 98.0) / 98.0 * 100.0, 2)
    assert out["AAPL"]["ext_last"] == 100.0
