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
from research.book_mechanisms import normalize_failure_modes, normalize_mechanism

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
    # Not running nightly right now. The book stays fully registered — its lanes,
    # origins and ledger history are untouched — so pausing is one flag and one
    # commented cron line, and unpausing is the same two edits back. The
    # dead-man skips paused books; without that a pause means a warning push
    # every night for a report nobody expects.
    paused: bool = False

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
    # PAUSED 2026-07-30 (founder): the panel's four Opus seats cost 8-10x the solo
    # books (07-29: 84 min, 35M tokens) and it is being held while the solo books
    # forward-test. Its crontab line is commented out to match.
    "panel": Book(
        "panel", "Claude Panel", "acct_alpaca_open", "book_panel", PANEL_SEATS, paused=True
    ),
    # PAUSED 2026-07-30 (founder): the account holds nothing, so a nightly
    # advisory pass reviews an empty book — and the co-managed idea belongs in
    # CHAT, where the founder can actually go back and forth, not in an
    # unprompted once-a-night report. See card matt-llm-belongs-in-chat.
    "matt": Book("matt", "Matt + LLM", "acct_fc78907f4bda", None, paused=True),
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
    plan_horizon, optional ``mechanism`` / ``failure_modes`` (closed vocab;
    unknown mechanism stored as NULL so the idea still lands), and either a
    full plan (entry_zone {low,high} + stop + targets) or entry_ref_price
    (graded as horizon_return). Panel candidates carry ``seat``; ``advanced:
    true`` marks what the book actually proposed — an advanced panel idea
    lands in BOTH its seat lane and the headline lane.

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
        mechanism = normalize_mechanism(cand.get("mechanism"))
        failure_modes = normalize_failure_modes(cand.get("failure_modes"))
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
                mechanism=mechanism,
                failure_modes=failure_modes or None,
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


