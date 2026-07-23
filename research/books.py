"""LLM paper-books program — book registry + ledger-lane registration.

Spec: References/LLM_BOOKS_PROGRAM_SPEC_2026-07-23.md. One-way valve (spec §2):
this module only WRITES idea-ledger rows (measurement) and READS app state for
context packs — nothing here touches proposals, the fusion bus, or any
perception store. Proposal intake is the driver's own separate step.

Lane map (spec §4, plus the /considered amendment): a book's ADVANCED ideas go
to its headline lane ``book:<id>``; everything else it weighed goes to a
journal-class lane — panel candidates to their seat lane ``book:panel/<seat>``,
solo-book candidates to ``book:<id>/considered``. Journal lanes feed per-lane
calibration but never the headline win-rate (calibration-counts-everything).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.idea_outcomes import register_idea

logger = logging.getLogger(__name__)

PANEL_SEATS = ("bull", "bear", "macro", "risk")


@dataclass(frozen=True)
class Book:
    book_id: str
    name: str
    account_id: str
    # None for Matt + LLM: no strategy record, no machine ideas, snapshot only.
    strategy_id: str | None
    seats: tuple[str, ...] = ()

    @property
    def headline_origin(self) -> str | None:
        return f"book:{self.book_id}" if self.strategy_id else None

    def journal_origin(self, seat: str | None) -> str | None:
        if not self.strategy_id:
            return None
        if self.seats:
            return f"book:{self.book_id}/{seat}" if seat in self.seats else None
        return f"book:{self.book_id}/considered"


BOOKS: dict[str, Book] = {
    "deepseek": Book("deepseek", "Deep Seek", "acct_alpaca_deepseek", "book_deepseek"),
    "fable": Book("fable", "Fable", "acct_alpaca_fable", "book_fable"),
    "panel": Book("panel", "Claude Panel", "acct_alpaca_open", "book_panel", PANEL_SEATS),
    "matt": Book("matt", "Matt + LLM", "acct_fc78907f4bda", None),
}

# Every lane this program writes — config/idea_engine.py cooldowns and the
# db/idea_outcomes JOURNAL_ORIGINS additions must cover exactly these
# (asserted in tests/test_llm_books.py so the three modules can't drift).
BOOK_HEADLINE_ORIGINS = frozenset(
    b.headline_origin for b in BOOKS.values() if b.headline_origin
)
BOOK_JOURNAL_ORIGINS = frozenset(
    {f"book:panel/{seat}" for seat in PANEL_SEATS}
    | {"book:deepseek/considered", "book:fable/considered"}
)
ALL_BOOK_ORIGINS = BOOK_HEADLINE_ORIGINS | BOOK_JOURNAL_ORIGINS


def register_book_candidates(
    book_id: str,
    candidates: list[dict[str, Any]],
    *,
    as_of: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Register a book cycle's full candidate list into its ledger lanes.

    Each candidate dict: symbol, direction, thesis, bear_case, confidence_label,
    plan_horizon, and either a full plan (entry_zone {low,high} + stop + targets)
    or entry_ref_price (graded as horizon_return). Panel candidates carry
    ``seat``; ``advanced: true`` marks what the book actually proposed — an
    advanced panel idea lands in BOTH its seat lane and the headline lane.

    Per-candidate failures skip, never raise — driver output is LLM-authored
    and one malformed entry must not drop the batch. Returns counts + ids.
    """
    book = BOOKS.get(str(book_id or "").strip())
    if book is None:
        raise ValueError(f"unknown book {book_id!r}")
    if not book.strategy_id:
        raise ValueError(f"book {book.book_id!r} takes no machine ideas")

    registered: list[int] = []
    skipped = 0
    for cand in candidates:
        if not isinstance(cand, dict):
            skipped += 1
            continue
        seat = cand.get("seat")
        advanced = bool(cand.get("advanced"))
        origins: list[str] = []
        journal = book.journal_origin(seat if seat in book.seats else None)
        if book.seats:
            # Panel: every candidate needs a valid seat for its journal row.
            if journal:
                origins.append(journal)
            elif not advanced:
                logger.warning(
                    "book %s candidate %s skipped — invalid seat %r",
                    book.book_id, cand.get("symbol"), seat,
                )
                skipped += 1
                continue
        elif not advanced and journal:
            origins.append(journal)
        if advanced and book.headline_origin:
            origins.append(book.headline_origin)

        wrote_any = False
        for origin in origins:
            idea_id = register_idea(
                origin=origin,
                signal_family="llm_book",
                symbol=str(cand.get("symbol", "")),
                direction=str(cand.get("direction", "long")),
                entry_zone=cand.get("entry_zone"),
                stop=cand.get("stop"),
                targets=cand.get("targets"),
                thesis=str(cand.get("thesis", "")),
                bear_case=str(cand.get("bear_case", "")),
                confidence_label=cand.get("confidence_label"),
                as_of=as_of,
                plan_horizon=cand.get("plan_horizon"),
                grading_basis=str(
                    cand.get("grading_basis")
                    or ("plan" if cand.get("entry_zone") else "horizon_return")
                ),
                entry_ref_price=cand.get("entry_ref_price"),
                source_ref={
                    "book": book.book_id,
                    "seat": seat if seat in book.seats else None,
                    "advanced": advanced,
                },
                db_path=db_path,
            )
            if idea_id is not None:
                registered.append(idea_id)
                wrote_any = True
        if not wrote_any:
            skipped += 1

    return {"registered": registered, "skipped": skipped}


