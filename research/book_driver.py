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

from pydantic import BaseModel, Field, ValidationError, field_validator

from db.base import DEFAULT_DB_PATH
from research.books import BOOKS, archive_book_pack, build_book_pack, register_book_candidates

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# After the flow_decomp report (16:xx ET) so the pack can point at today's.
BOOK_CYCLE_AFTER_ET_HOUR = 17
BOOK_CYCLE_AFTER_ET_MINUTE = 5
# DeepSeek V4 Flash thinking is ON by default (high effort) and shares this
# budget with visible content. 8000 was OUR clamp, not the provider's — it
# truncated the nightly report mid-sentence from 07-31 (v4-flash switch) through
# 08-14 (measured: finish_reason=length, bodies 261–1271 chars). V4 max output
# is 384k; the 900s cycle wall-clock is the real spend backstop, not this number.
BOOK_MAX_TOKENS = 384_000
# Must cover one long think+report turn. 240s sat under the HTTP timeout once
# that moved with the token uncap, and the repair path would lose the race.
BOOK_CALL_TIMEOUT_S = 600
# The research session: iterations of the tool loop (each may carry several
# tool calls) and the hard wall-clock ceiling for the whole session.
BOOK_MAX_TOOL_ITERATIONS = 12
BOOK_CYCLE_TIMEOUT_S = 900
# The local model (LM Studio on the founder's PC, ~55 tok/s) measured 746s for a full
# cycle on 2026-08-16 — inside 900s but with no margin; a busier night must not
# become a timeout, so it gets double the wall-clock. Cost is free either way.
BOOK_CYCLE_TIMEOUT_LOCAL_S = 1800
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
    notional_usd: float | None = None  # advanced only; the analyst sizes its own book
    seat: str | None = None  # panel books only: bull|bear|macro|risk
    # Closed mechanism family (Keeper 2). Required on every candidate — considered
    # rows are the sample; unknown values fail parse so the session can repair.
    mechanism: str
    failure_modes: list[str] = Field(default_factory=list)
    # Advanced-only execution choice (founder 07-24: order selection is part
    # of what the study judges):
    #   moo   — market at the next opening auction (default);
    #   limit — GTC limit at limit_price, regular hours;
    #   ext   — GTC limit at limit_price, extended/overnight-eligible (fills
    #           near the decision price; stop first actionable next open).
    entry_type: Literal["moo", "limit", "ext"] = "moo"
    limit_price: str | None = None  # required when entry_type is limit/ext

    @field_validator("mechanism", mode="before")
    @classmethod
    def _closed_mechanism(cls, value: Any) -> str:
        from research.book_mechanisms import normalize_mechanism

        normalized = normalize_mechanism(value)
        if normalized is None:
            raise ValueError(
                "mechanism must be one of the closed vocabulary in the pack"
            )
        return normalized

    @field_validator("failure_modes", mode="before")
    @classmethod
    def _closed_failure_modes(cls, value: Any) -> list[str]:
        from research.book_mechanisms import normalize_failure_modes

        return normalize_failure_modes(value)

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
            "mechanism": self.mechanism,
            "failure_modes": self.failure_modes,
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


class BookOrderChange(BaseModel):
    """An analyst's order-management verb on ITS OWN book — a phone proposal
    like any entry, resolved against what rests at the broker at approval
    (trading/book_orders.py)."""

    symbol: str = Field(min_length=1, max_length=12)
    # "malformed" is never emitted by the analyst: the decision validator
    # substitutes it for an unparseable item so the refusal reaches the
    # report + next night's pack instead of vanishing into a log line.
    action: Literal["move_stop", "cancel_entry", "trim", "malformed"]
    stop_price: str | None = None  # move_stop: the new trigger
    qty: int | None = None  # trim: whole shares to sell at the next open
    reason: str = ""


