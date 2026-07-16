"""Tests for trade retrospective report generation."""

from __future__ import annotations

import copy
import json
from datetime import date, timedelta

import pytest

import db.ai_tables as ai_tables
from ai.llm.structured import parse_with_retry
from ai.perf_context import sanitize_perf_context
from ai.retrospective import (
    INSUFFICIENT_OOS_NOTE,
    RETRO_MAX_TOKENS,
    RetroReport,
    _compute_train_oos_split,
    _normalize_unicode_text,
    generate_retrospective_report,
)
from signals.indicators import Bar

ANALYSIS_FROM = "2025-01-01"
ANALYSIS_TO = "2025-12-31"
SPLIT = _compute_train_oos_split(ANALYSIS_FROM, ANALYSIS_TO)
TRAIN_FROM = SPLIT["train"]["from"]
TRAIN_TO = SPLIT["train"]["to"]
OOS_FROM = SPLIT["oos"]["from"]
OOS_TO = SPLIT["oos"]["to"]

MOCK_CTX = {
    "period": "year",
    "from_date": TRAIN_FROM,
    "to_date": TRAIN_TO,
    "realized": {
        "realized_closed": "1500.00",
        "closed_win_rate": 0.55,
        "per_symbol": [{"symbol": "AAPL", "realized": "800.00"}],
        "incomplete_positions": [{"symbol": "PLTR"}],
        "reliability": {"complete": True},
    },
    "behavioral": {"scalp_ratio": 0.2},
    "approximate_fifo": {
        "largest_wins": [
            {
                "symbol": "AAPL",
                "open_date": "2025-03-01",
                "close_date": "2025-03-05",
                "pnl": "500.00",
                "quantity": "10",
            }
        ],
        "largest_losses": [
            {
                "symbol": "MSFT",
                "open_date": "2025-04-01",
                "close_date": "2025-04-02",
                "pnl": "-200.00",
                "quantity": "5",
            }
        ],
        "note": "approximate (hold-time only)",
    },
    "sample_train_event": "2025-06-15",
}

MOCK_REPORT = {
    "narrative": "Realized on closed positions was **$1,500**. PLTR is open without cost basis.",
    "what_went_right": ["AAPL contributed $800 realized."],
    "what_went_wrong": ["Concentration in a few names."],
    "behavioral_observations": ["Moderate scalping frequency."],
    "proposed_rules": [
        {
            "name": "AAPL RSI oversold",
            "rationale": "Buy dips when RSI is low on a core holding.",
            "spec": {
                "name": "AAPL RSI oversold",
                "condition": {
                    "metric": "rsi_14",
                    "symbol": "AAPL",
                    "operator": "lessThan",
                    "value": 30,
                },
                "action": {"type": "alert", "message": "AAPL RSI below 30"},
            },
        },
        {
            "name": "Low cash warning",
            "rationale": "Preserve dry powder when cash is thin.",
            "spec": {
                "name": "Low cash warning",
                "condition": {
                    "metric": "cash_pct",
                    "operator": "lessThan",
                    "value": 5,
                },
                "action": {"type": "alert", "message": "Cash below 5%"},
            },
        },
        {
            "name": "Invalid metric rule",
            "rationale": "Should be dropped.",
            "spec": {
                "name": "Invalid metric rule",
                "condition": {
                    "metric": "bogus_metric",
                    "symbol": "AAPL",
                    "operator": "lessThan",
                    "value": 1,
                },
            },
        },
    ],
    "proposed_lessons": [
        "Size winners; cut losers faster.",
        "Avoid over-concentration in single names.",
    ],
}


class _FixedRetroLLM:
    provider = "mock"

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload if payload is not None else MOCK_REPORT

    def complete(self, messages, *, max_tokens: int = 1024) -> str:
        return json.dumps(self.payload)

    def complete_json(self, messages, *, max_tokens: int = 1024, temperature: float = 0.1) -> str:
        return json.dumps(self.payload)


class _FencedRetroLLM(_FixedRetroLLM):
    def complete(self, messages, *, max_tokens: int = 1024) -> str:
        body = json.dumps(self.payload)
        return f"Here is the retrospective:\n```json\n{body}\n```\nDone."

    def complete_json(self, messages, *, max_tokens: int = 1024, temperature: float = 0.1) -> str:
        return self.complete(messages, max_tokens=max_tokens)


