"""Perception layer — symbol context builder."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai.perception.context import build_symbol_context, list_capabilities
from brokers.alpaca import StockBar
from db import base as db_base
from db.chart_features import create_price_alert, init_chart_features
from db.earnings_calendar import upsert_earnings


@pytest.fixture(autouse=True)
def _reset_spy_bars_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SPY-bars TTL cache is module-global — never let it leak across tests."""
    monkeypatch.setattr("ai.perception.context._spy_bars_cache", None)


def _daily_bar(day: int, close: float) -> StockBar:
    return StockBar(
        time=f"2026-01-{day:02d}T21:00:00Z",
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=1_000_000.0,
    )


def _intraday_bar(day: int, hour: int, minute: int, volume: float = 50_000.0) -> StockBar:
    return StockBar(
        time=f"2026-01-{day:02d}T{hour:02d}:{minute:02d}:00-05:00",
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=volume,
    )


@pytest.fixture
def perception_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "perception.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.chart_features.DEFAULT_DB_PATH", db)
    init_chart_features(db)
    upsert_earnings(
        [
            {"symbol": "NVDA", "date": "2026-01-28"},
            {"symbol": "NVDA", "date": "2026-05-28"},
        ],
        db_path=db,
    )
    create_price_alert(
        symbol="NVDA",
        price=900.0,
        direction="cross",
        note="test alert",
        repeat=True,
    )
    return db


def test_list_capabilities_manifest() -> None:
    caps = list_capabilities()
    assert "symbol" in caps["context_scopes"]
    assert "quote" in caps["context_scopes"]["symbol"]
    assert "news" in caps["context_scopes"]["symbol"]
    assert "strategy_signals" in caps["context_scopes"]["symbol"]
    assert "active_alerts" in caps["context_scopes"]["symbol"]
    assert "watchlists" in caps["resources"]
    assert "screener" in caps["resources"]
    assert "market" in caps["resources"]
    assert "calibration" in caps["resources"]
    assert "propose_order" in caps["write_actions"]
    assert "create_rule" in caps["write_actions"]
    assert "query" in caps["notes"].lower()


@pytest.mark.asyncio
async def test_build_symbol_context_assembles_blocks(
    monkeypatch: pytest.MonkeyPatch,
    perception_db: Path,
) -> None:
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", perception_db)
    monkeypatch.setattr(
        "db.chart_features.DEFAULT_DB_PATH",
        perception_db,
    )

    daily = [_daily_bar(d, 100.0 + d) for d in range(1, 22)]
    intraday = [_intraday_bar(21, 10, 30)]

    provider = MagicMock()
    provider.aget_stock_bars_batch = AsyncMock(
        side_effect=[
            {"NVDA": daily},
            {"NVDA": intraday},
        ]
    )
    provider.aget_stock_bars = AsyncMock(return_value=daily * 12)
    provider.aget_quotes = AsyncMock(
        return_value={
            "NVDA": {
                "pe_ratio": 45.2,
                "eps": 2.5,
                "div_yield": 0.03,
                "shares_outstanding": 2_500_000_000,
            }
        }
    )

    monkeypatch.setattr(
        "ai.perception.context.get_market_data_provider",
        lambda: provider,
    )
    monkeypatch.setattr(
        "ai.perception.context.compute_session_levels",
        AsyncMock(
            return_value={
                "symbol": "NVDA",
                "session_date": "2026-01-20",
                "levels": [
                    {"name": "POC", "price": 880.0},
                    {"name": "VAH", "price": 890.0},
                    {"name": "VAL", "price": 870.0},
                    {"name": "PDH", "price": 895.0},
                    {"name": "PDL", "price": 865.0},
                ],
            }
        ),
    )
    monkeypatch.setattr(
        "ai.perception.context.load_profile",
        lambda _sym: None,
    )
    monkeypatch.setattr(
        "signals.decision_scorer.score_symbol",
        lambda *_a, **_k: {
            "win_prob": 0.62,
            "ev_pct": 1.8,
            "ci_low": 0.55,
            "ci_high": 0.69,
            "top_factors": [{"name": "rsi_14", "direction": "bullish"}],
        },
    )

    class FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return cls(2026, 3, 15)

    monkeypatch.setattr("ai.perception.context.date", FixedDate)

    ctx = await build_symbol_context("nvda", db_path=perception_db)

    assert ctx["symbol"] == "NVDA"
    assert ctx["quote"]["price"] == daily[-1].close
    assert ctx["session_nodes"]["poc"] == 880.0
    assert ctx["technicals"]["sma50"] is not None
    assert ctx["fundamentals"]["pe"] == 45.2
    assert ctx["earnings"]["next"] == "2026-05-28"
    assert ctx["decision_score"]["win_prob"] == 0.62
    assert ctx["decision_score"]["ev"] == 1.8
    assert len(ctx["active_alerts"]) == 1
    assert ctx["active_alerts"][0]["price"] == 900.0