# ── Context pack (spec §5.1) — deterministic markdown, no LLM, DB reads only ──
# The valve's INBOUND side: mechanical state flows into the pack; nothing here
# writes anywhere. Every section is fail-soft — a missing table yields an
# honest "unavailable" line, never a fabricated number and never a dead pack.

_INDEX_SYMBOLS = ("SPY", "QQQ", "IWM")
_HOT_LANE_CAP = 15
_LANE_RECENT_CAP = 3


def _fmt_money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_qty(value: Any) -> str:
    try:
        q = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{q:,.4f}".rstrip("0").rstrip(".") if q % 1 else f"{q:,.0f}"


def _section(title: str, build: Any) -> str:
    try:
        body = build()
    except Exception as exc:  # noqa: BLE001 — nightly unattended: partial pack > no pack
        logger.exception("book pack section %r failed", title)
        body = f"_unavailable ({type(exc).__name__})_"
    return f"## {title}\n\n{body}\n"


def _account_section(book: Book, db_path: Path) -> str:
    from db.account_scope import resolve_perf_scope
    from db.analytics import performance_dashboard
    from db.sqlite import get_latest_snapshot

    snap = get_latest_snapshot("alpaca", account_id=book.account_id, db_path=db_path)
    if snap is None:
        return "_no snapshot for this account yet_"
    acct = snap.get("account") or {}
    lines = [
        f"- portfolio value: {_fmt_money(acct.get('portfolio_value'))}"
        f" · cash: {_fmt_money(acct.get('cash'))}"
        f" · buying power: {_fmt_money(acct.get('buying_power'))}",
        f"- snapshot captured: {snap.get('captured_at', 'n/a')}",
    ]
    # Same source as the Lab's DAY tile (api/performance_routes.py day-pnl).
    try:
        scope = resolve_perf_scope(book.account_id)
        daily = (
            performance_dashboard(
                account_id=scope.ledger_account_id,
                real_only=scope.real_only,
                paper_only=scope.paper_only,
                period="day",
            ).get("daily_realized")
            or []
        )
        entry = daily[-1] if daily else None
        raw = (entry.get("realized") or entry.get("net_amount")) if entry else None
        lines.append(
            f"- day realized P&L (net-cash): {_fmt_money(raw)}"
            if raw is not None
            else "- day realized P&L: no activity today"
        )
    except Exception:  # noqa: BLE001
        logger.exception("book pack day-pnl failed for %s", book.book_id)
        lines.append("- day realized P&L: _unavailable_")

    positions = snap.get("positions") or []
    try:
        pv = float(acct.get("portfolio_value") or 0)
    except (TypeError, ValueError):
        pv = 0.0
    lines.append(f"\n### Positions ({len(positions)})\n")
    if positions:
        lines.append("| symbol | qty | side | avg entry | current | unreal P&L | weight |")
        lines.append("|---|---|---|---|---|---|---|")
        for p in positions:
            try:
                weight = f"{float(p.get('market_value') or 0) / pv * 100:.1f}%" if pv else "n/a"
            except (TypeError, ValueError):
                weight = "n/a"
            lines.append(
                f"| {p.get('symbol')} | {_fmt_qty(p.get('qty'))} | {p.get('side')}"
                f" | {_fmt_money(p.get('avg_entry_price'))} | {_fmt_money(p.get('current_price'))}"
                f" | {_fmt_money(p.get('unrealized_pl'))} | {weight} |"
            )
    else:
        lines.append("_none — the book is flat_")

    orders = snap.get("open_orders") or []
    if orders:
        lines.append(f"\n### Open orders ({len(orders)})\n")
        for o in orders:
            lines.append(
                f"- {o.get('side')} {_fmt_qty(o.get('qty'))} {o.get('symbol')}"
                f" ({o.get('order_type')}, {o.get('status')},"
                f" filled {_fmt_qty(o.get('filled_qty'))})"
            )
    return "\n".join(lines)


