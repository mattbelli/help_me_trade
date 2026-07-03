"""Tests for first-party research data sources and specialist wiring."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from ai.research.data_sources import (
    fetch_calibration_source,
    fetch_earnings_source,
    fetch_fundamentals_source,
    fetch_market_regime_source,
    fetch_news_source,
    fetch_technicals_source,
)
from ai.research.source_text import source_for_analysis
from ai.research.specialists import SPECIALIST_ROSTER, run_specialist
from config.research_engine import resolve_effort


def _make_bars(n: int = 300, *, base: float = 100.0) -> list[MagicMock]:
    bars: list[MagicMock] = []
    for i in range(n):
        close = base + i * 0.1
        bar = MagicMock()
        bar.time = f"2025-01-{(i % 28) + 1:02d}"
        bar.open = close - 0.5
        bar.high = close + 1.0
        bar.low = close - 1.0
        bar.close = close
        bar.volume = 1_000_000 + i * 100
        bars.append(bar)
    return bars


class TestFetcherFailSoft:
    def test_fundamentals_unconfigured_finnhub(self) -> None:
        with patch("ai.research.data_sources.finnhub_is_configured", return_value=False):
            assert fetch_fundamentals_source("AAPL") is None

    def test_news_unconfigured_finnhub(self) -> None:
        with patch("ai.research.data_sources.finnhub_is_configured", return_value=False):
            assert fetch_news_source("AAPL") is None

    def test_market_regime_empty(self) -> None:
        with patch("ai.research.data_sources.market_regime", return_value=None):
            assert fetch_market_regime_source() is None

    def test_calibration_empty(self) -> None:
        with patch(
            "ai.research.data_sources.compute_calibration",
            return_value={"n_resolved": 0, "by_strategy": [], "by_bucket": []},
        ):
            assert fetch_calibration_source() is None

    def test_technicals_exception_returns_none(self) -> None:
        with patch(
            "ai.research.data_sources._fetch_bars",
            side_effect=RuntimeError("boom"),
        ):
            assert fetch_technicals_source("AAPL") is None

    def test_earnings_exception_returns_none(self) -> None:
        with patch(
            "ai.research.data_sources.next_earnings_date",
            side_effect=RuntimeError("boom"),
        ):
            assert fetch_earnings_source("AAPL") is None


class TestFetchTechnicalsSource:
    def test_computed_keys_present(self) -> None:
        bars = _make_bars(300)
        mock_broker = MagicMock()
        mock_broker.get_stock_bars.return_value = bars

        with (
            patch("ai.research.data_sources.broker_is_configured", return_value=True),
            patch("ai.research.data_sources.get_broker", return_value=mock_broker),
            patch("ai.research.data_sources.load_profile", return_value=None),
        ):
            source = fetch_technicals_source("SLB")

        assert source is not None
        assert source["type"] == "technicals_internal"
        content = source["content"]
        assert content["symbol"] == "SLB"
        assert content["sma50"] is not None
        assert content["sma200"] is not None
        assert content["rsi14"] is not None
        assert content["atr14"] is not None
        assert content["levels"] is not None
        assert 0 <= content["rsi14"] <= 100
        assert content["latest_close"] > 0


class TestSpecialistDataSourceMapping:
    @pytest.fixture
    def patch_llm(self, monkeypatch: pytest.MonkeyPatch):
        import json

        from ai.llm.base import LLMMessage

        class MiniLLM:
            def complete(self, messages: list[LLMMessage], *, max_tokens: int = 1024) -> str:
                return json.dumps(
                    {
                        "section_markdown": "Section text.",
                        "key_facts": [],
                        "gaps_remaining": [],
                    }
                )

        monkeypatch.setattr("ai.research.specialists.get_llm", lambda **k: MiniLLM())

    def test_technicals_sees_technicals_internal(
        self, patch_llm, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called: list[str] = []
        tech_source = {
            "id": "tech_1",
            "type": "technicals_internal",
            "title": "SLB technicals (internal)",
            "url": None,
            "snippet": "Close $100",
            "content": {"sma50": 99.0},
        }

        def _track(name: str):
            def _fetcher(symbol: str | None = None):
                called.append(name)
                if name == "technicals":
                    return tech_source
                return None

            return _fetcher

        registry = {
            "technicals": _track("technicals"),
            "bars": _track("bars"),
            "earnings": _track("earnings"),
            "fundamentals": _track("fundamentals"),
            "news": _track("news"),
            "market_regime": _track("market_regime"),
            "calibration": _track("calibration"),
            "portfolio": _track("portfolio"),
        }
        monkeypatch.setattr(
            "ai.research.specialists._DATA_SOURCE_REGISTRY",
            registry,
        )
        monkeypatch.setattr(
            "ai.research.specialists.web_search",
            lambda query, **kwargs: [],
        )
        monkeypatch.setattr("ai.research.specialists.fetch_memory_sources", lambda *a, **k: [])

        spec = next(s for s in SPECIALIST_ROSTER if s.key == "technicals")
        effort = resolve_effort("quick")
        result = run_specialist(
            spec,
            topic="SLB outlook",
            symbol="SLB",
            plan_directive="Analyze technicals",
            seed_queries=["SLB technical analysis"],
            effort=effort,
            specialist_count=effort.specialist_count,
        )
        assert result.status == "ok"
        assert "technicals" in called
        assert "bars" in called
        assert "earnings" not in called
        types = {s.get("type") for s in result.sources}
        assert "technicals_internal" in types

    def test_symbol_less_research_no_symbol_fetchers(
        self, patch_llm, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        symbol_calls: list[str] = []

        def _track(name: str):
            def _fetcher(symbol: str | None = None):
                if symbol is not None:
                    symbol_calls.append(name)
                return None

            return _fetcher

        registry = {n: _track(n) for n in (
            "technicals", "bars", "earnings", "fundamentals", "news",
            "market_regime", "calibration", "portfolio",
        )}
        monkeypatch.setattr("ai.research.specialists._DATA_SOURCE_REGISTRY", registry)
        monkeypatch.setattr("ai.research.specialists.web_search", lambda *a, **k: [])
        monkeypatch.setattr("ai.research.specialists.fetch_memory_sources", lambda *a, **k: [])

        spec = SPECIALIST_ROSTER[0]  # fundamentals
        effort = resolve_effort("quick")
        result = run_specialist(
            spec,
            topic="macro outlook without ticker",
            symbol=None,
            plan_directive="Analyze",
            seed_queries=["macro outlook"],
            effort=effort,
            specialist_count=effort.specialist_count,
        )
        assert result.status == "ok"
        assert not symbol_calls


class TestSourceForAnalysisUntrusted:
    def test_web_is_untrusted(self) -> None:
        payload = source_for_analysis({"id": "w1", "type": "web", "title": "Hit"})
        assert payload["untrusted"] is True

    def test_internal_is_trusted(self) -> None:
        payload = source_for_analysis(
            {"id": "t1", "type": "technicals_internal", "title": "Technicals"}
        )
        assert payload["untrusted"] is False


class TestEarningsSource:
    def test_earnings_from_db_without_finnhub(self) -> None:
        with (
            patch("ai.research.data_sources.finnhub_is_configured", return_value=False),
            patch("ai.research.data_sources.next_earnings_date", return_value="2026-08-01"),
            patch("ai.research.data_sources.last_earnings_date", return_value="2026-05-01"),
            patch("ai.research.data_sources.earnings_hour", return_value="amc"),
        ):
            source = fetch_earnings_source("AAPL")
        assert source is not None
        assert source["type"] == "earnings_internal"
        assert source["content"]["next"] == "2026-08-01"
        assert source["content"]["eps_surprises"] == []

    def test_market_regime_source_content(self) -> None:
        with (
            patch(
                "ai.research.data_sources.market_regime",
                return_value={"term_spread": 0.3, "vix": 18.5, "as_of": date.today().isoformat()},
            ),
            patch("ai.research.data_sources.regime_is_fresh", return_value=True),
        ):
            source = fetch_market_regime_source()
        assert source is not None
        content = source["content"]
        assert "regime_label" in content
        assert content["fresh"] is True
