"""Book analyst driver (research/book_driver.py) — gates, tool loop, lanes, arming, inbox."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from db.base import connect
from db.idea_outcomes import list_ledger_rows
from db.reports import recent_reports_by_kind
from db.strategies import seed_default_strategies, update_strategy
from research.book_driver import run_book_cycle
from research.books import build_book_pack

_ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 23, 17, 30, tzinfo=_ET)  # Thursday, post-window


@pytest.fixture
def unpaused(monkeypatch: Any):
    """Run a book's pipeline regardless of whether it is currently paused.

    Pausing (matt + panel, 2026-07-30) is an OPERATIONAL switch — it must not
    cost us the pipeline coverage we need the day either one resumes.
    """
    from research.books import BOOKS

    def _unpause(book_id: str) -> None:
        monkeypatch.setitem(BOOKS, book_id, dataclasses.replace(BOOKS[book_id], paused=False))

    return _unpause

GOOD_DECISION = {
    "candidates": [
        {
            "symbol": "AAPL",
            "direction": "long",
            "thesis": "post-selloff basing above support",
            "bear_case": "loses 200 and the base fails",
            "entry_low": "199",
            "entry_high": "201",
            "stop": "195",
            "targets": ["210"],
            "plan_horizon": "10d",
            "confidence_label": "medium",
            "advanced": True,
            "notional_usd": 400,
        },
        {
            "symbol": "MSFT",
            "direction": "long",
            "thesis": "watching only — no trigger yet",
            "entry_ref_price": "500",
            "plan_horizon": "5d",
            "advanced": False,
        },
        {
            "symbol": "IWM",
            "direction": "short",
            "thesis": "small caps rolling over",
            "entry_low": "289",
            "entry_high": "291",
            "stop": "296",
            "targets": ["280"],
            "plan_horizon": "10d",
            "advanced": True,  # must be short_blocked at the proposal step
        },
    ],
    "holds_review": "flat book, nothing to review",
    "journal": "stalking AAPL 200 retest; NVDA still too hot to chase",
}

GOOD_RESPONSE = "## Nightly report\n\nMarkdown body.\n\n```json\n" + json.dumps(GOOD_DECISION) + "\n```\n"


class FakeLLM:
    """No complete_tools attr → run_chat_loop degrades to plain complete()."""

    provider = "deepseek"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[Any] = []

    def complete(self, messages: list[Any], *, max_tokens: int = 1024) -> str:
        self.calls.append(messages)
        return self.responses.pop(0)


class FakeToolLLM:
    """Native tool-calling: pops queued ToolCallResults per model turn."""

    provider = "deepseek"

    def __init__(self, turns: list[Any]) -> None:
        self.turns = list(turns)
        self.calls: list[Any] = []

    def complete_tools(
        self, messages: list[Any], tools: list[Any], *, tool_choice: str = "auto",
        max_tokens: int = 1024,
    ) -> Any:
        self.calls.append((messages, tools))
        return self.turns.pop(0)

    def complete(self, messages: list[Any], *, max_tokens: int = 1024) -> str:
        raise AssertionError("plain complete should not be reached in the tool test")


def _run(db: Path, **kwargs: Any) -> dict[str, Any]:
    seed_default_strategies(db_path=db)
    kwargs.setdefault("now_et", NOW)
    kwargs.setdefault("db_path", db)
    return asyncio.run(run_book_cycle(kwargs.pop("book_id", "deepseek"), **kwargs))


def _notification_count(db: Path) -> int:
    with connect(db) as conn:
        try:
            return conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        except Exception:
            return 0


def test_dry_cycle_registers_lanes_saves_report_and_inbox_row(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    out = _run(db, llm=FakeLLM([GOOD_RESPONSE]))
    assert out["status"] == "ok" and out["armed"] is False and out["proposals"] == []
    heads = list_ledger_rows(origin="book:deepseek", db_path=db)["rows"]
    assert {r["symbol"] for r in heads} == {"AAPL", "IWM"}
    considered = list_ledger_rows(origin="book:deepseek/considered", db_path=db)["rows"]
    assert [r["symbol"] for r in considered] == ["MSFT"]
    reports = recent_reports_by_kind("book_deepseek", db_path=db)
    assert len(reports) == 1
    payload = reports[0]["payload"]
    assert payload["dry"] is True and payload["registered"] == 3
    assert payload["journal"].startswith("stalking AAPL")
    assert _notification_count(db) == 1  # the app/web inbox row


def test_report_body_drops_the_decision_block_it_already_parsed(tmp_path: Path) -> None:
    """The block is REQUIRED output (_DECISION_CONTRACT), so no prompt change can
    remove it — the save path has to. It was 92% of the 2026-08-06 Deep Seek report,
    which the phone then collapsed behind a disclosure, reading as 'output is tiny'."""
    db = tmp_path / "t.db"
    assert _run(db, llm=FakeLLM([GOOD_RESPONSE]))["status"] == "ok"
    body = recent_reports_by_kind("book_deepseek", db_path=db)[0]["body"]
    assert "## Nightly report" in body and "Markdown body." in body
    assert "```json" not in body
    assert "post-selloff basing above support" not in body  # block content, not prose
    # The parsed decision is not lost — it rides the payload, where it is usable.
    payload = recent_reports_by_kind("book_deepseek", db_path=db)[0]["payload"]
    assert payload["journal"].startswith("stalking AAPL")


def test_prose_json_block_survives_only_the_decision_goes(tmp_path: Path) -> None:
    """Only the LAST block is the contract. A json block the analyst quotes inside
    its own prose is prose, and stripping it would silently edit the report."""
    quoted = (
        "## Nightly report\n\nThe API returned:\n\n```json\n{\"quoted\": true}\n```\n\n"
        "That is why I passed.\n\n```json\n" + json.dumps(GOOD_DECISION) + "\n```\n"
    )
    db = tmp_path / "t.db"
    assert _run(db, llm=FakeLLM([quoted]))["status"] == "ok"
    body = recent_reports_by_kind("book_deepseek", db_path=db)[0]["body"]
    assert '{"quoted": true}' in body
    assert "holds_review" not in body
    assert body.rstrip().endswith("That is why I passed.")


def test_block_only_completion_saves_an_honest_placeholder(tmp_path: Path) -> None:
    """Strip-to-empty must not store a blank body — the row should say what happened."""
    db = tmp_path / "t.db"
    only_block = "```json\n" + json.dumps(GOOD_DECISION) + "\n```"
    assert _run(db, llm=FakeLLM([only_block]))["status"] == "ok"
    body = recent_reports_by_kind("book_deepseek", db_path=db)[0]["body"]
    assert body == "(no prose — the model emitted only its decision block)"


def test_journal_feeds_back_into_next_pack(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    assert _run(db, llm=FakeLLM([GOOD_RESPONSE]))["status"] == "ok"
    pack = build_book_pack("deepseek", db_path=db)
    assert "Your handoff from last cycle" in pack
    assert "stalking AAPL 200 retest" in pack


def test_matt_advisory_cycle_report_only(tmp_path: Path, unpaused: Any) -> None:
    unpaused("matt")
    db = tmp_path / "t.db"
    out = _run(db, book_id="matt", llm=FakeLLM(["## Advisory\n\nno json block needed"]))
    assert out == {"status": "ok", "advisory": True, "report_id": out["report_id"]}
    reports = recent_reports_by_kind("book_matt", db_path=db)
    assert len(reports) == 1 and reports[0]["payload"]["advisory"] is True
    # Structurally no machine ideas: zero rows in ANY lane, one inbox row.
    assert list_ledger_rows(db_path=db)["rows"] == []
    assert _notification_count(db) == 1


def test_tool_loop_executes_allowlisted_and_blocks_others(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from ai.llm.base import ToolCall, ToolCallResult

    executed: list[tuple[str, dict[str, Any]]] = []

    async def fake_execute(name: str, args: dict[str, Any]) -> Any:
        executed.append((name, args))
        return {"ok": True}

    import ai.chat.tools as chat_tools

    monkeypatch.setattr(chat_tools, "execute_tool", fake_execute)

    fake = FakeToolLLM(
        [
            ToolCallResult(
                text="",
                tool_calls=(
                    ToolCall(id="1", name="query", arguments={"resource": "strategies"}),
                    ToolCall(id="2", name="propose_order", arguments={"symbol": "AAPL"}),
                ),
            ),
            ToolCallResult(text=GOOD_RESPONSE, tool_calls=()),
        ]
    )
    db = tmp_path / "t.db"
    out = _run(db, llm=fake)
    assert out["status"] == "ok"
    # query ran; propose_order never reached the chat executor (allowlist wall).
    assert executed == [("query", {"resource": "strategies"})]
    # The blocked call's error went back to the model as a tool result and the
    # loop continued to the final report.
    assert len(fake.calls) == 2


def test_second_run_same_day_dedups(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    assert _run(db, llm=FakeLLM([GOOD_RESPONSE]))["status"] == "ok"
    again = _run(db, llm=FakeLLM([GOOD_RESPONSE]))
    assert again == {"status": "skipped", "reason": "already_ran_today"}


def test_window_and_weekend_gates(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    early = _run(db, llm=FakeLLM([]), now_et=datetime(2026, 7, 23, 16, 59, tzinfo=_ET))
    assert early == {"status": "skipped", "reason": "before_window"}
    weekend = _run(db, llm=FakeLLM([]), now_et=datetime(2026, 7, 25, 18, 0, tzinfo=_ET))
    assert weekend == {"status": "skipped", "reason": "weekend"}


def test_parse_repair_retry_recovers(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    fake = FakeLLM(["no json block here", "```json\n" + json.dumps(GOOD_DECISION) + "\n```"])
    out = _run(db, llm=fake)
    assert out["status"] == "ok"
    assert len(fake.calls) == 2  # analysis + one repair retry
    assert len(list_ledger_rows(origin="book:deepseek", db_path=db)["rows"]) == 2


def test_double_parse_fail_saves_failure_report_registers_nothing(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    out = _run(db, llm=FakeLLM(["garbage", "still garbage"]))
    assert out["status"] == "parse_fail"
    reports = recent_reports_by_kind("book_deepseek", db_path=db)
    assert len(reports) == 1 and reports[0]["payload"]["status"] == "parse_fail"
    assert list_ledger_rows(origin="book:deepseek", db_path=db)["rows"] == []
    # A failed cycle still stamps the day — no retry storm within the same day.
    again = _run(db, llm=FakeLLM([GOOD_RESPONSE]))
    assert again == {"status": "skipped", "reason": "already_ran_today"}


def test_armed_cycle_self_sized_proposals_block_shorts_drop_unsized(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Founder 07-23: the analyst sizes its own book — the model's notional
    passes through UNCLAMPED; an advanced pick without a size is dropped."""
    db = tmp_path / "t.db"
    seed_default_strategies(db_path=db)
    update_strategy("book_deepseek", enabled=True, notional_usd=300.0, db_path=db)

    captured: list[Any] = []

    async def fake_propose(candidate: Any, *, db_path: Path) -> dict[str, Any]:
        captured.append(candidate)
        return {"status": "proposed"}

    import trading.proposal_intake as intake

    monkeypatch.setattr(intake, "propose_trade", fake_propose)
    decision = json.loads(json.dumps(GOOD_DECISION))
    decision["candidates"][0]["notional_usd"] = 22_000  # the analyst's own size
    decision["candidates"].append(
        {
            "symbol": "CVX",
            "direction": "long",
            "thesis": "energy breakout",
            "entry_low": "160",
            "entry_high": "162",
            "stop": "155",
            "targets": ["175"],
            "plan_horizon": "10d",
            "advanced": True,  # no notional_usd → dropped as unfinished work
        }
    )
    response = "## r\n\n```json\n" + json.dumps(decision) + "\n```"
    out = asyncio.run(run_book_cycle("deepseek", now_et=NOW, db_path=db, llm=FakeLLM([response])))
    assert out["status"] == "ok" and out["armed"] is True
    # AAPL proposed at the model's OWN 22k (strategy notional_usd=300 ignored);
    # IWM short blocked; CVX dropped unsized; MSFT not advanced.
    assert [c.symbol for c in captured] == ["AAPL"]
    assert float(captured[0].notional_usd) == 22_000.0
    assert captured[0].account_id == "acct_alpaca_deepseek"
    assert captured[0].source == "book_deepseek"
    statuses = {p["symbol"]: p["status"] for p in out["proposals"]}
    assert statuses == {"AAPL": "proposed", "IWM": "short_blocked", "CVX": "no_size"}


