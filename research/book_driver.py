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
from decimal import Decimal
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

    def to_registration(self) -> dict[str, Any]:
        reg: dict[str, Any] = {
            "symbol": self.symbol.strip().upper(),
            "direction": self.direction,
            "thesis": self.thesis,
            "bear_case": self.bear_case,
            "confidence_label": self.confidence_label,
            "plan_horizon": self.plan_horizon,
            "advanced": self.advanced,
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


def _system_prompt(book_name: str) -> str:
    return (
        f"You manage '{book_name}', a real paper-trading book in a forward-test study. "
        "You receive a nightly context pack (your account, your open ledger lanes, the market "
        "frame). Write a concise markdown report: what you see, what you considered, your "
        "hold/trim review of existing positions, and what (if anything) you advance to propose.\n\n"
        "Rules:\n"
        "- Every thesis must name its falsifier — the level or date that kills it; for full plans "
        "that falsifier IS the stop.\n"
        "- Never state a numeric win probability. Use confidence_label low/medium/high only.\n"
        "- Shorts may appear as candidates for tracking, but must have advanced=false (short "
        "execution is not yet unlocked for this book).\n"
        "- List EVERY candidate you seriously weighed, not just what you advance — considered "
        "candidates are tracked too. Advancing nothing is a perfectly good night.\n"
        "- Prices are strings; plan_horizon is trading days like '5d' or '15d' (no intraday).\n\n"
        "End the report with EXACTLY ONE fenced ```json block:\n"
        '{"candidates": [{"symbol", "direction", "thesis", "bear_case", "entry_low", '
        '"entry_high", "stop", "targets", "entry_ref_price", "plan_horizon", '
        '"confidence_label", "advanced", "notional_usd"}], "holds_review": "..."}\n'
        "Give either a full plan (entry_low/entry_high/stop/targets) or entry_ref_price alone."
    )


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


async def _acomplete(llm: Any, messages: list[Any]) -> str:
    return await asyncio.wait_for(
        llm.acomplete(messages, max_tokens=BOOK_MAX_TOKENS),
        timeout=BOOK_CALL_TIMEOUT_S,
    )


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


async def run_book_cycle(
    book_id: str = "deepseek",
    *,
    now_et: datetime | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    llm: Any | None = None,
) -> dict[str, Any]:
    """One nightly cycle. Returns a status dict (every exit is observable)."""
    from ai.llm.factory import get_llm_for_provider
    from ai.llm.mock import MockLLM
    from db.reports import save_report
    from db.strategies import get_strategy

    book = BOOKS[book_id]
    if not book.strategy_id:
        raise ValueError(f"book {book_id!r} has no driver")
    kind = _report_kind(book_id)
    now = now_et or datetime.now(_ET)

    gate = _gate(now, kind, db_path)
    if gate:
        return {"status": "skipped", "reason": gate}

    strategy = get_strategy(book.strategy_id, db_path=db_path)
    if strategy is None:
        return {"status": "skipped", "reason": "no_strategy_record"}
    armed = bool(strategy.get("enabled"))

    client = llm if llm is not None else get_llm_for_provider(book_id)
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

    from ai.llm.base import LLMMessage

    messages = [
        LLMMessage(role="system", content=_system_prompt(book.name)),
        LLMMessage(role="user", content=pack),
    ]
    raw = await _acomplete(client, messages)
    decision, err = _extract_decision(raw)
    if decision is None:
        # ONE repair retry with the specific error echoed (the house pattern).
        retry = messages + [
            LLMMessage(role="assistant", content=raw or "(empty)"),
            LLMMessage(
                role="user",
                content=(
                    f"Your decision block failed to parse: {err}. Reply with ONLY the "
                    "corrected fenced ```json block, nothing else."
                ),
            ),
        ]
        raw2 = await _acomplete(client, retry)
        decision, err = _extract_decision(raw2)
        if decision is not None:
            raw = f"{raw}\n\n{raw2}"

    et_date = now.date().isoformat()
    title = f"{book.name} book · {et_date}"

    if decision is None:
        # zero and dead are different numbers: a failed cycle still leaves a row.
        save_report(
            kind,
            "",
            title,
            raw or "(empty completion)",
            {"et_date": et_date, "dedup_key": kind, "status": "parse_fail", "error": err},
            format="markdown",
            db_path=db_path,
        )
        logger.warning("book %s cycle parse_fail: %s", book_id, err)
        return {"status": "parse_fail", "error": err}

    registration = register_book_candidates(
        book_id,
        [c.to_registration() for c in decision.candidates],
        db_path=db_path,
    )

    proposals: list[dict[str, Any]] = []
    if armed:
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
        },
        format="markdown",
        db_path=db_path,
    )
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

    cap = Decimal(str(strategy["notional_usd"]))
    results: list[dict[str, Any]] = []
    for cand in decision.candidates:
        if not cand.advanced:
            continue
        if cand.direction != "long":
            logger.warning(
                "book %s advanced SHORT %s blocked (shorts not unlocked)",
                book_id, cand.symbol,
            )
            results.append({"symbol": cand.symbol, "status": "short_blocked"})
            continue
        notional = cap
        if cand.notional_usd is not None and Decimal(str(cand.notional_usd)) > 0:
            notional = min(cap, Decimal(str(cand.notional_usd)))
        outcome = await propose_trade(
            TradeCandidate(
                symbol=cand.symbol.strip().upper(),
                side="buy",
                notional_usd=notional,
                source=f"book_{book_id}",
                rationale=cand.thesis[:300],
                account_id=strategy.get("account_id"),
            ),
            db_path=db_path,
        )
        results.append({"symbol": cand.symbol, "status": outcome.get("status")})
    return results