class BookDecision(BaseModel):
    candidates: list[BookCandidate] = Field(default_factory=list)
    order_changes: list[BookOrderChange] = Field(default_factory=list)
    holds_review: str = ""

    @field_validator("order_changes", mode="before")
    @classmethod
    def _drop_malformed_changes(cls, value: Any) -> list[Any]:
        # A malformed verb is the analyst's unfinished work, not a reason to
        # re-run the whole night's analysis: drop it (logged) and keep the rest.
        if not isinstance(value, list):
            return []
        kept: list[Any] = []
        for item in value:
            if isinstance(item, dict):
                # The likely slip is a JSON number where the contract says
                # string — coerce rather than drop.
                item = dict(item)
                if isinstance(item.get("stop_price"), int | float):
                    item["stop_price"] = str(item["stop_price"])
                if isinstance(item.get("qty"), float) and item["qty"].is_integer():
                    item["qty"] = int(item["qty"])
            try:
                kept.append(BookOrderChange.model_validate(item))
            except ValidationError:
                logger.warning("book decision: dropped malformed order change %r", item)
                kept.append(
                    BookOrderChange(
                        symbol=str((item or {}).get("symbol") or "?")[:12] if isinstance(item, dict) else "?",
                        action="malformed",
                        reason=str(item)[:200],
                    )
                )
        return kept
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
    "anything you're considering before you write. Then file your nightly report in markdown, "
    "in the structure given under 'Report format' below.\n\n"
    "House rules:\n"
    "- Every thesis must name its falsifier — the level or date that kills it.\n"
    "- Never state a numeric win probability or a numeric belief-confidence (no 0.7). "
    "Confidence is low/medium/high only; the pack's mechanism ledger is scored by code "
    "after ideas resolve.\n"
    "- Every candidate (advanced AND considered) MUST name a mechanism from the closed "
    "list in tonight's pack. Unknown mechanism = unfinished work, dropped at parse.\n"
    # Was: "end your report with a '## Desk journal' ... inside the json block" — a markdown
    # heading inside JSON, when `journal` is already a plain string field of the contract.
    "- The 'journal' field of the decision block is your handoff to tomorrow's you: open theses "
    "you're stalking, levels you're waiting on, lessons — max ~3500 chars. It is the ONLY "
    "narrative memory you carry between cycles.\n"
    # Report-format block added 2026-08-07 (SERV_HANDOFF_reports): presentation only —
    # research procedure, house rules, sizing and the decision contract are unchanged.
    # Kept identical to research/book_prompts/fable.md so the two books stay comparable.
    "\nReport format. Write for a phone: one column, read at night. Lead with "
    "the answer. Use these '##' sections, in this order, omitting any that are empty:\n"
    "1. '## TL;DR' — 3-5 sentences covering the whole night: book P&L, what you're advancing, "
    "and the single most important thing that changed. This is promoted into the report's "
    "standfirst, so it must stand alone.\n"
    "2. \"## Tonight's calls\" — what you are advancing and what you want done. One table: "
    "| symbol | entry | stop | targets | size | confidence |. If you are advancing nothing, say "
    "so in one line — that is a fine night.\n"
    "3. '## What changed' — the delta since your last cycle: fills, stops that moved, theses "
    "that broke, orders that rotted. Skip anything that is merely still true.\n"
    "4. '## Positions' — your hold/trim review, one row per position: "
    "| symbol | qty @ cost | now | stop | verdict |, with a sentence beneath each naming its "
    "falsifier — the level or date that kills it.\n"
    "5. '## Passed on' — what you seriously weighed and did not advance, with the one reason "
    "each. This is graded for calibration, so it is not optional.\n"
    "6. '## Market frame' — regime, tape, and the outside research that informed the above. "
    "Context goes last, not first.\n"
    "Style:\n"
    "- Write every change signed and with its unit: +6.1%, -$417, $835.67. Gains render green "
    "and losses red automatically, but only when the sign is present.\n"
    "- State confidence (low/medium/high) inline where you make a call, not only in the "
    "decision block.\n"
    "- Bold the symbol that opens a bullet. Keep paragraphs to ~4 sentences.\n"
    "- Tables are rendered as stacked cards on a phone — use them freely for repeating fields, "
    "and keep them to 6 columns or fewer.\n"
    "- Never paste raw JSON, tool output, or your decision block into the prose.\n"
    "- No '#' title line — the system supplies the title and date.\n"
)

