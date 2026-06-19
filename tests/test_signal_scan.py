"""Signal scan: merged portfolio + indicator facts and periodic evaluation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai.rules.engine import evaluate_condition, reset_rule_transition_state
from db.ai_tables import create_rule, delete_rule
from signals.indicators import Bar
from signals.scan import build_scan_facts, run_signal_scan


def _bars_from_closes(close_series: list[float]) -> list[Bar]:
    return [
        {"o": c, "h": c + 1.0, "l": c - 1.0, "c": c, "v": 1000.0}
        for c in close_series
    ]


def test_build_scan_facts_merges_portfolio_and_indicator_facts() -> None:
    symbol = "NVDA"
    close_series = [100.0] * 200 + [105.0, 95.0]
    snapshot = {
        "broker": "alpaca",
        "account": {
            "portfolio_value": 50_000.0,
            "cash": 5_000.0,
            "buying_power": 10_000.0,
        },
        "positions": [
            {
                "symbol": symbol,
                "market_value": 10_000.0,
                "unrealized_pl": 250.0,
                "current_price": 95.0,
            }
        ],
        "open_orders": [],
        "analytics": {},
    }
    symbol_bars = {symbol: _bars_from_closes(close_series)}

    facts = build_scan_facts(snapshot, symbol_bars)

    assert facts["portfolio_value"] == pytest.approx(50_000.0)
    assert facts[f"position_{symbol}_market_value"] == pytest.approx(10_000.0)
    assert facts[f"{symbol}_cross_below_sma_200"] is True

    condition = {
        "fact": f"{symbol}_cross_below_sma_200",
        "operator": "equal",
        "value": True,
    }
    assert evaluate_condition(condition, facts) is True


def test_build_scan_facts_indicator_only_when_no_snapshot() -> None:
    symbol = "AAPL"
    close_series = [100.0] * 200 + [105.0, 95.0]
    facts = build_scan_facts(None, {symbol: _bars_from_closes(close_series)})

    assert "portfolio_value" not in facts
    assert facts[f"{symbol}_cross_below_sma_200"] is True


@pytest.mark.asyncio
async def test_run_signal_scan_returns_empty_without_enabled_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("signals.scan.list_rules", lambda: [])

    assert await run_signal_scan() == []


@pytest.mark.asyncio
async def test_run_signal_scan_triggers_indicator_rule(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import db.ai_tables as ai_tables

    db = tmp_path / "scan_rules.db"
    monkeypatch.setattr(ai_tables, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(
        "ai.rules.engine.list_rules", lambda: ai_tables.list_rules(db_path=db)
    )
    monkeypatch.setattr(
        "ai.rules.engine.record_rule_event",
        lambda **kwargs: ai_tables.record_rule_event(db_path=db, **kwargs),
    )
    monkeypatch.setattr(
        "ai.rules.engine.get_last_rule_event",
        lambda rule_id: ai_tables.get_last_rule_event(rule_id, db_path=db),
    )
    monkeypatch.setattr(
        "signals.scan.list_rules", lambda: ai_tables.list_rules(db_path=db)
    )

    reset_rule_transition_state()
    create_rule(
        rule_id="scan-sma-cross",
        name="NVDA below SMA200",
        conditions={
            "fact": "NVDA_cross_below_sma_200",
            "operator": "equal",
            "value": True,
        },
        action={"type": "alert", "message": "NVDA crossed below SMA200"},
        enabled=True,
        db_path=db,
    )

    close_series = [100.0] * 200 + [105.0, 95.0]

    class FakeBar:
        def __init__(self, close: float) -> None:
            self.open = close
            self.high = close + 1.0
            self.low = close - 1.0
            self.close = close
            self.volume = 1000.0

    broker = MagicMock()
    broker.aget_stock_bars_batch = AsyncMock(
        return_value={"NVDA": [FakeBar(c) for c in close_series]}
    )

    snapshot = {
        "broker": "alpaca",
        "account": {"portfolio_value": 1.0, "cash": 1.0, "buying_power": 1.0},
        "positions": [],
        "open_orders": [],
        "analytics": {},
    }

    monkeypatch.setattr("signals.scan.primary_broker", lambda: "alpaca")
    monkeypatch.setattr("signals.scan.broker_is_configured", lambda _name: True)
    monkeypatch.setattr("signals.scan.get_broker", lambda _name=None: broker)
    monkeypatch.setattr("signals.scan.get_watchlist", lambda: ["NVDA"])
    monkeypatch.setattr("signals.scan.get_latest_snapshot", lambda _broker: snapshot)

    triggered = await run_signal_scan()
    assert len(triggered) == 1
    assert triggered[0]["rule_id"] == "scan-sma-cross"
    assert triggered[0]["rule_name"] == "NVDA below SMA200"

    reset_rule_transition_state()
    delete_rule("scan-sma-cross", db_path=db)