def _synthetic_bars(n: int | None = None) -> list[Bar]:
    dates = _synthetic_bar_dates(n)
    bars: list[Bar] = []
    price = 100.0
    for i in range(len(dates)):
        price += 0.1 if i % 20 else -2.0
        bars.append({"o": price, "h": price + 1, "l": price - 1, "c": price, "v": 1000.0})
    return bars


def _synthetic_bar_dates(n: int | None = None) -> list[date]:
    start = date(2024, 1, 1)
    end = date.fromisoformat(ANALYSIS_TO)
    if n is not None:
        return [start + timedelta(days=i) for i in range(n)]
    dates: list[date] = []
    current = start
    while current <= end:
        dates.append(current)
        current += timedelta(days=1)
    return dates


def _patch_retrospective_deps(monkeypatch, *, llm_ctx_seen: list | None = None) -> list[dict]:
    context_calls: list[dict] = []

    def _mock_ai_performance_context(**kwargs):
        context_calls.append(kwargs)
        ctx = copy.deepcopy(MOCK_CTX)
        ctx["from_date"] = kwargs.get("custom_from") or ANALYSIS_FROM
        ctx["to_date"] = kwargs.get("custom_to") or ANALYSIS_TO
        return ctx

    monkeypatch.setattr(
        "ai.retrospective.ai_performance_context",
        _mock_ai_performance_context,
    )
    monkeypatch.setattr("ai.retrospective.get_llm", lambda **kwargs: _FixedRetroLLM())

    if llm_ctx_seen is not None:

        def _capture_llm(ctx):
            llm_ctx_seen.append(copy.deepcopy(ctx))
            from ai.retrospective import RetroReport

            return RetroReport.model_validate(MOCK_REPORT)

        monkeypatch.setattr("ai.retrospective._call_retrospective_llm", _capture_llm)

    monkeypatch.setattr(
        "ai.retrospective._fetch_symbol_bars",
        lambda symbol, oos_window: (_synthetic_bars(), _synthetic_bar_dates()),
    )
    return context_calls


@pytest.fixture
def retro_db(monkeypatch, tmp_path):
    db = tmp_path / "retro.db"
    monkeypatch.setattr(ai_tables, "DEFAULT_DB_PATH", db)

    def _add_memory(self, content, *, memory_type, metadata=None, **kwargs):
        return ai_tables.create_memory(
            memory_type=memory_type,
            content=content,
            metadata=metadata,
            db_path=db,
            **kwargs,
        )

    from ai.memories import MemoryService

    monkeypatch.setattr(MemoryService, "add", _add_memory)
    return db


def test_sanitize_llm_context_strips_unreliable_pnl() -> None:
    sanitized = sanitize_perf_context(MOCK_CTX)
    for key in ("largest_wins", "largest_losses"):
        for trade in sanitized["approximate_fifo"][key]:
            assert "pnl" not in trade
            assert "quantity" not in trade
            assert "symbol" in trade
            assert "open_date" in trade
            assert "close_date" in trade
    assert MOCK_CTX["approximate_fifo"]["largest_wins"][0]["pnl"] == "500.00"


def test_train_oos_split_excludes_tail_from_train() -> None:
    split = _compute_train_oos_split(ANALYSIS_FROM, ANALYSIS_TO)
    assert split["valid"] is True
    assert split["train"]["from"] == ANALYSIS_FROM
    assert split["train"]["to"] < split["oos"]["from"]
    assert split["oos"]["to"] == ANALYSIS_TO


def test_insufficient_split_marks_oos_false(monkeypatch, retro_db) -> None:
    short_from, short_to = "2025-01-01", "2025-01-20"
    monkeypatch.setattr(
        "ai.retrospective.resolve_period",
        lambda period, **kwargs: (short_from, short_to),
    )
    split = _compute_train_oos_split(short_from, short_to)
    assert split["valid"] is False

    _patch_retrospective_deps(monkeypatch)
    result = generate_retrospective_report(period="month")

    events = ai_tables.list_rule_events(limit=10, db_path=retro_db)
    insufficient_events = [
        e for e in events if e["payload"]["backtest_metrics"].get("oos") is False
    ]
    assert len(insufficient_events) == 1
    assert insufficient_events[0]["payload"]["backtest_metrics"]["note"] == INSUFFICIENT_OOS_NOTE
    assert "artifact_id" in result