_DECISION_CONTRACT = (
    "\nAfter the report, end with EXACTLY ONE fenced ```json block:\n"
    '{"candidates": [{"symbol", "direction", "thesis", "bear_case", "entry_low", '
    '"entry_high", "stop", "targets", "entry_ref_price", "plan_horizon", '
    '"confidence_label", "advanced", "notional_usd", "entry_type", "limit_price", '
    '"mechanism", "failure_modes"}], '
    '"order_changes": [{"symbol", "action", "stop_price", "qty", "reason"}], '
    '"holds_review": "...", "journal": "..."}\n'
    "Rules for the block:\n"
    "- List EVERY candidate you seriously weighed, not just what you advance — considered "
    "candidates are tracked too. Advancing nothing is a perfectly good night.\n"
    "- Every candidate MUST include mechanism from the pack's closed list (aliases like "
    "gap-fill are accepted). failure_modes is an optional list of at most 3 ids from that "
    "same section; unknown ids are dropped, the candidate still registers.\n"
    "- Never write a numeric belief-confidence for a mechanism family. The pack table is "
    "scored by code after the idea tracker resolves a row; n below 10 is insufficient.\n"
    "- advanced=true ONLY for what you actually propose for entry, and it REQUIRES "
    "notional_usd — you size your own book, and your sizing is part of what this study "
    "judges — AND a numeric stop price (e.g. \"314.90\"): once you're filled, that stop "
    "is placed as a RESTING GTC stop order, your only protection between cycles. An "
    "advanced pick missing either is dropped as unfinished work.\n"
    "- You also choose HOW to enter (entry_type — order selection is judged too): "
    "\"moo\" = market at the next opening auction (default); \"limit\" = GTC limit at "
    "your limit_price, regular hours; \"ext\" = GTC limit at your limit_price, also "
    "eligible for pre/post/overnight sessions — use it to act NEAR tonight's decision "
    "price instead of paying tomorrow's open, accepting that your stop cannot act "
    "before the next regular open. limit/ext REQUIRE a numeric limit_price. Your "
    "standing GTC orders and resting stops appear in your pack's open-orders section "
    "every night — review them.\n"
    "- order_changes is how you manage what you already have; each becomes a phone "
    "proposal the founder approves like an entry, and acts on whatever is resting at "
    "approval time. action \"move_stop\" (+ numeric stop_price) re-triggers EVERY "
    "protective stop resting on that symbol in your book at the new price — below the market, "
    "or it is refused; \"cancel_entry\" pulls your working GTC entry on that symbol; "
    "\"trim\" (+ whole-share qty) sells that many held shares market at the next open "
    "(the remainder stays stopped). Give a one-line reason on each. Do not also ask "
    "for these in prose — the block is what the desk executes.\n"
    "- Shorts may appear as candidates for tracking, but must have advanced=false (short "
    "execution is not yet unlocked for this book).\n"
    "- Prices are strings; plan_horizon is trading days like '5d' or '15d' (no intraday).\n"
    "- Give either a full plan (entry_low/entry_high/stop/targets) or entry_ref_price alone."
)