def _proposals_section(book: Book, db_path: Path) -> str:
    from db.paper_orders import list_pending_proposals

    rows = list_pending_proposals(account_id=book.account_id, db_path=db_path)
    if not rows:
        return "_none pending_"
    return "\n".join(
        f"- [{r.get('id')}] {r.get('side')} {_fmt_qty(r.get('qty'))} {r.get('symbol')}"
        f" — {r.get('summary')} (expires {r.get('expires_at')})"
        for r in rows
    )


def _lanes_section(book: Book, db_path: Path) -> str:
    from db.idea_outcomes import OPEN_STATUSES, list_ledger_rows

    origins = sorted(
        o for o in ALL_BOOK_ORIGINS if o == book.headline_origin or o.startswith(f"book:{book.book_id}/")
    )
    lines: list[str] = []
    for origin in origins:
        result = list_ledger_rows(origin=origin, limit=200, db_path=db_path)
        rows = result.get("rows") or []
        if not rows:
            lines.append(f"- `{origin}`: no ideas yet")
            continue
        open_rows = [r for r in rows if r.get("status") in OPEN_STATUSES]
        resolved = [r for r in rows if r.get("status") not in OPEN_STATUSES]
        recent = ", ".join(
            f"{r.get('symbol')} {r.get('status')}"
            + (f" {float(r['ret_pct']):+.1f}%" if r.get("ret_pct") is not None else "")
            for r in resolved[:_LANE_RECENT_CAP]
        )
        lines.append(
            f"- `{origin}`: {len(open_rows)} open · {len(resolved)} resolved of {result.get('total')}"
            + (f" — recent: {recent}" if recent else "")
        )
    return "\n".join(lines) if lines else "_no lanes for this book_"


def _index_bars(sym: str, db_path: Path) -> list[dict[str, Any]]:
    from datetime import date, timedelta

    from research.bars import get_daily_bars

    # get_daily_bars live-fetches cache gaps from Alpaca. Only allow that on the
    # real production DB — a tmp/test db_path must never reach the network
    # (CI hangs on network calls; see composer-full-suite-hangs).
    if db_path != DEFAULT_DB_PATH:
        return []
    end = date.today()
    return get_daily_bars(sym, end - timedelta(days=10), end)