def test_analysis_crash_retries_then_stamps_error_report(tmp_path: Path) -> None:
    class ExplodingLLM:
        provider = "deepseek"

        def complete(self, messages: list[Any], *, max_tokens: int = 1024) -> str:
            raise RuntimeError("api down")

    db = tmp_path / "t.db"
    seed_default_strategies(db_path=db)
    for attempt in (1, 2):
        out = asyncio.run(run_book_cycle("deepseek", now_et=NOW, db_path=db, llm=ExplodingLLM()))
        assert out["status"] == "retry" and out["attempt"] == attempt
        assert recent_reports_by_kind("book_deepseek", db_path=db) == []
    out = asyncio.run(run_book_cycle("deepseek", now_et=NOW, db_path=db, llm=ExplodingLLM()))
    assert out["status"] == "error"
    reports = recent_reports_by_kind("book_deepseek", db_path=db)
    assert len(reports) == 1 and reports[0]["payload"]["status"] == "error"
    # The error report stamps the day — the fourth tick skips.
    again = asyncio.run(run_book_cycle("deepseek", now_et=NOW, db_path=db, llm=ExplodingLLM()))
    assert again == {"status": "skipped", "reason": "already_ran_today"}


def test_unconfigured_llm_skips(tmp_path: Path, monkeypatch: Any) -> None:
    import ai.llm.factory as factory
    from ai.llm.mock import MockLLM

    monkeypatch.setattr(factory, "get_llm_for_provider", lambda *a, **k: MockLLM())
    db = tmp_path / "t.db"
    out = _run(db)  # no llm injected → factory path → mock → skip
    assert out == {"status": "skipped", "reason": "llm_unconfigured"}


