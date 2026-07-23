"""Deep Seek book — nightly LLM driver (LLM_BOOKS_PROGRAM_SPEC_2026-07-23.md §5, increment 2).

One cycle per trading day, post-close: build the deterministic context pack →
one pinned-provider LLM call → markdown report ending in a fenced ```json
decision block → EVERY candidate registered into the book's ledger lanes →
advanced LONG candidates become phone proposals ONLY while the strategy record
is armed (enabled=true). Disabled = the shipped state = a DRY cycle: pack +
report + lane rows, zero proposals, zero pushes — the proving period is free.

Valve rules (spec §2): the pack is the only inbound; this module writes ONLY
ledger rows, a report row, and (armed) proposals through the normal intake.
Shorts are measured in lanes but NEVER advanced to proposals until the
increment-5 chokepoint round ships.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError

from db.base import DEFAULT_DB_PATH
from research.books import BOOKS, archive_book_pack, build_book_pack, register_book_candidates

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# After the flow_decomp report (16:xx ET) so the pack can point at today's.
BOOK_CYCLE_AFTER_ET_HOUR = 17
BOOK_CYCLE_AFTER_ET_MINUTE = 5
# DeepSeek reasoning tokens eat max_tokens (deepseek-llm-lessons) — generous on
# purpose; a length-truncated reply persists as parse_fail, never silently drops.
BOOK_MAX_TOKENS = 8000
BOOK_CALL_TIMEOUT_S = 240
# The research session: iterations of the tool loop (each may carry several
# tool calls) and the hard wall-clock ceiling for the whole session.
BOOK_MAX_TOOL_ITERATIONS = 12
BOOK_CYCLE_TIMEOUT_S = 900
# A crashed session retries on later job ticks, but only this many times before
# the day is stamped with an error report — never an all-night retry storm.
# In-memory on purpose: a backend restart resetting the count is acceptable.
_MAX_ANALYSIS_ATTEMPTS = 3
_ANALYSIS_FAILURES: dict[tuple[str, str, str], int] = {}

_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL)


class BookCandidate(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    direction: Literal["long", "short"] = "long"
    thesis: str = Field(min_length=1)
    bear_case: str = ""
    entry_low: str | None = None
    entry_high: str | None = None
    stop: str | None = None
    targets: list[str] = Field(default_factory=list)
    entry_ref_price: str | None = None
    plan_horizon: str = Field(min_length=2)  # e.g. "5d" — intraday is rejected downstream
    confidence_label: Literal["low", "medium", "high"] | None = None
    advanced: bool = False
    notional_usd: float | None = None  # advanced only; clamped by the strategy record
    seat: str | None = None  # panel books only: bull|bear|macro|risk

    def to_registration(self) -> dict[str, Any]:
        reg: dict[str, Any] = {
            "symbol": self.symbol.strip().upper(),
            "direction": self.direction,
            "thesis": self.thesis,
            "bear_case": self.bear_case,
            "confidence_label": self.confidence_label,
            "plan_horizon": self.plan_horizon,
            "advanced": self.advanced,
            "seat": self.seat,
        }
        if self.entry_low and self.entry_high and self.stop:
            reg["entry_zone"] = {"low": self.entry_low, "high": self.entry_high}
            reg["stop"] = self.stop
            reg["targets"] = self.targets
            reg["grading_basis"] = "plan"
        else:
            reg["entry_ref_price"] = self.entry_ref_price
            reg["grading_basis"] = "horizon_return"
        return reg


class BookDecision(BaseModel):
    candidates: list[BookCandidate] = Field(default_factory=list)
    holds_review: str = ""
    # The analyst's self-maintained desk journal: rewritten every cycle, fed
    # back verbatim in the next night's pack — the book's narrative memory
    # (bounded; lanes + reports remain the mechanical memory).
    journal: str = Field(default="", max_length=4000)


# The founder's charter (book-analyst-charter, 07-23) — shared by every book.
_CHARTER = (
    "You are a senior analyst at the founder's elite trading firm, personally responsible "
    "for the '{book_name}' book. Our goal is to create wealth without losing it — not merely "
    "preserve it. We swing for home runs when they present themselves, but never size large "
    "enough to go belly up.\n\n"
    "It is after hours. Tonight's context pack is below. RESEARCH FIRST: use your tools — "
    "the firm's own data (get_context on symbols, query across app resources, earnings dates, "
    "list_capabilities to see what's readable) and outside data (web_search) — to pressure-test "
    "anything you're considering before you write. Then file your nightly report in markdown: "
    "what you see, what you dug into, your hold/trim review, and your conclusions.\n\n"
    "House rules:\n"
    "- Every thesis must name its falsifier — the level or date that kills it.\n"
    "- Never state a numeric win probability. Confidence is low/medium/high only.\n"
    "- End your report with a '## Desk journal' handoff to tomorrow's you inside the json "
    "block: open theses you're stalking, levels you're waiting on, lessons — max ~3500 chars. "
    "It is the ONLY narrative memory you carry between cycles.\n"
)

_DECISION_CONTRACT = (
    "\nAfter the report, end with EXACTLY ONE fenced ```json block:\n"
    '{"candidates": [{"symbol", "direction", "thesis", "bear_case", "entry_low", '
    '"entry_high", "stop", "targets", "entry_ref_price", "plan_horizon", '
    '"confidence_label", "advanced", "notional_usd"}], "holds_review": "...", '
    '"journal": "..."}\n'
    "Rules for the block:\n"
    "- List EVERY candidate you seriously weighed, not just what you advance — considered "
    "candidates are tracked too. Advancing nothing is a perfectly good night.\n"
    "- advanced=true ONLY for what you actually propose for entry, and it REQUIRES "
    "notional_usd — you size your own book, and your sizing is part of what this study "
    "judges — AND a numeric stop price (e.g. \"314.90\"): once you're filled, that stop "
    "is placed as a RESTING GTC stop order, your only protection between cycles. An "
    "advanced pick missing either is dropped as unfinished work.\n"
    "- Shorts may appear as candidates for tracking, but must have advanced=false (short "
    "execution is not yet unlocked for this book).\n"
    "- Prices are strings; plan_horizon is trading days like '5d' or '15d' (no intraday).\n"
    "- Give either a full plan (entry_low/entry_high/stop/targets) or entry_ref_price alone."
)

_ADVISORY_CONTRACT = (
    "\nThis book is CO-MANAGED: the founder makes every trade decision himself. Your report is "
    "advisory only — review his positions, flag risks and opportunities, but produce NO order "
    "proposals and no candidate list. Do not end with a json block."
)


def _system_prompt(book_name: str, *, advisory: bool) -> str:
    charter = _CHARTER.format(book_name=book_name)
    return charter + (_ADVISORY_CONTRACT if advisory else _DECISION_CONTRACT)


def _extract_decision(raw: str) -> tuple[BookDecision | None, str]:
    """(decision, error). The LAST fenced json block is the contract."""
    blocks = _JSON_BLOCK_RE.findall(raw or "")
    if not blocks:
        return None, "no fenced ```json block found"
    try:
        payload = json.loads(blocks[-1])
    except json.JSONDecodeError as exc:
        return None, f"json parse error: {exc}"
    try:
        return BookDecision.model_validate(payload), ""
    except ValidationError as exc:
        return None, f"schema error: {exc.errors()[:3]}"


async def _run_analysis(llm: Any, system: str, pack: str) -> str:
    """The after-hours research session: an agentic tool loop (read-only
    analyst toolset over our data + web) driven by the proven chat loop,
    collected to the final markdown report text."""
    from ai.chat.loop import LoopError, TextChunk, TextDelta, run_chat_loop
    from ai.llm.base import LLMMessage
    from research.book_tools import analyst_execute, analyst_tool_specs

    convo = [
        LLMMessage(role="system", content=system),
        LLMMessage(role="user", content=pack),
    ]

    async def _consume() -> str:
        parts: list[str] = []
        async for event in run_chat_loop(
            llm,
            convo,
            analyst_tool_specs(),
            execute=analyst_execute,
            approval_tools=frozenset(),  # nothing in the analyst set pauses
            max_iterations=BOOK_MAX_TOOL_ITERATIONS,
            max_tokens=BOOK_MAX_TOKENS,
        ):
            if isinstance(event, (TextChunk, TextDelta)):
                parts.append(event.text)
            elif isinstance(event, LoopError):
                raise RuntimeError(f"analysis loop failed: {event.message}")
        return "".join(parts)

    return await asyncio.wait_for(_consume(), timeout=BOOK_CYCLE_TIMEOUT_S)


async def _complete_once(llm: Any, messages: list[Any]) -> str:
    """One plain completion (no tools) — the repair-retry path."""
    return await asyncio.wait_for(
        asyncio.to_thread(llm.complete, messages, max_tokens=BOOK_MAX_TOKENS),
        timeout=BOOK_CALL_TIMEOUT_S,
    )


async def _analyze_and_extract(
    client: Any, book_name: str, advisory: bool, pack: str
) -> tuple[str, BookDecision | None, str]:
    """Research session + (for decision books) block extraction with ONE repair
    retry. Kept in one unit so the caller's retry/stamp backstop covers every
    LLM call in the cycle — the repair path must not be an unprotected raise."""
    raw = await _run_analysis(client, _system_prompt(book_name, advisory=advisory), pack)
    if advisory:
        return raw, None, ""
    decision, err = _extract_decision(raw)
    if decision is None:
        from ai.llm.base import LLMMessage

        retry = [
            LLMMessage(role="system", content=_system_prompt(book_name, advisory=False)),
            LLMMessage(role="user", content=pack),
            LLMMessage(role="assistant", content=raw or "(empty)"),
            LLMMessage(
                role="user",
                content=(
                    f"Your decision block failed to parse: {err}. Reply with ONLY the "
                    "corrected fenced ```json block, nothing else."
                ),
            ),
        ]
        raw2 = await _complete_once(client, retry)
        decision, err = _extract_decision(raw2)
        if decision is not None:
            raw = f"{raw}\n\n{raw2}"
    return raw, decision, err


def _report_kind(book_id: str) -> str:
    return f"book_{book_id}"


def _gate(now_et: datetime, kind: str, db_path: Path) -> str | None:
    from db.reports import report_exists_for_day

    if now_et.weekday() >= 5:
        return "weekend"
    if (now_et.hour, now_et.minute) < (BOOK_CYCLE_AFTER_ET_HOUR, BOOK_CYCLE_AFTER_ET_MINUTE):
        return "before_window"
    if report_exists_for_day(kind, now_et.date(), dedup_key=kind, db_path=db_path):
        return "already_ran_today"
    return None


async def deliver_report(report: dict[str, Any], book_name: str, db_path: Path) -> None:
    """Deliver one book report: an app/web inbox row + ONE phone push.

    The inbox row is a report_ready notification linking the report (notify
    forces push=False for that kind). The push goes direct via send_alert_push
    (founder 07-23: "a phone push for each report") — that seam ignores the
    flow-lane max_push_per_day kill, which stays untouched. One push per book
    per night by construction (the cycle runs once per ET day)."""
    from signals.notify import notify
    from signals.push import send_alert_push

    await notify(
        kind="report_ready",
        title=report["title"],
        body=str(report.get("body", ""))[:280],
        severity="info",
        ref_type="report",
        ref_id=report["id"],
        action_url=f"/reports/{report['id']}",
        data={"report_id": report["id"], "kind": report["kind"], "book": book_name},
        push=False,
        db_path=db_path,
    )
    try:
        await send_alert_push(
            report["title"],
            str(report.get("body", ""))[:180],
            data={
                "type": "report_ready",
                "report_id": report["id"],
                "kind": report["kind"],
            },
            db_path=db_path,
        )
    except Exception:  # noqa: BLE001 — delivery must never kill a completed cycle
        logger.exception("book report push failed (inbox row already written)")


async def run_book_cycle(
    book_id: str = "deepseek",
    *,
    now_et: datetime | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    llm: Any | None = None,
) -> dict[str, Any]:
    """One nightly analyst cycle. Returns a status dict (every exit is observable).

    Books with a strategy record run the full decision pipeline (lanes +
    proposals-when-armed). The co-managed book (matt) runs the SAME research
    session but advisory-only: report + inbox, no decision block, no lanes,
    no proposals — structurally, not by prompt alone."""
    from ai.llm.factory import get_llm_for_provider
    from ai.llm.mock import MockLLM
    from db.reports import save_report
    from db.strategies import get_strategy

    book = BOOKS[book_id]
    advisory = not book.strategy_id
    kind = _report_kind(book_id)
    now = now_et or datetime.now(_ET)

    gate = _gate(now, kind, db_path)
    if gate:
        return {"status": "skipped", "reason": gate}

    strategy = None
    armed = False
    if not advisory:
        strategy = get_strategy(book.strategy_id, db_path=db_path)
        if strategy is None:
            return {"status": "skipped", "reason": "no_strategy_record"}
        armed = bool(strategy.get("enabled"))

    # In-process driver books run on the pinned DeepSeek seam (the app-default
    # LLM must never silently switch a book's analyst).
    client = llm if llm is not None else get_llm_for_provider("deepseek")
    if isinstance(client, MockLLM):
        return {"status": "skipped", "reason": "llm_unconfigured"}

    pack = build_book_pack(book_id, db_path=db_path)
    if db_path == DEFAULT_DB_PATH:
        # Audit archive is for real cycles only — a test/tmp db_path must never
        # overwrite the day's real pack (the _index_bars prod-gate pattern).
        try:
            archive_book_pack(book_id, pack)
        except OSError:
            logger.exception("book %s pack archive failed (cycle continues)", book_id)

    et_date = now.date().isoformat()
    title = f"{book.name} book · {et_date}"

    try:
        raw, decision, err = await _analyze_and_extract(client, book.name, advisory, pack)
    except Exception as exc:  # noqa: BLE001 — API outage/timeout: bounded retries, then stamp
        key = (str(db_path), kind, et_date)
        attempts = _ANALYSIS_FAILURES.get(key, 0) + 1
        _ANALYSIS_FAILURES[key] = attempts
        if attempts < _MAX_ANALYSIS_ATTEMPTS:
            logger.warning(
                "book %s analysis failed (attempt %d/%d, will retry next tick): %s",
                book_id, attempts, _MAX_ANALYSIS_ATTEMPTS, exc,
            )
            return {"status": "retry", "attempt": attempts, "error": str(exc)}
        # Third strike stamps the day — an honest failure row beats an all-night
        # retry storm, and the inbox shows the cycle died instead of silence.
        _ANALYSIS_FAILURES.pop(key, None)
        report = save_report(
            kind,
            "",
            title,
            f"Analyst session failed after {attempts} attempts: {exc}",
            {"et_date": et_date, "dedup_key": kind, "status": "error", "error": str(exc)},
            format="markdown",
            db_path=db_path,
        )
        await deliver_report(report, book.name, db_path)
        logger.error("book %s cycle stamped as error after %d attempts", book_id, attempts)
        return {"status": "error", "error": str(exc)}
    _ANALYSIS_FAILURES.pop((str(db_path), kind, et_date), None)

    if advisory:
        report = save_report(
            kind,
            "",
            title,
            raw or "(empty report)",
            {"et_date": et_date, "dedup_key": kind, "status": "ok", "advisory": True},
            format="markdown",
            db_path=db_path,
        )
        await deliver_report(report, book.name, db_path)
        logger.info("book %s advisory cycle: report=%s", book_id, report["id"])
        return {"status": "ok", "advisory": True, "report_id": report["id"]}

    if decision is None:
        # zero and dead are different numbers: a failed cycle still leaves a row.
        report = save_report(
            kind,
            "",
            title,
            raw or "(empty completion)",
            {"et_date": et_date, "dedup_key": kind, "status": "parse_fail", "error": err},
            format="markdown",
            db_path=db_path,
        )
        await deliver_report(report, book.name, db_path)
        logger.warning("book %s cycle parse_fail: %s", book_id, err)
        return {"status": "parse_fail", "error": err}

    registration = register_book_candidates(
        book_id,
        [c.to_registration() for c in decision.candidates],
        db_path=db_path,
    )

    proposals: list[dict[str, Any]] = []
    if armed and strategy is not None:
        proposals = await _propose_advanced(book_id, strategy, decision, db_path=db_path)

    report = save_report(
        kind,
        "",
        title,
        raw,
        {
            "et_date": et_date,
            "dedup_key": kind,
            "status": "ok",
            "armed": armed,
            "dry": not armed,
            "candidates": len(decision.candidates),
            "registered": len(registration["registered"]),
            "lane_skipped": registration["skipped"],
            "proposals": proposals,
            "holds_review": decision.holds_review,
            # Fed back verbatim in tomorrow's pack — the book's narrative memory.
            "journal": decision.journal,
        },
        format="markdown",
        db_path=db_path,
    )
    await deliver_report(report, book.name, db_path)
    logger.info(
        "book %s cycle: armed=%s candidates=%d registered=%d proposals=%d report=%s",
        book_id, armed, len(decision.candidates),
        len(registration["registered"]), len(proposals), report["id"],
    )
    return {
        "status": "ok",
        "armed": armed,
        "registered": registration["registered"],
        "proposals": proposals,
        "report_id": report["id"],
    }


async def submit_session_cycle(
    book_id: str,
    report_md: str,
    decision_payload: dict[str, Any],
    *,
    now_et: datetime | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Session-driven books (Fable / Claude Panel): the scheduled Claude Code
    session does its own research and calls this (via tools/book_session_submit.py)
    as its LAST act — one code path with the in-process driver for validation,
    lanes, proposals-when-armed, report row, inbox + push delivery, and the
    once-per-ET-day stamp.

    A schema-invalid decision returns status=invalid WITHOUT stamping the day —
    the session is expected to fix its JSON and re-run the submit."""
    from db.reports import save_report
    from db.strategies import get_strategy

    book = BOOKS[book_id]
    if not book.strategy_id:
        raise ValueError(f"book {book_id!r} is advisory-only — nothing to submit")
    kind = _report_kind(book_id)
    now = now_et or datetime.now(_ET)

    gate = _gate(now, kind, db_path)
    if gate:
        return {"status": "skipped", "reason": gate}

    strategy = get_strategy(book.strategy_id, db_path=db_path)
    if strategy is None:
        return {"status": "skipped", "reason": "no_strategy_record"}
    armed = bool(strategy.get("enabled"))

    try:
        decision = BookDecision.model_validate(decision_payload)
    except ValidationError as exc:
        return {"status": "invalid", "error": f"schema error: {exc.errors()[:5]}"}

    registration = register_book_candidates(
        book_id,
        [c.to_registration() for c in decision.candidates],
        db_path=db_path,
    )
    proposals: list[dict[str, Any]] = []
    if armed:
        proposals = await _propose_advanced(book_id, strategy, decision, db_path=db_path)

    et_date = now.date().isoformat()
    report = save_report(
        kind,
        "",
        f"{book.name} book · {et_date}",
        report_md or "(empty report)",
        {
            "et_date": et_date,
            "dedup_key": kind,
            "status": "ok",
            "armed": armed,
            "dry": not armed,
            "candidates": len(decision.candidates),
            "registered": len(registration["registered"]),
            "lane_skipped": registration["skipped"],
            "proposals": proposals,
            "holds_review": decision.holds_review,
            "journal": decision.journal,
        },
        format="markdown",
        db_path=db_path,
    )
    await deliver_report(report, book.name, db_path)
    logger.info(
        "book %s session submit: armed=%s candidates=%d registered=%d proposals=%d report=%s",
        book_id, armed, len(decision.candidates),
        len(registration["registered"]), len(proposals), report["id"],
    )
    return {
        "status": "ok",
        "armed": armed,
        "registered": registration["registered"],
        "proposals": proposals,
        "report_id": report["id"],
    }