def test_generate_retrospective_report(
    monkeypatch,
    retro_db,
) -> None:
    llm_ctx_seen: list[dict] = []
    context_calls: list[dict] = []

    def _mock_ai_performance_context(**kwargs):
        context_calls.append(kwargs)
        ctx = copy.deepcopy(MOCK_CTX)
        ctx["from_date"] = kwargs.get("custom_from") or ANALYSIS_FROM
        ctx["to_date"] = kwargs.get("custom_to") or ANALYSIS_TO
        return ctx

    def _capture_llm(ctx):
        llm_ctx_seen.append(copy.deepcopy(ctx))
        from ai.retrospective import RetroReport

        return RetroReport.model_validate(MOCK_REPORT)

    monkeypatch.setattr(
        "ai.retrospective.resolve_period",
        lambda period, **kwargs: (ANALYSIS_FROM, ANALYSIS_TO),
    )
    monkeypatch.setattr(
        "ai.retrospective.ai_performance_context",
        _mock_ai_performance_context,
    )
    monkeypatch.setattr("ai.retrospective.get_llm", lambda **kwargs: _FixedRetroLLM())
    monkeypatch.setattr("ai.retrospective._call_retrospective_llm", _capture_llm)
    monkeypatch.setattr(
        "ai.retrospective._fetch_symbol_bars",
        lambda symbol, oos_window: (_synthetic_bars(), _synthetic_bar_dates()),
    )

    result = generate_retrospective_report(period="year")

    assert len(context_calls) == 1
    assert context_calls[0]["custom_from"] == TRAIN_FROM
    assert context_calls[0]["custom_to"] == TRAIN_TO

    assert len(llm_ctx_seen) == 1
    llm_ctx = llm_ctx_seen[0]
    assert llm_ctx["to_date"] == TRAIN_TO
    assert llm_ctx["from_date"] == TRAIN_FROM
    assert llm_ctx["to_date"] < OOS_FROM
    fifo_dates = json.dumps(llm_ctx.get("approximate_fifo", {}))
    assert OOS_FROM not in fifo_dates
    assert llm_ctx["sample_train_event"] < OOS_FROM
    for key in ("largest_wins", "largest_losses"):
        for trade in llm_ctx["approximate_fifo"][key]:
            assert "pnl" not in trade

    assert "artifact_id" in result
    assert result["period"] == "year"
    assert result["n_proposed_rules"] == 2
    assert result["n_lessons"] == 2

    rules = ai_tables.list_rules(db_path=retro_db)
    assert len(rules) == 2
    assert all(not rule["enabled"] for rule in rules)

    events = ai_tables.list_rule_events(limit=10, db_path=retro_db)
    assert len(events) == 2
    for event in events:
        assert event["event_type"] == "ai_retrospective"
        payload = event["payload"]
        assert payload["source"] == "ai_retrospective"
        assert "backtest_metrics" in payload

    portfolio_event = next(
        e for e in events if "not backtestable" in str(e["payload"]["backtest_metrics"].get("note", ""))
    )
    assert portfolio_event is not None

    symbol_event = next(
        e for e in events if e["payload"]["backtest_metrics"].get("fire_count") is not None
    )
    metrics = symbol_event["payload"]["backtest_metrics"]
    assert metrics["oos_window"] is not None
    assert metrics["oos"] is True
    assert metrics["oos_trading_days"] is not None
    assert "fires over" in f"{metrics['fire_count']} fires over {metrics['oos_trading_days']} OOS trading days"

    artifact = ai_tables.get_research_artifact(result["artifact_id"], db_path=retro_db)
    assert artifact is not None
    assert artifact["topic"] == "Trade Retrospective year"
    assert artifact["depth"] == "retrospective"
    assert artifact["report"] is not None
    assert artifact["html"]
    assert TRAIN_TO in artifact["markdown"]
    assert OOS_FROM in artifact["markdown"]
    assert "fires over" in artifact["markdown"] or str(metrics["fire_count"]) in artifact["markdown"]

    lessons = ai_tables.list_memories(memory_type="lesson", db_path=retro_db)
    assert len(lessons) == 2
    assert all(lesson["type"] == "lesson" for lesson in lessons)
    assert all(
        lesson["metadata"].get("source") == "retrospective" for lesson in lessons
    )

    assert not any(rule["enabled"] for rule in rules)


def test_normalize_unicode_text_repairs_em_dash_mojibake() -> None:
    mojibake = INSUFFICIENT_OOS_NOTE.encode("utf-8").decode("latin-1")
    assert _normalize_unicode_text(mojibake) == INSUFFICIENT_OOS_NOTE


