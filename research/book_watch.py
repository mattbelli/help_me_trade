"""Intraday position watch for the LLM paper books — the event channel.

The nightly cycle (research/book_driver) is the books' heartbeat: it runs whether
or not anything happened. This module is the other half — between cycles a book is
blind, and the positions it already owns are the half of the P&L nobody is watching.

Shape (deliberately the cheap one):
  Tier 0  a deterministic sweep over what each book OWNS — its held positions and
          its pending proposals. Runs every poll during market hours, calls no LLM,
          costs nothing. This is where "did something material happen" is decided.
  Tier 1  a NARROW LLM note on the one name that tripped a trigger: no tool loop,
          one completion, ~1.5k tokens. Bounded by BOOK_WATCH_DAILY_BUDGET per book
          per day, which is a law constant — miscalibrating the triggers costs
          missed events, never money.

Scoping the sweep to what the book owns is the whole trick: "what in the market
deserves an LLM?" is unbounded and hard to calibrate, while "did something material
happen to one of my ~8 names?" is bounded, tiny, and the higher-value half —
protecting capital beats finding one more entry.

Every trigger that matches is written to ``book_watch_events`` whether it fired or
was refused by the budget, and the day's log is fed back into the next nightly pack.
The watcher is ADVISORY: it writes notes, never orders. BOOK_WATCH_PROPOSE_ENABLED
is the single arming seam (strategy-off-by-default) and nothing reads it yet.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from db.base import DEFAULT_DB_PATH
from research.books import BOOKS, Book

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# ── Law constants (not deployment toggles) ────────────────────────────────────
# Wakeups per book per trading day. The ceiling is enforced BEFORE the LLM call,
# so a badly-tuned trigger costs missed events, not tokens.
BOOK_WATCH_DAILY_BUDGET = 3
# Never wake on a price we can't stand behind: the account poll writes a snapshot
# every few minutes, and anything older than this is not evidence (daily-bar-trap
# discipline — an old print must never masquerade as "right now").
BOOK_WATCH_SNAPSHOT_MAX_AGE_MIN = 20
# Trigger cut-lines.
BOOK_WATCH_STOP_PROXIMITY_PCT = 2.0   # price within this % ABOVE the plan's stop
BOOK_WATCH_DRAWDOWN_PCT = 8.0         # position down this % from its average entry
BOOK_WATCH_TARGET_PROXIMITY_PCT = 1.0 # price within this % BELOW the first target
BOOK_WATCH_PROPOSAL_DRIFT_PCT = 3.0   # pending proposal's price left its entry zone by this %
# A GTC entry that never fills is the quietest way for a book to be wrong: it is
# not a position and not a pending proposal, so every other surface reads "flat".
# Fires on this many trading days of resting, and again each multiple after.
BOOK_WATCH_RESTING_ENTRY_DAYS = 3
BOOK_WATCH_NEWS_MAX = 5               # headlines carried into the note
# The narrow call: one completion, no tools.
BOOK_WATCH_MAX_TOKENS = 1500
BOOK_WATCH_CALL_TIMEOUT_S = 120
# ARMING SEAM — when this goes True the watcher's exit/trim verdicts become
# proposals through trading/proposal_intake. Shipped False: notes only, no order
# path exists in this module at all (strategy-off-by-default).
BOOK_WATCH_PROPOSE_ENABLED = False

# Most urgent first — the budget spends on the top of this list, so a book that
# trips four triggers at once wakes for the stop, not for the headline.
_TRIGGER_SEVERITY = {
    "stop_proximity": 0,
    "drawdown": 1,
    "news": 2,
    "earnings_imminent": 3,
    "target_proximity": 4,
    "resting_entry": 5,
    "proposal_drift": 6,
}

_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL)
_VERDICTS = ("hold", "trim", "exit", "adjust_stop", "watch")


def watch_lane(book_id: str) -> str:
    """The book's own lane_stats lane. Per-book on purpose: a pooled counter
    can't tell "this book is flat" from "this book's watcher died"."""
    return f"book_watch:{book_id}"