async def _propose_advanced(
    book_id: str,
    strategy: dict[str, Any],
    decision: BookDecision,
    *,
    db_path: Path,
) -> list[dict[str, Any]]:
    """Advanced LONG candidates → the normal proposal intake (armed books only).

    Shorts are hard-excluded here regardless of what the model marked — the
    measurement lanes carry them; execution waits for the increment-5 round."""
    from trading.proposal_intake import TradeCandidate, propose_trade

    # Sanity ceiling, not a sizing clamp: the analyst shapes its own slate, but
    # a malformed decision marking dozens of picks advanced must not turn into
    # dozens of phone proposals (moo is exempt from the pending-count cap).
    max_advanced = 10
    advanced_seen = 0
    results: list[dict[str, Any]] = []
    for cand in decision.candidates:
        if not cand.advanced:
            continue
        advanced_seen += 1
        if advanced_seen > max_advanced:
            logger.warning(
                "book %s advanced %s beyond the %d-per-cycle ceiling — dropped",
                book_id, cand.symbol, max_advanced,
            )
            results.append({"symbol": cand.symbol, "status": "over_ceiling"})
            continue
        if cand.direction != "long":
            logger.warning(
                "book %s advanced SHORT %s blocked (shorts not unlocked)",
                book_id, cand.symbol,
            )
            results.append({"symbol": cand.symbol, "status": "short_blocked"})
            continue
        # Founder 07-23: the analyst sizes its own book — no imposed clamp, no
        # default. An advanced pick without a stated size is unfinished work.
        # The walls that remain are physical: the book's cash, the RiskEngine
        # at the chokepoint, and the founder's approval tap in propose mode.
        if cand.notional_usd is None or Decimal(str(cand.notional_usd)) <= 0:
            logger.warning(
                "book %s advanced %s without notional_usd — no proposal",
                book_id, cand.symbol,
            )
            results.append({"symbol": cand.symbol, "status": "no_size"})
            continue
        # Founder 07-23 (same night): analysts are blind between cycles, so the
        # stated stop is the position's only intraday defense — the stop guard
        # places it as a resting GTC stop once the entry fills. An advanced
        # pick whose stop doesn't parse to a price is unfinished work too.
        stop = _parse_stop_price(cand.stop)
        if stop is None:
            logger.warning(
                "book %s advanced %s without a numeric stop — no proposal",
                book_id, cand.symbol,
            )
            results.append({"symbol": cand.symbol, "status": "no_stop"})
            continue
        notional = Decimal(str(cand.notional_usd))
        outcome = await propose_trade(
            TradeCandidate(
                symbol=cand.symbol.strip().upper(),
                side="buy",
                notional_usd=notional,
                source=f"book_{book_id}",
                rationale=cand.thesis[:300],
                account_id=strategy.get("account_id"),
                entry_style="moo",
                stop_price=stop,
            ),
            db_path=db_path,
        )
        results.append({"symbol": cand.symbol, "status": outcome.get("status")})
    return results


def _parse_stop_price(raw: str | None) -> Decimal | None:
    """Analyst stop string → positive Decimal ("$314.90" / "314.90" / "1,250").
    None when it doesn't parse — prose falsifiers can't arm a resting stop."""
    if not raw:
        return None
    cleaned = str(raw).strip().lstrip("$").replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value if value > 0 else None
