"""Research citation enrichment and rules engine (P1)."""

from __future__ import annotations

import pytest

from ai.research.citations import enrich_report_citations
from ai.rules.engine import build_facts_from_snapshot, evaluate_rules, reset_rule_transition_state
from db.ai_tables import create_rule, delete_rule


def test_enrich_report_citations_from_findings() -> None:
    report = {
        "title": "TSLA",
        "sections": [
            {
                "heading": "Merger",
                "body": "Betting markets assign 52% probability to a Tesla SpaceX merger.",
                "citations": [],
            }
        ],
    }
    findings = [
        {
            "source_id": "web_8",
            "claim": "Betting markets assign 52% probability to merger by May 2027",
            "confidence": "medium",
        },
    ]
    sources = [
        {
            "id": "web_8",
            "title": "Morningstar",
            "url": "https://example.com",
            "type": "web",
        },
    ]
    enriched = enrich_report_citations(report, findings, sources)
    assert "web_8" in enriched["sections"][0]["citations"]
    assert enriched["citation_map"][0]["source_id"] == "web_8"


def test_build_facts_includes_per_symbol_and_analytics() -> None:
    snapshot = {
        "broker": "alpaca",
        "account": {"portfolio_value": 100_000.0, "cash": 10_000.0, "buying_power": 20_000.0},
        "positions": [
            {
                "symbol": "NVDA",
                "market_value": 30_000.0,
                "unrealized_pl": 500.0,
                "current_price": 120.0,
            }
        ],
        "open_orders": [],
        "analytics": {
            "cash_pct": 10.0,
            "equity_change_pct_since_day_ago": -1.5,
            "exposure_drift_pct": {"NVDA": 2.0},
        },
    }
    facts = build_facts_from_snapshot(
        snapshot,
        quotes={"NVDA": {"price": 121.0, "change_pct": 0.8, "sparkline": []}},
    )
    assert facts["position_NVDA_weight_pct"] == 30.0
    assert facts["position_NVDA_quote_change_pct"] == 0.8


def test_rule_transition_only_fires_on_edge(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import db.ai_tables as ai_tables

    db = tmp_path / "rules.db"
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
    reset_rule_transition_state()
    create_rule(
        rule_id="test-transition",
        name="Transition test",
        conditions={
            "fact": "portfolio_value",
            "operator": "greaterThan",
            "value": 0,
        },
        action={
            "type": "alert",
            "message": "hi",
            "trigger_on": "transition",
            "cooldown_seconds": 3600,
        },
        db_path=db,
    )
    facts = {"portfolio_value": 1000}
    assert len(evaluate_rules(facts, event_type="poll")) == 1
    assert len(evaluate_rules(facts, event_type="poll")) == 0
    reset_rule_transition_state()
    delete_rule("test-transition", db_path=db)