@dataclass(frozen=True)
class WatchTrigger:
    symbol: str
    trigger: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def severity(self) -> int:
        return _TRIGGER_SEVERITY.get(self.trigger, 99)


def _pct(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _parse_price(raw: Any) -> float | None:
    """Ledger/plan price string → float ("$314.90" / "314.90" / "1,250")."""
    if raw is None:
        return None
    try:
        return float(str(raw).strip().lstrip("$").replace(",", ""))
    except ValueError:
        return None


def _snapshot_age_minutes(captured_at: Any, now: datetime) -> float | None:
    try:
        stamp = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (now - stamp).total_seconds() / 60.0


def _book_snapshot(book: Book, db_path: Path) -> dict[str, Any] | None:
    """The book's OWN latest snapshot, or None.

    get_latest_snapshot falls back to the newest run for the broker when the
    account has never polled — which would hand this book another account's
    positions. Verify the ledger account id before believing anything in it.
    """
    from db.sqlite import get_latest_snapshot

    snap = get_latest_snapshot("alpaca", account_id=book.account_id, db_path=db_path)
    if snap is None:
        return None
    if str(snap.get("account_id") or "") != book.account_id:
        logger.warning(
            "book %s: snapshot belongs to account %r, not %r — refusing it",
            book.book_id, snap.get("account_id"), book.account_id,
        )
        return None
    return snap


def _open_plans(book: Book, db_path: Path) -> dict[str, dict[str, Any]]:
    """symbol → the book's own open plan (stop / targets / thesis) from its
    headline ledger lane. Empty for advisory books, which have no lanes."""
    if not book.headline_origin:
        return {}
    from db.idea_outcomes import list_ledger_rows

    plans: dict[str, dict[str, Any]] = {}
    result = list_ledger_rows(
        origin=book.headline_origin, status="open", limit=200, db_path=db_path
    )
    for row in result.get("rows") or []:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol or symbol in plans:
            continue  # newest-first ordering: the first row is the live plan
        targets = row.get("targets")
        if targets is None:
            try:
                targets = json.loads(row.get("targets_json") or "[]")
            except (json.JSONDecodeError, TypeError):
                targets = []
        plans[symbol] = {
            "stop": _parse_price(row.get("stop")),
            "entry_low": _parse_price(row.get("entry_low")),
            "entry_high": _parse_price(row.get("entry_high")),
            "targets": [p for p in (_parse_price(t) for t in targets or []) if p],
            "direction": row.get("direction"),
            "idea_id": row.get("id"),
        }
    return plans


def _news_since(symbols: list[str], since: datetime, db_path: Path) -> dict[str, list[dict]]:
    """symbol → headlines published since ``since`` (UTC). Fail-soft: a broken
    news read must never stop the price triggers."""
    if not symbols:
        return {}
    from db.news import recent_news

    stamp = since.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        rows = recent_news(symbols=symbols, since=stamp, limit=100, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.exception("book watch: news read failed")
        return {}
    wanted = set(symbols)
    out: dict[str, list[dict]] = {}
    for row in rows:
        for sym in row.get("symbols") or []:
            if sym in wanted:
                out.setdefault(sym, []).append(row)
    return out


def evaluate_triggers(
    book: Book,
    snapshot: dict[str, Any],
    *,
    now: datetime,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[WatchTrigger]:
    """The Tier-0 sweep: deterministic, zero LLM, zero network.

    Returns every trigger that matched, most urgent first. Pure over
    (snapshot, ledger, news, earnings) — the same inputs always yield the same
    list, which is what makes the log auditable after the fact.
    """
    plans = _open_plans(book, db_path)
    positions = snapshot.get("positions") or []
    held = sorted({str(p.get("symbol") or "").strip().upper() for p in positions} - {""})
    triggers: list[WatchTrigger] = []

    for pos in positions:
        symbol = str(pos.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        price = _pct(pos.get("current_price"))
        entry = _pct(pos.get("avg_entry_price"))
        plan = plans.get(symbol) or {}

        # 1. Stop proximity — the plan's own falsifier is close. Long books only:
        #    the chokepoint cannot short, so a short plan here would be measurement
        #    only and its "stop above" reading would be backwards.
        stop = plan.get("stop")
        if price and stop and plan.get("direction") != "short" and price > stop:
            gap_pct = (price / stop - 1) * 100
            if gap_pct <= BOOK_WATCH_STOP_PROXIMITY_PCT:
                triggers.append(
                    WatchTrigger(
                        symbol,
                        "stop_proximity",
                        f"{price:,.2f} is {gap_pct:.1f}% above the plan stop {stop:,.2f}",
                        {"price": price, "stop": stop, "gap_pct": round(gap_pct, 2)},
                    )
                )

        # 2. Drawdown from the book's own average entry.
        if price and entry and entry > 0:
            move_pct = (price / entry - 1) * 100
            if move_pct <= -BOOK_WATCH_DRAWDOWN_PCT:
                triggers.append(
                    WatchTrigger(
                        symbol,
                        "drawdown",
                        f"down {abs(move_pct):.1f}% from the book's entry {entry:,.2f}",
                        {"price": price, "avg_entry": entry, "move_pct": round(move_pct, 2)},
                    )
                )

        # 3. First target in reach — trimming is a decision too.
        targets = plan.get("targets") or []
        if price and targets:
            first = min(targets)
            if first > 0 and price >= first * (1 - BOOK_WATCH_TARGET_PROXIMITY_PCT / 100):
                triggers.append(
                    WatchTrigger(
                        symbol,
                        "target_proximity",
                        f"{price:,.2f} has reached the plan's first target {first:,.2f}",
                        {"price": price, "target": first},
                    )
                )

    # 4. Fresh news on a held name, since this session's open.
    session_open = now.astimezone(_ET).replace(hour=9, minute=30, second=0, microsecond=0)
    for symbol, items in _news_since(held, session_open, db_path).items():
        heads = [str(i.get("headline") or "") for i in items[:BOOK_WATCH_NEWS_MAX]]
        triggers.append(
            WatchTrigger(
                symbol,
                "news",
                f"{len(items)} headline(s) since the open: {heads[0][:120]}"
                if heads
                else f"{len(items)} headline(s) since the open",
                {"count": len(items), "headlines": heads},
            )
        )

    # 5. Earnings landing before the next nightly cycle can react.
    triggers.extend(_earnings_triggers(held, now=now, db_path=db_path))

    # 6. A GTC entry still resting unfilled — intent parked with no exposure.
    triggers.extend(_resting_entry_triggers(book, now=now, db_path=db_path))

    # 7. A pending proposal whose price has walked away from its entry zone.
    triggers.extend(_proposal_triggers(book, plans, now=now, db_path=db_path))

    triggers.sort(key=lambda t: (t.severity, t.symbol))
    return triggers


def _earnings_triggers(
    held: list[str], *, now: datetime, db_path: Path
) -> list[WatchTrigger]:
    if not held:
        return []
    from db.earnings_calendar import earnings_hour, next_earnings_date

    today = now.astimezone(_ET).date()
    cutoff = (today + timedelta(days=1)).isoformat()
    out: list[WatchTrigger] = []
    for symbol in held:
        try:
            nxt = next_earnings_date(symbol, today.isoformat(), db_path=db_path)
        except Exception:  # noqa: BLE001
            logger.exception("book watch: earnings lookup failed for %s", symbol)
            continue
        if not nxt or nxt > cutoff:
            continue
        # The calendar's bmo/amc hour is directionally biased on this data
        # (edgar-free-earnings-source) — carry the DATE as fact and the hour as
        # a hint the analyst can discount, never as the basis of the trigger.
        hour = None
        try:
            hour = earnings_hour(symbol, nxt, db_path=db_path)
        except Exception:  # noqa: BLE001
            logger.exception("book watch: earnings hour lookup failed for %s", symbol)
        out.append(
            WatchTrigger(
                symbol,
                "earnings_imminent",
                f"reports {nxt}" + (f" ({hour}, hour is a hint only)" if hour else ""),
                {"date": nxt, "hour_hint": hour},
            )
        )
    return out


def _resting_entry_triggers(
    book: Book, *, now: datetime, db_path: Path
) -> list[WatchTrigger]:
    """Approved buys the broker still holds unfilled.

    A GTC limit entry rests until it fills or is cancelled — it never expires
    itself into view. So a book can carry five figures of approved intent for
    weeks while the account reads flat and the analyst, seeing no position,
    quietly stops thinking about the name. This is the trigger that says the
    quiet part: N days resting, price still X% away from your limit.
    """
    from db.base import connect as _connect
    from signals.fusion.lifecycle import _trading_days_between

    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT symbol, qty, limit_price, submitted_at FROM order_executions "
                "WHERE account_id = ? AND side = 'buy' AND status = 'submitted' "
                "ORDER BY submitted_at",
                (book.account_id,),
            ).fetchall()
    except Exception:  # noqa: BLE001
        logger.exception("book watch: resting-entry read failed")
        return []

    today = now.astimezone(_ET).date()
    out: list[WatchTrigger] = []
    for row in rows:
        try:
            submitted = datetime.fromisoformat(
                str(row["submitted_at"]).replace("Z", "+00:00")
            ).astimezone(_ET).date()
        except (TypeError, ValueError):
            continue
        resting = _trading_days_between(submitted, today)
        # Fires as it crosses the line, then on each multiple — a parked order
        # is a standing question, not a one-time alert, but it must not become
        # a daily tax on the wakeup budget.
        if resting < BOOK_WATCH_RESTING_ENTRY_DAYS or resting % BOOK_WATCH_RESTING_ENTRY_DAYS:
            continue
        symbol = str(row["symbol"]).strip().upper()
        limit = _pct(row["limit_price"])
        price = _last_price(symbol, db_path)
        gap = f"{(price / limit - 1) * 100:+.1f}% away" if price and limit else "distance unknown"
        out.append(
            WatchTrigger(
                symbol,
                "resting_entry",
                f"approved buy of {row['qty']} at limit {limit} has rested "
                f"{resting} trading days unfilled — last {price} ({gap})",
                {
                    "qty": row["qty"],
                    "limit_price": limit,
                    "last_price": price,
                    "resting_days": resting,
                    "submitted_at": row["submitted_at"],
                },
            )
        )
    return out


def _proposal_triggers(
    book: Book, plans: dict[str, dict[str, Any]], *, now: datetime, db_path: Path
) -> list[WatchTrigger]:
    """A pending proposal is a decision the founder hasn't tapped yet. If the
    price has left the plan's entry zone while it waited, the tap is now on a
    different trade than the one the analyst wrote up."""
    from db.paper_orders import list_pending_proposals

    try:
        rows = list_pending_proposals(account_id=book.account_id, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.exception("book watch: pending proposal read failed")
        return []

    out: list[WatchTrigger] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        plan = plans.get(symbol)
        if not symbol or not plan:
            continue
        high = plan.get("entry_high")
        low = plan.get("entry_low")
        if not high or not low:
            continue
        price = _last_price(symbol, db_path)
        if price is None:
            continue
        if price > high * (1 + BOOK_WATCH_PROPOSAL_DRIFT_PCT / 100):
            drift = (price / high - 1) * 100
            side = "above"
        elif price < low * (1 - BOOK_WATCH_PROPOSAL_DRIFT_PCT / 100):
            drift = (1 - price / low) * 100
            side = "below"
        else:
            continue
        out.append(
            WatchTrigger(
                symbol,
                "proposal_drift",
                f"pending proposal's entry zone {low:,.2f}–{high:,.2f} is "
                f"{drift:.1f}% {side} the current {price:,.2f}",
                {
                    "proposal_id": row.get("id"),
                    "price": price,
                    "entry_low": low,
                    "entry_high": high,
                },
            )
        )
    return out


def _last_price(symbol: str, db_path: Path) -> float | None:
    """Newest close we hold for a symbol. Cache-only on purpose — the watcher
    runs inside the poll loop and must never make a blocking network call."""
    from db.base import connect as _connect

    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT c FROM daily_bars WHERE symbol = ? ORDER BY date DESC LIMIT 1",
                (symbol.upper(),),
            ).fetchone()
    except Exception:  # noqa: BLE001 — a missing bars cache disables this trigger, loudly
        logger.exception("book watch: last-price read failed for %s", symbol)
        return None
    return _pct(row["c"]) if row else None


# ── Tier 1: the narrow note ───────────────────────────────────────────────────

_WATCH_CHARTER = (
    "You are the intraday risk watcher for the '{book_name}' book at the founder's "
    "trading firm. A deterministic trigger woke you about ONE position. You are not "
    "the book's analyst: you do not open new trades and you do not re-pitch the "
    "thesis. Your job is to judge whether this development threatens the position "
    "the book already holds, and to say so in a few sentences the founder can act "
    "on from his phone.\n\n"
    "Rules:\n"
    "- Never state a numeric win probability. Confidence is low/medium/high only.\n"
    "- Name the specific level or fact that would change your mind.\n"
    "- A false alarm called plainly is worth more than a hedged paragraph. If "
    "nothing here actually threatens the position, the verdict is 'hold' and the "
    "note is two sentences.\n"
    "- You only see what is below — no tools, no browsing. If the evidence is too "
    "thin to judge, say so and return 'watch'.\n"
)

# Kept OUT of the formatted charter: it carries literal JSON braces, and
# str.format would read them as fields (the book_driver contract split).
_WATCH_CONTRACT = (
    "\nEnd with EXACTLY ONE fenced ```json block:\n"
    '{"verdict": "hold|trim|exit|adjust_stop|watch", "urgency": "low|medium|high", '
    '"reason": "one sentence"}'
)


def build_watch_pack(
    book: Book, trigger: WatchTrigger, snapshot: dict[str, Any], *, db_path: Path
) -> str:
    """The narrow evidence block — one position, one trigger. Deliberately small:
    this is the whole reason the event lane is cheap."""
    lines = [
        f"# Watch trigger — {book.name} · {trigger.symbol}",
        "",
        f"**Trigger:** `{trigger.trigger}` — {trigger.detail}",
        f"**Snapshot captured:** {snapshot.get('captured_at')}",
        "",
        "## The position",
    ]
    pos = next(
        (
            p
            for p in snapshot.get("positions") or []
            if str(p.get("symbol") or "").upper() == trigger.symbol
        ),
        None,
    )
    if pos:
        def num(key: str) -> str:
            value = _pct(pos.get(key))
            return f"{value:,.2f}" if value is not None else "n/a"

        lines.append(
            f"- qty {pos.get('qty')} · avg entry {num('avg_entry_price')}"
            f" · current {num('current_price')} · unrealized {num('unrealized_pl')}"
        )
    else:
        lines.append("_no open position — this trigger is about a pending proposal_")

    plan = (_open_plans(book, db_path) or {}).get(trigger.symbol)
    lines.append("\n## The book's own plan")
    if plan:
        targets = ", ".join(f"{t:,.2f}" for t in plan.get("targets") or []) or "none stated"
        lines.append(
            f"- entry zone {plan.get('entry_low')}–{plan.get('entry_high')}"
            f" · stop {plan.get('stop')} · targets {targets}"
        )
    else:
        lines.append("_no open plan on the book's ledger lane for this name_")

    if trigger.evidence:
        lines.append("\n## Trigger evidence\n")
        lines.append("```json")
        lines.append(json.dumps(trigger.evidence, indent=2, default=str)[:2000])
        lines.append("```")
    return "\n".join(lines)


def _extract_verdict(raw: str) -> tuple[str, str]:
    """(verdict, reason). Unparseable output is 'watch' — an unreadable note must
    never read as an all-clear."""
    blocks = _JSON_BLOCK_RE.findall(raw or "")
    if not blocks:
        return "watch", "no json block in the note"
    try:
        payload = json.loads(blocks[-1])
    except json.JSONDecodeError as exc:
        return "watch", f"json parse error: {exc}"
    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in _VERDICTS:
        return "watch", f"unknown verdict {verdict!r}"
    return verdict, str(payload.get("reason") or "")


async def _watch_note(llm: Any, book: Book, pack: str) -> str:
    from ai.llm.base import LLMMessage

    messages = [
        LLMMessage(
            role="system",
            content=_WATCH_CHARTER.format(book_name=book.name) + _WATCH_CONTRACT,
        ),
        LLMMessage(role="user", content=pack),
    ]
    return await asyncio.wait_for(
        asyncio.to_thread(llm.complete, messages, max_tokens=BOOK_WATCH_MAX_TOKENS),
        timeout=BOOK_WATCH_CALL_TIMEOUT_S,
    )


async def run_book_watch(
    book_id: str,
    *,
    now: datetime | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    llm: Any | None = None,
) -> dict[str, Any]:
    """One watch pass for one book. Every exit is observable — the returned dict
    and the lane_stats counters are how "quiet" is told apart from "broken"."""
    from db.book_watch import attach_outcome, claim_watch_event, fired_today
    from db.lane_stats import bump, gauge
    from trading.market_calendar import is_trading_day
    from trading.market_hours import is_market_open

    book = BOOKS[book_id]
    now = now or datetime.now(timezone.utc)
    now_et = now.astimezone(_ET)
    market_day = now_et.date().isoformat()

    if not is_trading_day(now_et.date()) or not is_market_open(now_et):
        return {"status": "skipped", "reason": "market_closed"}

    def meter(counter: str, amount: int = 1) -> None:
        bump(watch_lane(book.book_id), counter, amount, date=market_day, db_path=db_path)

    meter("passes")

    snapshot = _book_snapshot(book, db_path)
    if snapshot is None:
        meter("no_snapshot")
        return {"status": "skipped", "reason": "no_snapshot"}
    age = _snapshot_age_minutes(snapshot.get("captured_at"), now)
    if age is None or age > BOOK_WATCH_SNAPSHOT_MAX_AGE_MIN:
        # An old price is not evidence. Counted, never acted on.
        meter("stale_snapshot")
        return {"status": "skipped", "reason": "stale_snapshot", "age_min": age}

    # Gauged, not summed: "how many names was this book carrying" is a level,
    # and a pass that watched nothing is the answer to "is it broken or quiet".
    watched = len(snapshot.get("positions") or [])
    gauge(watch_lane(book.book_id), "positions_watched", watched, date=market_day, db_path=db_path)

    triggers = evaluate_triggers(book, snapshot, now=now, db_path=db_path)
    if not triggers:
        return {"status": "ok", "watched": watched, "matched": 0, "fired": []}
    meter("matched", len(triggers))

    fired: list[dict[str, Any]] = []
    deduped = 0
    for trigger in triggers:
        over_budget = fired_today(book.book_id, market_day, db_path=db_path) >= (
            BOOK_WATCH_DAILY_BUDGET
        )
        event_id = claim_watch_event(
            book_id=book.book_id,
            market_day=market_day,
            symbol=trigger.symbol,
            trigger=trigger.trigger,
            decision="budget_dropped" if over_budget else "fired",
            detail=trigger.detail,
            evidence=trigger.evidence,
            db_path=db_path,
        )
        if event_id is None:
            deduped += 1  # this trigger already fired for this name today
            continue
        if over_budget:
            meter("budget_dropped")
            logger.info(
                "book %s watch: %s/%s matched but the day's budget is spent",
                book.book_id, trigger.symbol, trigger.trigger,
            )
            continue

        outcome = await _fire_wakeup(book, trigger, snapshot, llm=llm, db_path=db_path)
        attach_outcome(
            event_id,
            report_id=outcome.get("report_id"),
            verdict=outcome.get("verdict"),
            db_path=db_path,
        )
        meter("fired")
        fired.append({"symbol": trigger.symbol, "trigger": trigger.trigger, **outcome})

    if deduped:
        meter("deduped", deduped)
    return {
        "status": "ok",
        "watched": watched,
        "matched": len(triggers),
        "deduped": deduped,
        "fired": fired,
    }


async def _fire_wakeup(
    book: Book,
    trigger: WatchTrigger,
    snapshot: dict[str, Any],
    *,
    llm: Any | None,
    db_path: Path,
) -> dict[str, Any]:
    """The wakeup itself: one narrow completion → a report row → inbox + push.

    An LLM failure still ships the deterministic evidence to the founder. The
    trigger fired; he should see it even when the note didn't render."""
    from ai.llm.factory import get_llm_for_provider
    from ai.llm.mock import MockLLM
    from db.reports import save_report
    from research.book_driver import deliver_report

    pack = build_watch_pack(book, trigger, snapshot, db_path=db_path)
    title = f"{book.name} watch · {trigger.symbol} · {trigger.trigger}"

    client = llm if llm is not None else get_llm_for_provider("deepseek")
    if isinstance(client, MockLLM):
        return {"status": "llm_unconfigured", "report_id": None, "verdict": None}

    try:
        raw = await _watch_note(client, book, pack)
        verdict, reason = _extract_verdict(raw)
        status = "ok"
    except Exception as exc:  # noqa: BLE001 — a dead note must not hide a live trigger
        logger.exception("book %s watch note failed for %s", book.book_id, trigger.symbol)
        raw = f"The watcher's note failed to render: `{exc}`\n\n{pack}"
        verdict, reason, status = "watch", f"note failed: {exc}", "note_failed"

    report = save_report(
        f"book_{book.book_id}_watch",
        trigger.symbol,
        title,
        raw,
        {
            "book": book.book_id,
            "trigger": trigger.trigger,
            "detail": trigger.detail,
            "evidence": trigger.evidence,
            "verdict": verdict,
            "reason": reason,
            "status": status,
            "advisory": True,
        },
        format="markdown",
        db_path=db_path,
    )
    await deliver_report(
        {**report, "body": f"{verdict.upper()} — {reason or trigger.detail}"},
        book.name,
        db_path,
    )
    logger.info(
        "book %s watch fired: %s/%s → %s (report %s)",
        book.book_id, trigger.symbol, trigger.trigger, verdict, report["id"],
    )
    return {"status": status, "report_id": report["id"], "verdict": verdict, "reason": reason}


async def run_all_book_watches(
    *, now: datetime | None = None, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any]:
    """The job entry point — every book, sequentially (one LLM at a time)."""
    out: dict[str, Any] = {}
    for book_id in BOOKS:
        try:
            out[book_id] = await run_book_watch(book_id, now=now, db_path=db_path)
        except Exception as exc:  # noqa: BLE001 — one book must not stop the others
            logger.exception("book %s watch pass failed", book_id)
            out[book_id] = {"status": "error", "error": str(exc)}
    return out
