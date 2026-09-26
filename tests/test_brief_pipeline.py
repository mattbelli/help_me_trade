"""Nightly brief Stage 2: number check, probability strip, fact verification, grader
blinding, call resolution, submit idempotency, and the orchestrator end to end with
the claude CLI and DeepSeek mocked. Nothing here reaches a real LLM, CLI or ntfy."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from research.brief import arms, facts, scorecard, submit, verify
from research.brief.board import board_key
from tools import nightly_brief as nb

# --- number check -------------------------------------------------------------------------


def test_number_check_matches_rounding_formats_and_ignores_dates() -> None:
    idx = verify.build_index(jsons=[{
        "flip": 7617.7, "net_gamma": 65.8e9, "away_pct": 1.41, "classified_frac": 0.62,
        "close": 101.234, "label": "largest print $1.25M",
    }])
    text = (
        "1. On 2026-09-25 at 16:05 ET the flip sat at 7,618, net $65.8B, 1.41% away; "
        "62% classified; close 101.23; a $1.25M print. Horizon 5 sessions, 10d, since 1979. "
        "Invented: 7,777 and $3.3B and 12.5%."
    )
    assert verify.unmatched_numbers(text, idx) == ["7,777", "$3.3B", "12.5%"]


def test_number_check_is_sign_blind_and_reads_bp() -> None:
    idx = verify.build_index(jsons=[{"chg": -2.31, "spread": 0.25}])
    assert verify.unmatched_numbers("down −2.31% and a 25bp gap", idx) == []


def test_probability_claims_are_stripped() -> None:
    text = ("## Pick\n- NVDA looks strong. There is a 70% chance it breaks out. Stop 98.\n"
            "The odds of a squeeze are rising.\nWin rate 65% historically.\nKeep this line.")
    out, removed = verify.strip_probability_claims(text)
    assert removed == 3
    assert "70%" not in out and "odds of" not in out and "Win rate" not in out
    assert "NVDA looks strong." in out and "Stop 98." in out and "Keep this line." in out


def test_check_night_counts_per_memo_against_its_own_tool_results(tmp_path: Path) -> None:
    (tmp_path / "board.json").write_text(json.dumps({"x": 100.5}))
    (tmp_path / "facts.json").write_text(json.dumps({"names": {}}))
    (tmp_path / "memo_flows_opus.md").write_text("levels 100.5 and 222.2")
    (tmp_path / "tools_flows_opus.txt").write_text("the tool said 222.2")
    (tmp_path / "memo_flows_deepseek.md").write_text("levels 100.5 and 222.2")
    (tmp_path / "brief.md").write_text("brief cites 222.2 and 333.3")
    out = verify.check_night(tmp_path, "opus")
    assert out["memos"]["flows_opus"]["unmatched"] == []
    assert out["memos"]["flows_deepseek"]["unmatched"] == ["222.2"]
    assert out["brief"]["unmatched"] == ["333.3"]
    assert json.loads((tmp_path / "numbers.json").read_text()) == out


# --- fact digests -------------------------------------------------------------------------


def test_fact_numbers_must_appear_in_the_cited_source() -> None:
    sources = [
        {"source_id": "news:1", "kind": "news", "text": "XYZ beat: revenue $1,200 million, up 12 percent."},
        {"source_id": "8k:a", "kind": "8-K", "text": "Guidance raised to 3,400 units. "
         "The Company repurchased $222.3 million3 of its stock."},
    ]
    raw = {"catalyst": "Earnings", "summary": "Beat.", "facts": [
        {"fact": "Revenue", "number": "$1.2B", "source_id": "news:1"},
        {"fact": "Growth", "number": "12%", "source_id": "news:1"},
        {"fact": "Units", "number": "3,400", "source_id": "8k:a"},
        {"fact": "Units in the wrong source", "number": "3,400", "source_id": "news:1"},
        {"fact": "Made up", "number": "$9.9B", "source_id": "news:1"},
        {"fact": "Unknown source", "number": None, "source_id": "news:404"},
        {"fact": "No number", "number": None, "source_id": "8k:a"},
        # Measured false negative (DRI 2026-09-24): a footnote digit glued to the unit.
        {"fact": "Buyback", "number": "$222.3 million", "source_id": "8k:a"},
    ]}
    d = facts.verify_digest(raw, sources)
    assert d["catalyst"] == "earnings"
    assert [f["fact"] for f in d["facts"]] == ["Revenue", "Growth", "Units", "No number", "Buyback"]
    assert len(d["dropped"]) == 3


def test_build_facts_is_fail_soft_per_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(facts, "gather_sources", lambda sym, *_a, **_k: [
        {"source_id": "news:1", "kind": "news", "text": f"{sym} sales 5.5 billion"}])

    class FakeLLM:
        model = "fake"

        def complete_json(self, messages: list[Any], **_k: Any) -> str:
            if "Stock: BAD" in messages[1].content:
                raise TimeoutError("slow")
            return json.dumps({"catalyst": "earnings", "summary": "s", "facts": [
                {"fact": "sales", "number": "$5.5B", "source_id": "news:1"}]})

    board = {"et_date": "2026-09-25", "candidates": {"cards": [{"symbol": "AAA"}, {"symbol": "BAD"}]}}
    out = facts.build_facts(board, llm=FakeLLM())
    assert out["names"]["AAA"]["facts"][0]["number"] == "$5.5B"
    assert "TimeoutError" in out["names"]["BAD"]["error"]
    assert out["errors"] == ["BAD"] and out["kept_total"] == 1
    assert "digest failed" in facts.render_facts_md(out)


# --- arms ---------------------------------------------------------------------------------


def test_effort_ladder_and_arms() -> None:
    assert arms.lower_effort("xhigh") == "high" and arms.lower_effort("low") == "low"
    assert arms.arms_for("macro") == [("opus", "xhigh"), ("deepseek", None), ("opus_lower", "high")]
    assert arms.arms_for("macro", shadow=False) == [("opus", "xhigh")]
    cmd = arms.claude_cmd("PROMPT", "high", arms.SPECIALIST_TOOLS)
    assert cmd[:6] == ["claude", "--model", "claude-opus-5-5", "--effort", "high", "-p"]
    assert cmd[cmd.index("--allowedTools") + 1:] == list(arms.SPECIALIST_TOOLS)
    assert "Write" not in arms.SPECIALIST_TOOLS and "Agent" not in arms.SPECIALIST_TOOLS


def test_calls_are_validated() -> None:
    md, block = arms.split_json_block("memo\n```json\n{\"calls\": [\n"
        '{"claim": "c", "kind": "close", "subject": "spy", "op": ">", "threshold": 1, "horizon_sessions": 5},'
        '{"claim": "c", "kind": "close", "subject": "SPY", "op": ">=", "threshold": 1, "horizon_sessions": 5},'
        '{"claim": "c", "kind": "series", "subject": "DGS10", "op": "<", "threshold": 4, "horizon_sessions": 40}'
        "]}\n```")
    assert md == "memo"
    assert [c["subject"] for c in arms.validate_calls(block)] == ["SPY"]


# --- grader blinding ----------------------------------------------------------------------


def test_grader_labels_are_randomized_and_the_map_persisted(tmp_path: Path) -> None:
    for arm in ("opus", "deepseek", "opus_lower"):
        (tmp_path / f"memo_flows_{arm}.md").write_text(f"memo by {arm}")
    seen = set()
    for _ in range(40):
        grades = nb.plan_grades(tmp_path)
        assert json.loads((tmp_path / "grades.json").read_text()) == grades
        for g in grades["flows"]:
            assert sorted(g["labels"].values()) == sorted(["opus", g["shadow"]])
            text = (tmp_path / g["input"]).read_text()
            assert "deepseek" not in g["input"] and "opus" not in g["input"]
            assert text.index("# Memo A") < text.index(f"memo by {g['labels']['A']}")
            seen.add(g["labels"]["A"])
    assert {"opus", "deepseek", "opus_lower"} <= seen


# --- call resolution ----------------------------------------------------------------------


def test_calls_resolve_from_bars_and_series() -> None:
    et = date(2026, 9, 25)  # Friday: 1 session later is Monday 09-28
    close_call = {"kind": "close", "subject": "SPY", "op": ">", "threshold": 100, "horizon_sessions": 1}
    series_call = {"kind": "series", "subject": "DGS10", "op": "<", "threshold": 4.1, "horizon_sessions": 1}

    def closes(sym: str, d: date) -> float | None:
        return 101.0 if (sym, d) == ("SPY", date(2026, 9, 28)) else None

    def series(sid: str, after: date, through: date) -> tuple[float | None, str | None]:
        return 4.2, "2026-09-28"

    kw = {"closes": closes, "series": series, "today": date(2026, 9, 30)}
    assert scorecard.resolve_call(close_call, et, **kw)["status"] == "hit"
    assert scorecard.resolve_call({**close_call, "op": "<"}, et, **kw)["status"] == "miss"
    assert scorecard.resolve_call(series_call, et, **kw)["status"] == "miss"
    assert scorecard.resolve_call({**close_call, "subject": "QQQ"}, et, **kw)["status"] == "no_data"
    assert scorecard.resolve_call(close_call, et, closes=closes, series=series,
                                  today=date(2026, 9, 28))["status"] == "pending"
    # FRED lags: the store hasn't reached the target date yet -> still pending.
    assert scorecard.resolve_call(series_call, et, closes=closes,
                                  series=lambda *_a: (4.0, "2026-09-25"),
                                  today=date(2026, 9, 30))["status"] == "pending"


def test_scorecard_collects_grades_numbers_and_calls(tmp_path: Path) -> None:
    night = tmp_path / "2026-09-25"
    night.mkdir()
    call = {"claim": "c", "kind": "close", "subject": "SPY", "op": ">", "threshold": 100,
            "horizon_sessions": 1}
    (night / "calls_flows_opus.json").write_text(json.dumps([call]))
    (night / "calls_flows_deepseek.json").write_text(json.dumps([{**call, "op": "<"}]))
    (night / "numbers.json").write_text(json.dumps({"memos": {
        "flows_opus": {"unmatched": []}, "flows_deepseek": {"unmatched": ["1.1", "2.2"]}}}))
    verdict = {"A": {"accuracy": 4, "depth": 4, "insight": 3, "actionability": 4},
               "B": {"accuracy": 2, "depth": 3, "insight": 2, "actionability": 3}, "winner": "A"}
    (night / "grades.json").write_text(json.dumps({"flows": [
        {"shadow": "deepseek", "labels": {"A": "opus", "B": "deepseek"}, "verdict": verdict}]}))
    (night / "run.json").write_text(json.dumps({"sessions": {
        "flows_opus": {"wall_s": 300}, "judge": {"wall_s": 600}, "grade_flows_deepseek": {"wall_s": 50}}}))
    rows = scorecard.collect(tmp_path, closes=lambda *_a: 101.0, series=lambda *_a: (None, None),
                             today=date(2026, 9, 30))
    opus, ds = rows[("flows", "opus")], rows[("flows", "deepseek")]
    assert (opus["wins"], opus["games"], opus["hits"], opus["resolved"]) == (1, 1, 1, 1)
    assert (ds["wins"], ds["hits"], ds["unmatched"]) == (0, 0, [2])
    assert opus["wall_s"] == [300] and ("grade", "flows_deepseek") not in rows
    out = scorecard.render(rows)
    assert "flows" in out and "opus" in out and "deepseek" in out


# --- submit -------------------------------------------------------------------------------


def _write_night(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "board.json").write_text(json.dumps({
        "et_date": "2026-09-25", "next_session": "2026-09-28",
        "generated_at": "2026-09-25T21:00:00+00:00",
        "candidates": {"cards": [{"symbol": "XYZ", "day": {"close": 101.5}},
                                 {"symbol": "ABC", "day": {"close": 40.0}}]},
    }))
    (d / "brief.md").write_text("## The read\nQuiet tape.\n")
    (d / "picks.json").write_text(json.dumps({
        "picks": [
            {"rank": 1, "symbol": "XYZ", "direction": "long", "thesis": "flow + level",
             "bear_case": "b", "invalidation": "below 99", "entry_zone": {"low": "101", "high": "102"},
             "stop": "99", "targets": ["105"], "confidence": "medium", "horizon_sessions": 5},
            {"rank": 2, "symbol": "WAT", "direction": "watch", "thesis": "no structure",
             "entry_zone": None, "stop": None, "targets": [], "confidence": "low"},
        ],
        "considered": [{"symbol": "ABC", "direction": "short", "why_not": "extended"},
                       {"symbol": "NOL", "why_not": "no lean"}],
    }))


def test_submit_registers_once_and_pushes_once_per_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import research.book_driver as driver

    pushes: list[dict[str, Any]] = []

    async def fake_deliver(report: dict[str, Any], name: str, db_path: Path) -> None:
        pushes.append(report)

    monkeypatch.setattr(driver, "deliver_report", fake_deliver)
    db = tmp_path / "t.db"
    nightly, weekly = tmp_path / "2026-09-25", tmp_path / "2026-09-25-weekly"
    _write_night(nightly)
    _write_night(weekly)
    # The Sunday weekly's board was built two days after Friday's nightly.
    board = json.loads((weekly / "board.json").read_text())
    (weekly / "board.json").write_text(json.dumps({**board, "generated_at": "2026-09-27T22:30:00+00:00"}))

    first = asyncio.run(submit.submit_brief(nightly, db_path=db))
    assert first["status"] == "ok"
    assert {(r["symbol"], r["origin"]) for r in first["registered"]} == {
        ("XYZ", "brief/nightly"), ("ABC", "brief/considered")}
    assert {r["symbol"] for r in first["skipped"]} == {"WAT", "NOL"}
    assert [p["title"] for p in pushes] == ["Nightly brief: XYZ, WAT"]
    assert pushes[0]["format"] == "markdown" and pushes[0]["kind"] == "evening_brief"
    assert pushes[0]["payload"]["dedup_key"] == "nightly_brief"

    # Same ET date, other mode: its own dedup key and origin, so it lands too.
    week = asyncio.run(submit.submit_brief(weekly, mode="weekly", db_path=db))
    assert week["status"] == "ok"
    assert ("XYZ", "brief/weekly") in {(r["symbol"], r["origin"]) for r in week["registered"]}
    assert [p["title"] for p in pushes] == ["Nightly brief: XYZ, WAT", "Weekly brief: XYZ, WAT"]

    for night, mode in ((nightly, "nightly"), (weekly, "weekly")):
        again = asyncio.run(submit.submit_brief(night, mode=mode, db_path=db))
        assert again == {"status": "skipped", "reason": "already_submitted"}
    assert len(pushes) == 2
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT symbol, origin, grading_basis FROM idea_outcomes "
                            "WHERE symbol = 'XYZ' ORDER BY origin").fetchall()
    assert rows == [("XYZ", "brief/nightly", "plan"), ("XYZ", "brief/weekly", "plan")]
    # A crash between registering and saving the report: re-registration dedupes.
    again = submit.register_picks(json.loads((nightly / "picks.json").read_text()),
                                  json.loads((nightly / "board.json").read_text()), db_path=db)
    assert again["registered"] == []


# --- book-pack frame reads the board's date ------------------------------------------------


def test_market_frame_reads_the_date_it_is_given(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import db.abnormality as abn
    import db.attention as att
    import db.gamma_exposure as gex_mod
    import db.reports as reports_mod
    import signals.earnings_exhibit as exhibit_mod
    import signals.fusion.attention as attention_mod
    import signals.macro_substrate as macro_mod
    from research.books import _market_frame_section

    asked: dict[str, Any] = {}
    monkeypatch.setattr(attention_mod, "hot_lane_rows", lambda **_k: [])
    monkeypatch.setattr(reports_mod, "recent_reports_by_kind", lambda *_a, **_k: [])
    monkeypatch.setattr(gex_mod, "history", lambda **_k: [])
    store = tmp_path / "earnings.db"
    store.touch()
    monkeypatch.setattr(exhibit_mod, "DEFAULT_RELEASES_DB_PATH", store)
    monkeypatch.setattr(exhibit_mod, "latest_releases", lambda **_k: [
        {"symbol": "AMZN", "session": "amc", "accepted_et": "2026-09-24T16:06:23-04:00"}])
    monkeypatch.setattr(macro_mod, "event_calendar", lambda day, **_k: asked.setdefault("cal", day) and None)
    monkeypatch.setattr(abn, "events_today", lambda session=None, **_k: asked.setdefault("fires", session) and [])
    monkeypatch.setattr(att, "list_rows", lambda session, **_k: asked.setdefault("att", session) and [])

    frame = _market_frame_section(tmp_path / "t.db", "2026-09-24")
    assert asked == {"cal": "2026-09-24", "fires": "2026-09-24", "att": "2026-09-24"}
    assert "8-K earnings exhibits: AMZN amc" in frame


# --- orchestrator (claude + DeepSeek mocked) -----------------------------------------------

_BOARD = {
    "et_date": "2026-09-25", "next_session": "2026-09-28",
    "generated_at": "2026-09-25T21:00:00+00:00",
    "candidates": {"cards": [{"symbol": "XYZ", "day": {"close": 101.5}}]},
    "index": {"gex": {"SPX": {"spot": 6600.25}}},
}


def _stream(text: str, tool_result: str = "DGS10 4.12") -> str:
    events = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
            "input": {"command": "uv run python tools/perception_query.py query market"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": [
            {"type": "text", "text": tool_result}]}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": text,
         "num_turns": 3, "total_cost_usd": 1.25, "usage": {"output_tokens": 900}},
    ]
    return "\n".join(json.dumps(e) for e in events)


_CALLS = ('\n```json\n{"calls": [{"claim": "SPY above 650", "kind": "close", "subject": "SPY", '
          '"op": ">", "threshold": 650, "horizon_sessions": 5}]}\n```')


def _fake_spawn(fail: set[str], spawned: list[list[str]]) -> Any:
    def spawn(cmd: list[str], timeout: float) -> tuple[int, str, str]:
        spawned.append(cmd)
        if cmd[0] != "claude":  # the DeepSeek arm subprocess
            spec, brief_dir = cmd[-2], Path(cmd[-1])
            arms.write_arm_outputs(brief_dir, spec, "deepseek", f"## {spec} (ds)\nSPX 6600.25" + _CALLS, [])
            return 0, json.dumps({"ok": True, "iterations": 3}), ""
        prompt = cmd[cmd.index("-p") + 1]
        role = ("judge" if "the JUDGE" in prompt else "grader" if "blind GRADER" in prompt
                else next(s for s in arms.SPECIALISTS if f"{s.upper()} specialist" in prompt))
        effort = cmd[cmd.index("--effort") + 1]
        if role in fail:
            return 1, "", "boom"
        if role == "judge":
            text = ("## The read\nSPX sits at 6600.25; a 70% chance of a rally. Flip 9999.9.\n"
                    '```json\n{"picks": [{"rank": 1, "symbol": "XYZ", "direction": "long", '
                    '"thesis": "t", "bear_case": "b", "invalidation": "i", "entry_zone": null, '
                    '"stop": null, "targets": [], "confidence": "low"}], "considered": []}\n```')
        elif role == "grader":
            text = json.dumps({"A": {"accuracy": 4, "depth": 4, "insight": 3, "actionability": 3},
                               "B": {"accuracy": 3, "depth": 3, "insight": 3, "actionability": 3},
                               "winner": "A", "why": "w"})
        else:
            text = f"## {role} ({effort})\nSPX 6600.25, 10y 4.12." + _CALLS
        return 0, _stream(text), ""

    return spawn


def _run_night(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: set[str],
               mode: str = "weekly", **opts: bool) -> tuple[int, list[str], list[list[str]]]:
    pages: list[str] = []
    spawned: list[list[str]] = []
    monkeypatch.setattr(nb, "BRIEFS_DIR", tmp_path)
    monkeypatch.setattr(nb, "build_board", lambda _d, mode: {**_BOARD, "mode": mode})

    def save(board: dict[str, Any], *, root: Path) -> Path:
        d = root / board_key(board["et_date"], board["mode"])
        d.mkdir(parents=True, exist_ok=True)
        (d / "board.json").write_text(json.dumps(board))
        (d / "board.md").write_text("# board\nSPX 6600.25\n")
        return d

    monkeypatch.setattr(nb, "save_board", save)
    monkeypatch.setattr(nb, "build_facts", lambda _b: {"et_date": "2026-09-25", "names": {},
                                                       "kept_total": 0, "dropped_total": 0})
    monkeypatch.setattr(nb, "deadman", pages.append)
    monkeypatch.setattr(arms, "_spawn", _fake_spawn(fail, spawned))
    a = argparse.Namespace(mode=mode, no_deliver=True, no_shadow=False, only_board_facts=False)
    for k, v in opts.items():
        setattr(a, k, v)
    return nb.night(date(2026, 9, 25), a), pages, spawned


def test_orchestrator_builds_the_brief_without_a_failed_specialist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc, pages, spawned = _run_night(tmp_path, monkeypatch, fail={"macro"})
    assert rc == 0 and pages == []
    night = tmp_path / "2026-09-25-weekly"
    brief = (night / "brief.md").read_text()
    assert brief.startswith("_Tonight's brief ran without the macro specialist")
    assert "70% chance" not in brief  # stripped
    assert "### Numbers not found in our data" in brief and "`9999.9`" in brief
    assert "`6600.25`" not in brief
    run = json.loads((night / "run.json").read_text())
    assert run["steps"]["specialists"]["missing_production"] == ["macro"]
    assert run["sessions"]["flows_opus"]["usage"]["total_cost_usd"] == 1.25
    assert run["sessions"]["flows_opus"]["web_calls"] == 0
    assert run["sessions"]["macro_opus"]["ok"] is False
    assert "submit" not in run["steps"]  # --no-deliver
    # 4 specs x 3 arms + judge + graders (the macro opus memo is missing: 3 specs x 2 pairs)
    assert len(spawned) == 12 + 1 + 6
    judge_cmd = next(c for c in spawned if c[0] == "claude" and "the JUDGE" in c[c.index("-p") + 1])
    assert judge_cmd[judge_cmd.index("--effort") + 1] == "max"
    assert "memo_macro_opus.md" not in judge_cmd[judge_cmd.index("-p") + 1]
    assert json.loads((night / "calls_flows_opus_lower.json").read_text())[0]["subject"] == "SPY"
    assert "(medium)" in (night / "memo_flows_opus_lower.md").read_text()  # one step below high
    assert "```json" not in (night / "memo_flows_opus.md").read_text()
    grades = json.loads((night / "grades.json").read_text())
    assert set(grades) == {"flows", "stocks", "sectors"}
    assert all(g["verdict"]["winner"] == "A" for gs in grades.values() for g in gs)
    assert json.loads((night / "picks.json").read_text())["picks"][0]["symbol"] == "XYZ"


def test_orchestrator_judge_failure_is_nonzero_with_dead_man(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc, pages, _ = _run_night(tmp_path, monkeypatch, fail={"judge"}, no_shadow=True)
    assert rc == nb.FAILED
    assert len(pages) == 1 and "judge failed" in pages[0]
    assert not (tmp_path / "2026-09-25-weekly" / "brief.md").exists()


def test_orchestrator_empty_board_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nb, "board_is_empty", lambda _b: True)
    rc, pages, spawned = _run_night(tmp_path, monkeypatch, fail=set())
    assert rc == nb.FAILED and "empty" in pages[0] and spawned == []


# --- modes --------------------------------------------------------------------------------


def test_session_date_per_mode() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    sunday = datetime(2026, 9, 27, 18, 30, tzinfo=et)
    assert nb.session_date("weekly", sunday) == date(2026, 9, 25)  # Friday
    # Good Friday 2026-04-03 is an NYSE holiday: that weekend's weekly briefs Thursday.
    assert nb.session_date("weekly", datetime(2026, 4, 5, 18, 30, tzinfo=et)) == date(2026, 4, 2)
    assert nb.session_date("nightly", datetime(2026, 9, 25, 20, 30, tzinfo=et)) == date(2026, 9, 25)
    assert nb.session_date("nightly", datetime(2026, 9, 26, 20, 30, tzinfo=et)) is None  # Saturday


def test_nightly_on_a_non_trading_day_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setattr(nb, "night", lambda *_a: pytest.fail("ran on a Saturday"))
    monkeypatch.setattr(sys, "argv", ["nightly_brief.py", "--date", "2026-09-26"])
    assert nb.main() == 0


def test_mode_routing_table() -> None:
    nightly, weekly = arms.MODES["nightly"], arms.MODES["weekly"]
    assert nightly["specialists"] == ("flows", "stocks") and not nightly["shadow"]
    assert nightly["judge_effort"] == "high"
    assert weekly["specialists"] == tuple(arms.SPECIALISTS) and weekly["shadow"]
    assert weekly["judge_effort"] == "max"
    assert {m["dedup_key"] for m in arms.MODES.values()} == {"nightly_brief", "weekly_brief"}
    lite = arms.judge_values("nightly")
    assert "## Macro" not in lite["sections"] and "## Sectors" not in lite["sections"]
    assert lite["specialists"] == "flows and stocks" and "top 3" in lite["mode_block"]
    assert "## Macro, rates and inflation" in arms.judge_values("weekly")["sections"]


def test_nightly_lite_runs_flows_and_stocks_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rc, pages, spawned = _run_night(tmp_path, monkeypatch, fail=set(), mode="nightly")
    assert rc == 0 and pages == []
    night = tmp_path / "2026-09-25"
    assert sorted(p.name for p in night.glob("memo_*.md")) == ["memo_flows_opus.md",
                                                               "memo_stocks_opus.md"]
    assert len(spawned) == 2 + 1  # two production specialists + the judge; no shadows, no grader
    judge = next(c for c in spawned if "the JUDGE" in c[c.index("-p") + 1])
    prompt = judge[judge.index("-p") + 1]
    assert judge[judge.index("--effort") + 1] == "high"
    assert "Missing tonight" not in prompt and "## Sectors" not in prompt
    assert "The flows and stocks specialists" in prompt
    assert not (night / "grades.json").exists()
    brief = (night / "brief.md").read_text()
    assert not brief.startswith("_Tonight's brief ran without")
    run = json.loads((night / "run.json").read_text())
    assert run["options"]["mode"] == "nightly" and run["options"]["shadow"] is False
    specialist = next(c for c in spawned if "FLOWS specialist" in c[c.index("-p") + 1])
    assert "NIGHTLY lite brief" in specialist[specialist.index("-p") + 1]
    assert "--filter date=2026-09-25 --filter" in specialist[specialist.index("-p") + 1]


# --- scoped number check ------------------------------------------------------------------


def _scoped_night(tmp_path: Path) -> Path:
    board = {
        "et_date": "2026-09-25",
        "candidates": {"cards": [
            {"symbol": "AAA", "day": {"close": 101.23}},
            {"symbol": "BBB", "day": {"close": 55.5}},
            {"symbol": "ET", "day": {"close": 18.4}},
        ]},
        "index": {"gex": {"SPX": {"spot": 7741.99, "gamma_flip_spot": 7668.84}}},
        "macro": {"market": {"series": {"DGS10": {"value": 5.18}}}},
    }
    (tmp_path / "board.json").write_text(json.dumps(board))
    (tmp_path / "facts.json").write_text(json.dumps({"names": {"BBB": {"facts": [
        {"fact": "raised guidance", "number": "$4.2B"}]}}}))
    (tmp_path / "tools_x.txt").write_text(
        '{"symbol": "AAA", "technicals": {"sma200": 88.8}}\n\nweb page: oil at 91.25 a barrel')
    return tmp_path


def test_scoped_number_check_catches_a_number_next_to_the_wrong_ticker(tmp_path: Path) -> None:
    corpus = verify.night_corpus(_scoped_night(tmp_path), [tmp_path / "tools_x.txt"])
    un = lambda text: verify.unmatched_numbers(text, corpus)  # noqa: E731
    assert un("AAA closed at 101.23, above its 200-day 88.8.") == []
    assert un("BBB closed at 101.23.") == ["101.23"]  # AAA's close, printed next to BBB
    assert un("BBB guided to $4.2B.") == [] and un("AAA guided to $4.2B.") == ["$4.2B"]
    # No ticker anywhere: checked against the market scope (frames + unowned records).
    assert un("The 10-year sits at 5.18 and oil at 91.25.") == []
    assert un("SPX closed at 7,741.99, above the 7,668.84 flip.") == []
    # A name's sentence may cite the macro frame.
    assert un("AAA held up while the 10y hit 5.18.") == []
    # Inherited from the bullet parent / the bold title.
    assert un("**1. BBB — long**\n- Why:\n  - It closed at 101.23.") == ["101.23"]
    assert un("**1. AAA — long**\n- Why:\n  - It closed at 101.23.") == []
    # "16:00 ET" is a time zone, not Energy Transfer.
    assert un("At 16:00 ET AAA printed 101.23.") == []
    # Unscoped (no symbols) is the old global check.
    glob = verify.build_index(jsons=[json.loads((tmp_path / "board.json").read_text())])
    assert verify.unmatched_numbers("BBB closed at 101.23.", glob) == []


def test_number_check_eval_counts_fakes(tmp_path: Path) -> None:
    from research.brief import number_check_eval as ev

    corpus = verify.night_corpus(_scoped_night(tmp_path), [tmp_path / "tools_x.txt"])
    out = ev.evaluate("AAA closed at 101.23. BBB closed at 55.50.", corpus)
    assert out["numbers"] == 2 and out["real_unmatched"] == []
    assert all(f["matched"] == 0 and f["total"] == 2 for f in out["fakes"].values())


# --- scorecard over both layouts ----------------------------------------------------------


def test_scorecard_reads_nightly_and_weekly_dirs(tmp_path: Path) -> None:
    call = {"claim": "c", "kind": "close", "subject": "SPY", "op": ">", "threshold": 100,
            "horizon_sessions": 1}
    nightly, weekly = tmp_path / "2026-09-24", tmp_path / "2026-09-25-weekly"
    for d in (nightly, weekly):
        d.mkdir()
        (d / "calls_flows_opus.json").write_text(json.dumps([call]))
    verdict = {"A": {"accuracy": 4}, "B": {"accuracy": 2}, "winner": "A"}
    (weekly / "grades.json").write_text(json.dumps({"flows": [
        {"shadow": "deepseek", "labels": {"A": "opus", "B": "deepseek"}, "verdict": verdict}]}))
    rows = scorecard.collect(tmp_path, closes=lambda *_a: 101.0, series=lambda *_a: (None, None),
                             today=date(2026, 10, 5))
    opus = rows[("flows", "opus")]
    assert opus["nights"] == {"2026-09-24", "2026-09-25-weekly"}
    assert opus["graded"] == {"2026-09-25-weekly"} and opus["games"] == 1
    assert opus["resolved"] == 2  # both runs' calls resolve
    out = scorecard.render(rows)
    assert "graded" in out


# --- cron wrapper hour guard ---------------------------------------------------------------


def _wrapper(tmp_path: Path, hour: str, *args: str) -> Any:
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "date").write_text(
        '#!/bin/sh\nif [ "$1" = "+%H" ]; then echo "$FAKE_HOUR"; else exec /bin/date "$@"; fi\n')
    # flock "fails" so a matching hour stops at the lock and never reaches Python.
    (bin_dir / "flock").write_text("#!/bin/sh\nexit 1\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    script = Path(__file__).resolve().parent.parent / "tools" / "nightly_brief.sh"
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_HOUR": hour, "TMPDIR": str(tmp_path)}
    return subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True,
                          timeout=30)


def test_wrapper_hour_guard(tmp_path: Path) -> None:
    other = _wrapper(tmp_path, "19", "--at-et-hour", "18", "--mode", "weekly")
    assert other.returncode == 0 and other.stdout == ""  # the other DST slot: silent no-op
    assert not (tmp_path / "hmt-brief-weekly.lock").exists()

    match = _wrapper(tmp_path, "08", "--at-et-hour", "8", "--mode", "weekly")
    assert match.returncode == 0 and "another weekly brief holds the lock" in match.stdout
    assert (tmp_path / "hmt-brief-weekly.lock").exists()

    nightly = _wrapper(tmp_path, "20", "--at-et-hour=20")
    assert "another nightly brief holds the lock" in nightly.stdout
    assert (tmp_path / "hmt-brief-nightly.lock").exists()