PANEL_DECISION = {
    "candidates": [
        {
            "symbol": "NVDA",
            "seat": "bull",
            "direction": "long",
            "thesis": "ai capex supercycle continues",
            "bear_case": "pre-mortem: guidance finally disappoints",
            "entry_low": "170",
            "entry_high": "174",
            "stop": "162",
            "targets": ["195"],
            "plan_horizon": "15d",
            "confidence_label": "high",
            "advanced": True,
        },
        {
            "symbol": "XLF",
            "seat": "bear",
            "direction": "short",
            "thesis": "credit stress building",
            "entry_ref_price": "43",
            "plan_horizon": "10d",
            "advanced": False,
        },
    ],
    "holds_review": "flat",
    "journal": "bear seat keeps losing the credit argument — watch its lane",
}


def test_session_submit_panel_registers_seats_and_delivers(
    tmp_path: Path, unpaused: Any
) -> None:
    from research.book_driver import submit_session_cycle

    unpaused("panel")
    db = tmp_path / "t.db"
    seed_default_strategies(db_path=db)
    out = asyncio.run(
        submit_session_cycle("panel", "## Panel report", PANEL_DECISION, now_et=NOW, db_path=db)
    )
    assert out["status"] == "ok" and out["armed"] is False and out["proposals"] == []
    # Advanced bull idea → seat lane AND headline; bear candidate → seat lane only.
    assert [r["symbol"] for r in list_ledger_rows(origin="book:panel/bull", db_path=db)["rows"]] == ["NVDA"]
    assert [r["symbol"] for r in list_ledger_rows(origin="book:panel", db_path=db)["rows"]] == ["NVDA"]
    assert [r["symbol"] for r in list_ledger_rows(origin="book:panel/bear", db_path=db)["rows"]] == ["XLF"]
    reports = recent_reports_by_kind("book_panel", db_path=db)
    assert len(reports) == 1 and reports[0]["payload"]["journal"].startswith("bear seat")
    assert _notification_count(db) == 1
    # Same day re-submit dedups.
    again = asyncio.run(
        submit_session_cycle("panel", "## Panel report", PANEL_DECISION, now_et=NOW, db_path=db)
    )
    assert again == {"status": "skipped", "reason": "already_ran_today"}


def test_session_submit_invalid_schema_does_not_stamp_day(tmp_path: Path) -> None:
    from research.book_driver import submit_session_cycle

    db = tmp_path / "t.db"
    seed_default_strategies(db_path=db)
    bad = {"candidates": [{"symbol": "AAPL"}]}  # missing thesis/plan_horizon
    out = asyncio.run(submit_session_cycle("fable", "## r", bad, now_et=NOW, db_path=db))
    assert out["status"] == "invalid"
    assert recent_reports_by_kind("book_fable", db_path=db) == []
    # The session fixes its JSON and re-submits successfully.
    ok = asyncio.run(
        submit_session_cycle(
            "fable",
            "## r",
            {"candidates": [], "holds_review": "flat", "journal": "quiet night"},
            now_et=NOW,
            db_path=db,
        )
    )
    assert ok["status"] == "ok"
    assert recent_reports_by_kind("book_fable", db_path=db)[0]["payload"]["journal"] == "quiet night"