def _fmt_trigger(order: dict) -> str:
    """Price levels of a resting order, as ' — stop $X / limit $Y' (empty for market)."""
    parts = [
        f"{label} {_fmt_money(order.get(key))}"
        for key, label in (("stop_price", "stop"), ("limit_price", "limit"))
        if order.get(key) is not None
    ]
    return f" — {' / '.join(parts)}" if parts else ""


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
            trigger = _fmt_trigger(o)
            tif = str(o.get("time_in_force") or "").upper()
            lines.append(
                f"- {o.get('side')} {_fmt_qty(o.get('qty'))} {o.get('symbol')}"
                f"{trigger} ({o.get('order_type')}"
                f"{', ' + tif if tif else ''}, {o.get('status')},"
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


def _book_lane_origins(book: Book) -> list[str]:
    return sorted(
        o
        for o in ALL_BOOK_ORIGINS
        if o == book.headline_origin or o.startswith(f"book:{book.book_id}/")
    )


def _lanes_section(book: Book, db_path: Path) -> str:
    from db.idea_outcomes import OPEN_STATUSES, list_ledger_rows

    origins = _book_lane_origins(book)
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


def _mechanism_section(book: Book, db_path: Path) -> str:
    from research.book_mechanisms import render_mechanism_ledger

    return render_mechanism_ledger(_book_lane_origins(book), db_path=db_path)


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

    # The macro calendar shipped 2026-08-11 onto the `market` perception resource but
    # NOT onto this pack — and the pack is what a book reads FIRST. Measured on the
    # 08-11 packs: no CPI line, on the eve of CPI. The one book that mentioned it had
    # gone to the open web for a date sitting in our own store. Same miss as
    # research-engine-bypassed-perception: a source is not wired until every consumer
    # surface has it.
    try:
        from datetime import datetime as _dt

        from signals.macro_substrate import event_calendar
        from trading.market_hours import _ET

        # ET, not the box's UTC: between 20:00 ET and midnight serv is already on
        # tomorrow, and the pack would ask the calendar about the wrong day.
        cal = event_calendar(_dt.now(_ET).date().isoformat())
    except Exception:  # noqa: BLE001
        logger.exception("book pack event calendar read failed")
        cal = None
    if cal:
        parts = []
        if cal["today"]:
            parts.append("TODAY " + ", ".join(cal["today"]))
        parts += [f"{e['release']} in {e['in_days']}d ({e['date']})" for e in cal["upcoming"][:4]]
        lines.append(
            f"- macro calendar: {'; '.join(parts) if parts else 'nothing scheduled in 10d'}"
            f" · not covered: {cal['not_covered']}"
        )
    else:
        lines.append("- macro calendar: _unavailable_")

    # Read from the snapshot store, never recomputed here: pricing the board costs a
    # 2.3 s broker fetch and pack generation must stay DB-only. The convention is
    # spelled out because the net is an assumption plus a fact, not a measurement —
    # see signals/gamma_exposure.py.
    try:
        from db.gamma_exposure import history as gex_history

        gex = (gex_history(limit=1) or [None])[0]
    except Exception:  # noqa: BLE001
        logger.exception("book pack gamma exposure read failed")
        gex = None
    if gex:
        flip = (
            f", flip {gex['flip']:,.0f} ({gex['spot_above_flip_pct']:+.2f}% away)"
            if gex.get("flip") is not None
            else ", no flip found in ±30%"
        )
        # Vanna and charm are null on snapshots stored before they existed — an old
        # row has no value for them, which is not the same as a board that was flat.
        extra = "".join(
            f", net {name} ${gex[key]:+,.1f}B per {per}"
            for name, key, per in (
                ("vanna", "net_vanna_b", "vol pt"), ("charm", "net_charm_b", "day")
            )
            if gex.get(key) is not None
        )
        lines.append(
            f"- SPX dealer exposure: net gamma ${gex['net_b']:+,.1f}B per 1% (calls "
            f"${gex['call_b']:,.1f}B vs puts ${gex['put_b']:,.1f}B){flip}{extra}; "
            f"spot {gex['spot']:,.2f}, OI as of {gex['oi_as_of']}, "
            f"priced {gex['as_of'][:16]}Z. "
            "Every net ASSUMES dealers long calls / short puts — the convention, not "
            "measured inventory; no edge tested. `query gex` for strikes/expiries/profile"
        )
    else:
        lines.append("- SPX dealer exposure: _no stored snapshot_")

    # Same miss as the 08-11 macro calendar: `earnings_release` shipped to the
    # perception resource and not onto this pack, which is what a book reads first.
    try:
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZoneInfo

        from signals.earnings_exhibit import DEFAULT_RELEASES_DB_PATH, latest_releases

        if not DEFAULT_RELEASES_DB_PATH.exists():
            today_exhibits = None
        else:
            today_et = _dt.now(_ZoneInfo("America/New_York")).date().isoformat()
            today_exhibits = [
                r
                for r in latest_releases(limit=40)
                if (r.get("accepted_et") or "")[:10] == today_et
            ]
    except Exception:  # noqa: BLE001
        logger.exception("book pack earnings exhibits read failed")
        today_exhibits = None
    if today_exhibits:
        shown = ", ".join(
            f"{r.get('symbol') or r.get('company') or '?'}"
            + (f" {r['session']}" if r.get("session") else "")
            for r in today_exhibits[:8]
        )
        extra = (
            f" +{len(today_exhibits) - 8} more" if len(today_exhibits) > 8 else ""
        )
        lines.append(
            f"- today's 8-K earnings exhibits: {shown}{extra}"
            " · `query earnings_release` for text; `query fundamentals` for XBRL"
        )
    elif today_exhibits == []:
        lines.append("- today's 8-K earnings exhibits: none stored")
    else:
        lines.append("- today's 8-K earnings exhibits: _unavailable_")

    try:
        from db.abnormality import events_today

        fires = events_today(db_path=db_path)
        fires = sorted(
            fires,
            key=lambda e: (
                e.get("composite") is None,
                -(e.get("composite") or 0),
            ),
        )
        paired_n = sum(1 for e in fires if e.get("kind") == "abnormality_paired")
        if fires:
            names = ", ".join(str(e["symbol"]) for e in fires[:8])
            extra = f" +{len(fires) - 8}" if len(fires) > 8 else ""
            lines.append(
                f"- abnormality fires today: {len(fires)} ({paired_n} paired) "
                f"{names}{extra}. Magnitude tell — in-sample fired names moved "
                "32–43% more than their time-matched norm over 2h; direction was "
                "a coin flip. Not an edge."
            )
        else:
            lines.append(
                "- abnormality fires today: none. Magnitude tell; direction is a "
                "coin flip. query('abnormality') for live state."
            )
    except Exception:  # noqa: BLE001
        logger.exception("book pack abnormality read failed")
        lines.append("- abnormality fires today: _unavailable_")

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


def _mandate_section(book: Book, db_path: Path) -> str:
    """The book's actual operating rules. Founder 07-23: NO imposed sizing —
    the analyst sizes its own book and is judged on it; the walls are the
    book's cash, the RiskEngine, and founder approval in propose mode."""
    from db.strategies import get_strategy

    s = get_strategy(book.strategy_id, db_path=db_path) if book.strategy_id else None
    if s is None:
        return "_no strategy record — cycles run dry_"
    lines = [
        (
            "- status: ARMED — advanced longs become phone proposals for founder approval"
            if s.get("enabled")
            else "- status: DRY (proving period) — nothing you advance is executed yet"
        ),
        "- sizing: YOURS. Every advanced pick must state notional_usd — you manage this"
        " book's capital and your sizing discipline is part of what the study judges"
        " (home runs when they present themselves, never sized to go belly up). The"
        " book's own cash is the wall; there is no per-trade clamp.",
    ]
    if s.get("max_deployed_usd"):
        lines.append(f"- founder-set max deployed: ${float(s['max_deployed_usd']):,.0f}")
    if s.get("max_concurrent_positions"):
        lines.append(f"- founder-set max concurrent positions: {int(s['max_concurrent_positions'])}")
    lines.append("- shorts: tracked in your lanes for calibration, NOT executable yet")
    return "\n".join(lines)


def _journal_section(book: Book, db_path: Path) -> str:
    """The analyst's own desk journal from its last cycle — its only narrative
    memory between nights (book-analyst-charter). Lanes above are the
    mechanical memory; this is the handoff it wrote to tomorrow's self."""
    from db.reports import recent_reports_by_kind

    reports = recent_reports_by_kind(f"book_{book.book_id}", limit=5, db_path=db_path)
    if not reports:
        return "_first cycle — no prior reports_"
    latest = reports[0]
    journal = str((latest.get("payload") or {}).get("journal") or "").strip()
    lines = [f"- prior reports on file: {len(reports)} (latest {latest.get('generated_at')})"]
    if journal:
        lines.append("\n### Your handoff from last cycle\n")
        lines.append(journal)
    else:
        lines.append("- no desk journal carried from the last cycle")
    return "\n".join(lines)


def archive_book_pack(book_id: str, pack: str, *, root: Path | None = None) -> Path:
    """Write a rendered pack to data/book_packs/<ET-date>/<book>.md (audit trail:
    every decision reviewable against exactly what the model saw). Shared by the
    CLI and the nightly driver."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    base = root if root is not None else Path(__file__).resolve().parent.parent / "data" / "book_packs"
    et_date = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    out_dir = base / et_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{book_id}.md"
    out_path.write_text(pack, encoding="utf-8")
    return out_path


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
    # Matt + LLM is advisory-only (spec §5): no mandate/lanes/journal for it.
    if book.headline_origin:
        parts.append(_section("Your mandate", lambda: _mandate_section(book, db_path)))
        parts.append(_section("Your ledger lanes", lambda: _lanes_section(book, db_path)))
        parts.append(
            _section("Your mechanism ledger", lambda: _mechanism_section(book, db_path))
        )
        parts.append(_section("Your desk journal", lambda: _journal_section(book, db_path)))
    parts.append(_section("Market frame", lambda: _market_frame_section(db_path)))
    parts.append(_section("Upcoming earnings (held names)", lambda: _earnings_section(book, db_path)))
    return "\n".join(parts)