_ADVISORY_CONTRACT = (
    "\nThis book is CO-MANAGED: the founder makes every trade decision himself. Your report is "
    "advisory only — review his positions, flag risks and opportunities, but produce NO order "
    "proposals and no candidate list. Do not end with a json block.\n"
    # The report format above is shared with the deciding books, so its
    # decision-block references have to be switched off here rather than left to
    # contradict this contract.
    "The report format above still applies, minus anything that assumes a decision block: skip "
    "'## Tonight's calls' and '## Passed on', and ignore the journal instruction — you carry no "
    "journal. Keep the TL;DR, What changed, Positions and Market frame sections."
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


def _strip_decision_block(raw: str) -> str:
    """The reader-facing body: the model's stream minus the decision block we
    already parsed out of it.

    _DECISION_CONTRACT REQUIRES the report to end with exactly one fenced json
    block, so the model emitting it is CORRECT behaviour and no prompt change can
    remove it — but ``raw`` was then saved verbatim as the report body, making
    the block 92% of the 2026-08-06 Deep Seek report and 71% of 07-30. The
    session-driven books never had this: they submit report and decision as two
    separate arguments (submit_session_cycle), which is why Fable reads clean.

    Only the LAST block goes — that is the one _extract_decision consumed; a json
    block the analyst quoted inside its own prose is prose, and stays.
    """
    matches = list(_JSON_BLOCK_RE.finditer(raw or ""))
    if not matches:
        return raw or ""
    last = matches[-1]
    return (raw[: last.start()] + raw[last.end() :]).strip()


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

    timeout = (
        BOOK_CYCLE_TIMEOUT_LOCAL_S
        if getattr(llm, "provider", "") == "lmstudio"
        else BOOK_CYCLE_TIMEOUT_S
    )
    return await asyncio.wait_for(_consume(), timeout=timeout)


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


def _gate(
    now_et: datetime, kind: str, db_path: Path, *, paused: bool = False
) -> str | None:
    from db.reports import report_exists_for_day

    if paused:
        # Checked HERE so "paused" means the same thing on every path into a
        # cycle — the in-process job, a session submit, or a manual run — rather
        # than living only in whatever scheduler happens to launch that book.
        return "paused"
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

    gate = _gate(now, kind, db_path, paused=book.paused)
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
    order_changes: list[dict[str, Any]] = []
    if armed and strategy is not None:
        proposals = await _propose_advanced(book_id, strategy, decision, db_path=db_path)
        order_changes = await _propose_order_changes(
            book_id, strategy, decision, db_path=db_path
        )

    report = save_report(
        kind,
        "",
        title,
        _strip_decision_block(raw) or "(no prose — the model emitted only its decision block)",
        {
            "et_date": et_date,
            "dedup_key": kind,
            "status": "ok",
            "armed": armed,
            "dry": not armed,
            "candidates": len(decision.candidates),
            # The plans as DATA, so a reader renders entry/stop/target cards
            # instead of regexing prices and tickers back out of prose. A new key
            # rather than widening "candidates": that one has meant a COUNT in
            # every stored report, and changing its type would leave clients
            # type-checking it forever.
            "candidate_plans": [c.model_dump() for c in decision.candidates],
            "registered": len(registration["registered"]),
            "lane_skipped": registration["skipped"],
            "proposals": proposals,
            "order_changes": order_changes,
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
        "order_changes": order_changes,
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

    gate = _gate(now, kind, db_path, paused=book.paused)
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
    order_changes: list[dict[str, Any]] = []
    if armed:
        proposals = await _propose_advanced(book_id, strategy, decision, db_path=db_path)
        order_changes = await _propose_order_changes(
            book_id, strategy, decision, db_path=db_path
        )

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
            "candidate_plans": [c.model_dump() for c in decision.candidates],
            "registered": len(registration["registered"]),
            "lane_skipped": registration["skipped"],
            "proposals": proposals,
            "order_changes": order_changes,
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
        "order_changes": order_changes,
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
        # Execution choice is the analyst's (founder 07-24). A limit/ext pick
        # whose limit price doesn't parse is unfinished work, same doctrine
        # as a missing size or stop.
        if cand.entry_type in ("limit", "ext"):
            limit = _parse_stop_price(cand.limit_price)  # same $/comma parsing
            if limit is None:
                logger.warning(
                    "book %s advanced %s (%s) without a numeric limit_price — no proposal",
                    book_id, cand.symbol, cand.entry_type,
                )
                results.append({"symbol": cand.symbol, "status": "no_limit_price"})
                continue
            entry_style = "book_limit" if cand.entry_type == "limit" else "book_ext"
            order_type = "limit"
        else:
            limit = None
            entry_style = "moo"
            order_type = "market"
        notional = Decimal(str(cand.notional_usd))
        outcome = await propose_trade(
            TradeCandidate(
                symbol=cand.symbol.strip().upper(),
                side="buy",
                notional_usd=notional,
                order_type=order_type,
                limit_price=limit,
                source=f"book_{book_id}",
                rationale=cand.thesis[:300],
                account_id=strategy.get("account_id"),
                entry_style=entry_style,
                stop_price=stop,
            ),
            db_path=db_path,
        )
        results.append({"symbol": cand.symbol, "status": outcome.get("status")})
    return results


async def _propose_order_changes(
    book_id: str,
    strategy: dict[str, Any],
    decision: BookDecision,
    *,
    db_path: Path,
) -> list[dict[str, Any]]:
    """The analyst's order-management verbs → phone proposals (armed books only).
    Each resolves against the broker at APPROVAL; here we only validate shape
    (a numeric stop / whole-share qty) and hand off — the same "unfinished work
    is dropped" doctrine as advanced picks."""
    from trading.book_orders import propose_cancel_entry, propose_move_stop, propose_trim

    results: list[dict[str, Any]] = []
    account_id = strategy.get("account_id")
    source = f"book_{book_id}"
    for change in decision.order_changes[:10]:
        symbol = change.symbol.strip().upper()
        reason = change.reason[:300] or None
        if change.action == "malformed":
            results.append(
                {"symbol": symbol, "action": change.action, "status": "malformed",
                 "reason": f"did not parse: {change.reason[:120]}"}
            )
            continue
        if change.action == "move_stop":
            stop = _parse_stop_price(change.stop_price)
            if stop is None:
                results.append({"symbol": symbol, "action": change.action, "status": "no_stop"})
                continue
            outcome = await propose_move_stop(
                symbol=symbol, stop_price=stop, account_id=account_id,
                source=source, reason=reason, db_path=db_path,
            )
        elif change.action == "cancel_entry":
            outcome = await propose_cancel_entry(
                symbol=symbol, account_id=account_id, source=source,
                reason=reason, db_path=db_path,
            )
        else:  # trim
            if change.qty is None or change.qty <= 0:
                results.append({"symbol": symbol, "action": change.action, "status": "no_qty"})
                continue
            outcome = await propose_trim(
                symbol=symbol, qty=Decimal(change.qty), account_id=account_id,
                source=source, reason=reason, db_path=db_path,
            )
        if outcome.get("status") != "proposed":
            logger.warning(
                "book %s order change %s %s → %s", book_id, change.action, symbol,
                outcome.get("reason") or outcome.get("status"),
            )
        entry = {"symbol": symbol, "action": change.action, "status": outcome.get("status")}
        if outcome.get("reason"):
            entry["reason"] = str(outcome["reason"])[:200]
        results.append(entry)
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