def _market_frame_section(db_path: Path) -> str:
    from db.regime import recent_observations
    from db.reports import recent_reports_by_kind
    from signals.fusion.attention import hot_lane_rows
    from signals.regime import market_regime

    lines: list[str] = []
    for sym in _INDEX_SYMBOLS:
        try:
            bars = _index_bars(sym, db_path)
        except Exception:  # noqa: BLE001
            logger.exception("book pack index bars failed for %s", sym)
            bars = []
        if len(bars) >= 2:
            pct = (bars[-1]["c"] / bars[-2]["c"] - 1) * 100
            # Bar date printed on purpose: the reader must never guess whether
            # the last bar is today's close or a prior session (daily-bar trap).
            lines.append(
                f"- {sym}: {bars[-1]['c']:,.2f} ({pct:+.2f}% vs prior close, bar {bars[-1]['d']})"
            )
        else:
            lines.append(f"- {sym}: _no daily bars available_")

    regime = market_regime(db_path=db_path)
    if regime:
        lines.append(
            f"- regime: term spread {regime.get('term_spread'):+.2f}"
            f" (10y {regime.get('dgs10'):.2f} / 2y {regime.get('dgs2'):.2f})"
        )
    else:
        lines.append("- regime: _unavailable_")
    vix_obs = recent_observations("VIXCLS", limit=5, db_path=db_path)
    if vix_obs:
        trail = " ← ".join(f"{v:.1f}" for _d, v in vix_obs)
        lines.append(f"- VIX: {trail} (newest first, through {vix_obs[0][0]})")
    else:
        lines.append("- VIX: _unavailable_")

    hot = hot_lane_rows(db_path=db_path)
    if hot:
        # Mechanical-idea attention first — "position" rows are mostly other
        # books' holdings and would otherwise crowd out the interesting names.
        hot = sorted(hot, key=lambda r: r["reason"] == "position")
        shown = ", ".join(f"{r['symbol']} ({r['reason']})" for r in hot[:_HOT_LANE_CAP])
        extra = f" +{len(hot) - _HOT_LANE_CAP} more" if len(hot) > _HOT_LANE_CAP else ""
        lines.append(f"- fusion hot lane: {shown}{extra}")
    else:
        lines.append("- fusion hot lane: empty")

    reports = recent_reports_by_kind("flow_decomp", limit=1, db_path=db_path)
    if reports:
        r = reports[0]
        lines.append(
            f"- flow decomposition report: {r.get('title')}"
            f" (generated {r.get('generated_at')}, report id {r.get('id')})"
        )
    else:
        lines.append("- flow decomposition report: none yet")
    return "\n".join(lines)


def _earnings_section(book: Book, db_path: Path) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from db.earnings_calendar import earnings_hour, next_earnings_date
    from db.sqlite import get_latest_snapshot

    snap = get_latest_snapshot("alpaca", account_id=book.account_id, db_path=db_path)
    held = sorted(
        {str(p.get("symbol", "")).upper() for p in (snap or {}).get("positions") or []} - {""}
    )
    if not held:
        return "_no held names_"
    today_et = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    lines = []
    for sym in held:
        nxt = next_earnings_date(sym, today_et, db_path=db_path)
        if nxt:
            hour = earnings_hour(sym, nxt, db_path=db_path)
            lines.append(f"- {sym}: {nxt}" + (f" ({hour})" if hour else ""))
        else:
            lines.append(f"- {sym}: no upcoming date on the calendar")
    return "\n".join(lines)


def build_book_pack(book_id: str, *, db_path: Path = DEFAULT_DB_PATH) -> str:
    """Render the nightly markdown context pack for one book (spec §5.1)."""
    from datetime import datetime, timezone

    book = BOOKS.get(str(book_id or "").strip())
    if book is None:
        raise ValueError(f"unknown book {book_id!r}")

    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    parts = [
        f"# Context pack — {book.name} ({book.book_id})\n",
        f"Generated {generated} · account `{book.account_id}` · paper\n",
        _section("Account", lambda: _account_section(book, db_path)),
        _section("Pending proposals", lambda: _proposals_section(book, db_path)),
    ]
    # Matt + LLM is snapshot-only (spec §5): no lanes exist and none may render.
    if book.headline_origin:
        parts.append(_section("Your ledger lanes", lambda: _lanes_section(book, db_path)))
    parts.append(_section("Market frame", lambda: _market_frame_section(db_path)))
    parts.append(_section("Upcoming earnings (held names)", lambda: _earnings_section(book, db_path)))
    return "\n".join(parts)