@pytest.mark.asyncio
async def test_build_symbol_context_news_fail_soft(
    monkeypatch: pytest.MonkeyPatch,
    perception_db: Path,
) -> None:
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", perception_db)
    monkeypatch.setattr(
        "ai.perception.context.get_market_data_provider",
        MagicMock(side_effect=RuntimeError("no provider")),
    )
    monkeypatch.setattr(
        "ai.perception.context.compute_session_levels",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "signals.decision_scorer.score_symbol",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "ai.perception.context.list_price_alerts",
        lambda **_: [],
    )

    async def boom(*_a, **_k):
        raise RuntimeError("news down")

    monkeypatch.setattr("signals.news.afetch_company_news", boom)

    ctx = await build_symbol_context("NVDA", db_path=perception_db)
    assert ctx["news"] == {"unavailable": True}


@pytest.mark.asyncio
async def test_build_symbol_context_strategy_signals(
    monkeypatch: pytest.MonkeyPatch,
    perception_db: Path,
) -> None:
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", perception_db)
    monkeypatch.setattr(
        "ai.perception.context.get_market_data_provider",
        MagicMock(side_effect=RuntimeError("no provider")),
    )
    monkeypatch.setattr(
        "ai.perception.context.compute_session_levels",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "signals.decision_scorer.score_symbol",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "ai.perception.context.list_price_alerts",
        lambda **_: [],
    )
    monkeypatch.setattr(
        "signals.news.afetch_company_news",
        AsyncMock(return_value=[]),
    )

    from datetime import timedelta

    from signals.strategy_kinds import STRATEGY_KINDS

    monkeypatch.setattr("signals.strategy_kinds._dip_check", lambda _ctx: True)
    monkeypatch.setattr("signals.strategy_kinds._earnings_check", lambda _ctx: True)
    monkeypatch.setattr("signals.strategy_kinds._vix_spike_check", lambda _ctx: True)

    base = date(2025, 1, 1)
    daily = [
        StockBar(
            time=f"{(base + timedelta(days=i)).isoformat()}T21:00:00Z",
            open=99.0 + i,
            high=101.0 + i,
            low=98.0 + i,
            close=100.0 + i,
            volume=1_000_000.0,
        )
        for i in range(260)
    ]

    async def fake_batch(symbols, **kwargs):
        del kwargs
        syms = [str(s).upper() for s in symbols]
        if "SPY" in syms:
            return {"NVDA": daily, "SPY": daily}
        key = syms[0]
        return {key: daily}

    provider = MagicMock()
    provider.aget_stock_bars_batch = AsyncMock(side_effect=fake_batch)
    provider.aget_stock_bars = AsyncMock(return_value=daily)
    provider.aget_quotes = AsyncMock(return_value={})
    monkeypatch.setattr(
        "ai.perception.context.get_market_data_provider",
        lambda: provider,
    )

    ctx = await build_symbol_context("NVDA", db_path=perception_db)
    signals = ctx.get("strategy_signals")
    assert signals is not None
    assert len(signals) == len(STRATEGY_KINDS)
    assert all(isinstance(s["would_fire"], bool) for s in signals)
    assert all(s.get("rationale") for s in signals)


@pytest.mark.asyncio
async def test_build_symbol_context_fail_soft_without_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db = tmp_path / "empty_perception.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.chart_features.DEFAULT_DB_PATH", db)
    init_chart_features(db)

    monkeypatch.setattr(
        "ai.perception.context.get_market_data_provider",
        MagicMock(side_effect=RuntimeError("no provider")),
    )
    monkeypatch.setattr(
        "ai.perception.context.compute_session_levels",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "signals.decision_scorer.score_symbol",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "ai.perception.context.list_price_alerts",
        lambda **_: [],
    )

    ctx = await build_symbol_context("NVDA", db_path=db)

    assert ctx["symbol"] == "NVDA"
    assert "quote" not in ctx
    assert ctx["decision_score"] is None
    assert ctx["active_alerts"] == []