def test_retrospective_parses_fenced_json_and_keeps_valid_rule(
    monkeypatch,
    retro_db,
) -> None:
    fenced_payload = {
        **MOCK_REPORT,
        "narrative": "Realized on closed positions was positive — PLTR lacks cost basis.",
        "proposed_rules": [
            {
                "name": "TSLA RSI oversold",
                "rationale": "Alert on oversold TSLA.",
                "spec": {
                    "name": "TSLA RSI oversold",
                    "condition": {
                        "all": [
                            {
                                "metric": "rsi_14",
                                "symbol": "TSLA",
                                "operator": "lessThan",
                                "value": 30,
                            }
                        ],
                    },
                    "action": {
                        "type": "alert",
                        "message": "TSLA RSI below 30",
                        "trigger_on": "transition",
                        "cooldown_seconds": 86400,
                    },
                },
            }
        ],
    }

    monkeypatch.setattr(
        "ai.retrospective.resolve_period",
        lambda period, **kwargs: (ANALYSIS_FROM, ANALYSIS_TO),
    )
    _patch_retrospective_deps(monkeypatch)
    monkeypatch.setattr(
        "ai.retrospective.get_llm",
        lambda **kwargs: _FencedRetroLLM(fenced_payload),
    )

    from ai.retrospective import _call_retrospective_llm

    monkeypatch.setattr(
        "ai.retrospective._call_retrospective_llm",
        lambda ctx: _call_retrospective_llm(ctx),
    )

    result = generate_retrospective_report(period="year")

    assert result["n_proposed_rules"] == 1
    rules = ai_tables.list_rules(db_path=retro_db)
    assert len(rules) == 1
    assert rules[0]["name"] == "TSLA RSI oversold"
    assert not rules[0]["enabled"]

    artifact = ai_tables.get_research_artifact(result["artifact_id"], db_path=retro_db)
    assert artifact is not None
    assert "—" in artifact["markdown"]
    assert artifact["report"]["proposed_rules"][0]["backtest_metrics"] is not None


def test_retrospective_drops_string_condition_rule(
    monkeypatch,
    retro_db,
) -> None:
    payload = {
        **MOCK_REPORT,
        "proposed_rules": [
            {
                "name": "String condition (invalid)",
                "rationale": "Should be dropped.",
                "spec": {
                    "name": "String condition (invalid)",
                    "condition": "when TSLA RSI is below 30",
                    "action": {"type": "alert", "message": "bad"},
                },
            },
            MOCK_REPORT["proposed_rules"][0],
        ],
    }

    monkeypatch.setattr(
        "ai.retrospective.resolve_period",
        lambda period, **kwargs: (ANALYSIS_FROM, ANALYSIS_TO),
    )
    _patch_retrospective_deps(monkeypatch)
    monkeypatch.setattr("ai.retrospective.get_llm", lambda **kwargs: _FixedRetroLLM(payload))

    from ai.retrospective import _call_retrospective_llm

    monkeypatch.setattr(
        "ai.retrospective._call_retrospective_llm",
        lambda ctx: _call_retrospective_llm(ctx),
    )

    result = generate_retrospective_report(period="year")

    assert result["n_proposed_rules"] == 1
    rules = ai_tables.list_rules(db_path=retro_db)
    assert len(rules) == 1
    assert rules[0]["name"] == "AAPL RSI oversold"


def test_retrospective_llm_uses_higher_max_tokens(monkeypatch) -> None:
    seen: list[int] = []

    class _TokenSpyLLM(_FixedRetroLLM):
        def complete_json(self, messages, *, max_tokens: int = 1024, temperature: float = 0.1) -> str:
            seen.append(max_tokens)
            return super().complete_json(messages, max_tokens=max_tokens, temperature=temperature)

    monkeypatch.setattr("ai.retrospective.get_llm", lambda **kwargs: _TokenSpyLLM())
    from ai.retrospective import _call_retrospective_llm

    _call_retrospective_llm(MOCK_CTX)

    assert seen == [RETRO_MAX_TOKENS]


def test_parse_with_retry_accepts_fenced_retrospective_json() -> None:
    payload = {
        "narrative": "Summary",
        "what_went_right": ["good trade"],
        "what_went_wrong": [],
        "behavioral_observations": [],
        "proposed_rules": [],
        "proposed_lessons": [],
    }
    raw = f"Analysis complete:\n```json\n{json.dumps(payload)}\n```"
    parsed = parse_with_retry(raw, RetroReport)
    assert parsed is not None
    assert parsed.narrative == "Summary"
