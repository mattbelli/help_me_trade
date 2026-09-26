"""Nightly brief board — every number the brief's LLM stages may cite, built by code.

Stage 1 of the nightly deep brief (board -> fact digests -> specialists -> judge).
Composes the EXISTING perception readers (``ai.perception.resources.query`` and
``build_symbol_context``); no LLM call anywhere, and no network path beyond what
those readers already take. Every section is fail-soft: a reader that raises
becomes ``{"error": ...}`` in its own section and the rest of the board builds.

Perception, not a verdict: lanes say WHY a name is on the board (which angle lit
it), never how likely it is to move. Nothing here is a win% or a direction call.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from calendar import monthrange
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ai.perception.resources import query
from db.base import DEFAULT_DB_PATH, ROOT

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
BRIEFS_DIR = ROOT / "data" / "briefs"
MAX_CANDIDATES = 30

# Lane -> how many NEW names it may admit, in admission-priority order. A name
# already on the board still collects the lane's angle; the quota only caps how
# many names a lane can ADD.
LANE_QUOTAS: dict[str, int] = {
    "attention": 8,
    "rare_flow": 6,
    "earnings": 4,
    "abnormality": 4,
    "technical": 4,
    "news": 3,
    "social": 2,
    "held": 6,
}
# The independent angle(s) each lane represents. Angle count = size of the union,
# so the same fact seen by two lanes counts once: an attention row IS a range fire
# plus a rare print, so a name in attention + abnormality + rare_flow is still two
# angles, not four.
LANE_ANGLES: dict[str, frozenset[str]] = {
    "attention": frozenset({"range", "flow"}),
    "rare_flow": frozenset({"flow"}),
    "earnings": frozenset({"earnings"}),
    "abnormality": frozenset({"range"}),
    "technical": frozenset({"technical"}),
    "news": frozenset({"news"}),
    "social": frozenset({"social"}),
    "held": frozenset({"held"}),
}
_EXCLUDED_CLASSES = frozenset({"etf", "index", "adjusted", "test"})
_PLAIN_TICKER = re.compile(r"^[A-Z]{1,5}$")
RARE_P = 0.01
_BIG_PRINT_NAMES = 6
INDEX_PRINT_ROOTS = ("SPX", "SPXW", "NDX", "NDXP", "SPY", "QQQ", "IWM")
OI_ROOTS = ("SPX", "SPXW", "NDX", "NDXP")
SECTOR_ETFS = ("XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC")
HORIZONS = (("1m", 1), ("3m", 3), ("1y", 12), ("3y", 36), ("5y", 60), ("10y", 120))
_SECTOR_BASIS = (
    "price return on split-adjusted daily closes (dividends excluded) from our Alpaca "
    "daily-bar store, which starts 2016-01-04; each figure names its from/to bars. "
    "rel_spy_pp = sector return minus SPY return over the same window, in points. "
    "No valuation data exists yet (CAPE / Ken French are a later data task)."
)
_INDEX_PRINT_CAVEAT = (
    "direction is LOW confidence: multi-leg packages flip the sign on 55.8% of index "
    "premium in the corpus, and a single leg cannot show which side of a package it was"
)

# Trading sessions a source may lag et_date before it is flagged STALE.
_CADENCE_SESSIONS: dict[str, int] = {
    "lane.attention": 0,
    "lane.rare_flow": 0,
    "lane.earnings": 1,
    "lane.abnormality": 0,
    "lane.technical": 0,
    "lane.news": 0,
    "lane.social": 0,
    "lane.held": 1,
    "index.gex.SPX": 1,
    "index.gex.NDX": 1,
    "index.oi_walls": 1,
    "index.prints": 0,
    "index.index_pressure": 0,
    "index.flow_report": 0,
    "index.overnight_flow": 0,
    "macro.regime": 3,
    "macro.axis": 3,
    "sectors.bars": 0,
}


# --- small helpers ------------------------------------------------------------------------


def next_session(d: date) -> date:
    from trading.market_calendar import is_trading_day

    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def _session_bounds(et_date: date) -> tuple[str, str]:
    """[ET midnight, next ET midnight) as UTC ISO-Z strings — the stores' ts format."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    start = datetime.combine(et_date, time(0), _ET).astimezone(UTC)
    end = datetime.combine(et_date + timedelta(days=1), time(0), _ET).astimezone(UTC)
    return start.strftime(fmt), end.strftime(fmt)


def _et_day(ts: str) -> str:
    """ET calendar day of a timestamp; a naive one (social's ET-hour buckets) is ET already."""
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return (dt.astimezone(_ET) if dt.tzinfo else dt).date().isoformat()


def _newest(values: list[Any]) -> str | None:
    vals = [str(v) for v in values if v]
    return max(vals) if vals else None


def _r(value: Any, nd: int = 2) -> Any:
    return round(value, nd) if isinstance(value, (int, float)) else value


async def _rows(resource: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
    """A reader's rows with its hoisted `shared` block merged back; raises on error."""
    res = await query(resource, filters)
    if res.get("error"):
        raise RuntimeError(f"{resource}: {res['error']}")
    shared = res.get("shared") or {}
    return [{**shared, **r} for r in res.get("rows") or []]


async def _guard(label: str, fn: Any, *args: Any) -> Any:
    try:
        out = fn(*args)
        return await out if inspect.isawaitable(out) else out
    except Exception as exc:  # noqa: BLE001 — nightly unattended: partial board > no board
        logger.exception("brief board: %s failed", label)
        return {"error": f"{type(exc).__name__}: {exc}"}


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value


@lru_cache(maxsize=1)
def _classifier() -> Any:
    # The registered fence (root-classification-fence): import the loader, never re-derive.
    from tools.root_classification_build import load

    return load(ROOT / "signals" / "options_flow" / "root_classification.json")


def _root_class(symbol: str) -> str:
    return _classifier().classify(symbol)


# --- candidate lanes ----------------------------------------------------------------------
# Each lane returns {"hits": [ranked {"symbol", "why", ...facts}], "newest": ts}. Hits are
# the lane's full LIT set (thresholded), ranked; the merge applies the quota.


async def _lane_attention(et_date: date) -> dict[str, Any]:
    rows = await _rows(
        "attention",
        {"session": et_date.isoformat(), "min_angles": 2, "min_quintile": 3, "limit": 200},
    )
    rows.sort(key=lambda r: -(r.get("abn_vol_ratio") or 0))
    hits = [
        {
            "symbol": r["symbol"],
            "why": (
                f"range fire + rare option print; window vol {_r(r.get('abn_vol_ratio'))}x "
                f"own norm, largest print {_money(r.get('flow_max_print_usd'))}"
            ),
            "abn_vol_ratio": _r(r.get("abn_vol_ratio")),
            "abn_range_pctile": _r(r.get("abn_range_pctile"), 1),
            "rvol_session": _r(r.get("abn_rvol_session")),
            "flow_min_p": r.get("flow_min_p"),
            "flow_call_prem": r.get("flow_call_prem"),
            "flow_put_prem": r.get("flow_put_prem"),
            "flow_short_dated_share": _r(r.get("flow_short_dated_share")),
        }
        for r in rows
    ]
    return {"hits": hits, "newest": _newest([r.get("updated_at") for r in rows])}


def _print_facts(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": r.get("ts"),
        "event_type": r.get("event_type"),
        "right": r.get("right"),
        "strike": r.get("strike"),
        "expiry": r.get("expiry"),
        "dte": r.get("dte"),
        "otm_pct": _r(r.get("otm_pct")),
        "premium_usd": r.get("premium_usd"),
        "contracts": r.get("contracts"),
        "direction": r.get("direction"),
        "mid_side": r.get("mid_side"),
        "rarity_p": r.get("rarity_p"),
    }


async def _lane_rare_flow(et_date: date) -> dict[str, Any]:
    since, until = _session_bounds(et_date)
    base = {"since": since, "until": until, "universe": "equity", "limit": 50}
    rare = await _rows("options_flow", {**base, "order": "rarity"})
    big = await _rows("options_flow", {**base, "order": "premium", "event_type": "sweep,block"})

    best: dict[str, dict[str, Any]] = {}
    for r in rare:
        p = r.get("rarity_p")
        if p is None or p > RARE_P:
            continue
        sym = r["underlying"]
        if sym not in best or (r.get("premium_usd") or 0) > (best[sym].get("premium_usd") or 0):
            best[sym] = r
    hits = [
        {
            "symbol": r["underlying"],
            "why": f"rare print: {r.get('rarity_sentence') or r.get('reason')}",
            **_print_facts(r),
        }
        for r in sorted(best.values(), key=lambda r: -(r.get("premium_usd") or 0))
    ]
    seen = set(best)
    for r in big:  # already premium-ordered
        sym = r["underlying"]
        if sym in seen:
            continue
        seen.add(sym)
        hits.append({"symbol": sym, "why": f"largest block/sweep: {r.get('reason')}", **_print_facts(r)})
        if len(seen) - len(best) >= _BIG_PRINT_NAMES:
            break
    return {"hits": hits, "newest": _newest([r.get("ts") for r in rare + big])}


def earnings_window(et_date: date, nxt: date) -> dict[str, Any]:
    """Reports after et_date's close or before the next session's open.

    Two stores: the EDGAR 8-K Item 2.02 release store (market-wide, only what has
    already been furnished) and the confirmed earnings calendar (dates we have
    fetched; estimated dates are excluded — they are not announcements).
    """
    from db.base import connect
    from signals.earnings_exhibit import latest_releases

    out: dict[str, dict[str, Any]] = {}
    # The `earnings_release` reader caps at 25 rows; peak season furnishes hundreds a
    # night, so read the same store through the function the reader wraps.
    releases = latest_releases(limit=500)
    for r in releases:
        filed, session = r.get("filed_date"), r.get("session")
        if (filed == et_date.isoformat() and session == "amc") or (
            filed == nxt.isoformat() and session == "bmo"
        ):
            sym = str(r.get("symbol") or "").upper()
            if sym:
                out[sym] = {
                    "symbol": sym,
                    "when": f"{filed} {session}",
                    "source": "8-K 2.02 furnished",
                    "accepted_et": r.get("accepted_et"),
                    "company": r.get("company"),
                }
    with connect(DEFAULT_DB_PATH) as conn:
        rows = conn.execute(
            "SELECT symbol, date, hour FROM earnings_calendar WHERE estimated = 0 "
            "AND ((date = ? AND hour = 'amc') OR (date = ? AND hour = 'bmo'))",
            (et_date.isoformat(), nxt.isoformat()),
        ).fetchall()
    for row in rows:
        sym = str(row["symbol"]).upper()
        out.setdefault(
            sym,
            {"symbol": sym, "when": f"{row['date']} {row['hour']}", "source": "calendar (confirmed)"},
        )
    return {
        "rows": sorted(out.values(), key=lambda r: r["symbol"]),
        # The release store's own newest filing — its freshness, whatever tonight holds.
        "newest": _newest([r.get("accepted_et") for r in releases]),
    }


async def _lane_earnings(et_date: date, nxt: date) -> dict[str, Any]:
    liquid = _liquidity_floor(et_date.isoformat())
    full = earnings_window(et_date, nxt)
    window = [r for r in full["rows"] if liquid(r["symbol"])]
    since, until = _session_bounds(et_date)
    res = await query("options_flow", {"since": since, "until": until, "limit": 1})
    by_under = (res.get("summary") or {}).get("by_underlying") or {}
    # The reaction we can measure tonight: the name's option premium today (a report
    # after the close has no regular-session price reaction yet).
    for r in window:
        r["options_premium_today_usd"] = (by_under.get(r["symbol"]) or {}).get("total_premium_usd")
    window.sort(key=lambda r: -(r["options_premium_today_usd"] or 0))
    hits = [
        {
            **r,
            "why": f"earnings {r['when']} ({r['source']}); option premium today "
            f"{_money(r['options_premium_today_usd'])}",
        }
        for r in window
    ]
    return {"hits": hits, "newest": full["newest"]}


def _lane_abnormality(et_date: date) -> dict[str, Any]:
    # The `abnormality` reader's `fired` filter reads TODAY's fires only; the function
    # it wraps takes the session, so a rebuild for a past date reads that date's fires.
    from db.abnormality import events_today

    evs = [
        e
        for e in events_today(et_date.isoformat(), first_only=True)
        if (e.get("quintile") or 0) >= 3
    ]
    evs.sort(key=lambda e: (-(e.get("range_pctile") or 0), -(e.get("range_ratio") or 0)))
    hits = [
        {
            "symbol": e["symbol"],
            "why": f"first abnormality fire {e.get('ts')}: 60-min range p{_r(e.get('range_pctile'), 1)} "
            f"of its peer cell, window vol {_r(e.get('vol_ratio'))}x norm",
            "range_ratio": _r(e.get("range_ratio")),
            "range_pctile": _r(e.get("range_pctile"), 1),
            "vol_ratio": _r(e.get("vol_ratio")),
            "rvol_session": _r(e.get("rvol_session")),
            "base_state": e.get("base_state"),
        }
        for e in evs
    ]
    return {"hits": hits, "newest": _newest([e.get("ts") for e in evs])}


def technical_events(closes: list[float]) -> list[str]:
    """Close-based 52-week break / 200-day cross on the LAST close (needs 253 closes)."""
    if len(closes) < 253:
        return []
    c, prev = closes[-1], closes[-2]
    prior_year = closes[-253:-1]
    sma_t = sum(closes[-200:]) / 200
    sma_y = sum(closes[-201:-1]) / 200
    out = []
    if c > max(prior_year):
        out.append("52w_high_break")
    if c < min(prior_year):
        out.append("52w_low_break")
    if prev <= sma_y and c > sma_t:
        out.append("200d_cross_up")
    if prev >= sma_y and c < sma_t:
        out.append("200d_cross_down")
    return out


def _lane_technical(et_date: date) -> dict[str, Any]:
    from db.abnormality import get_state
    from db.momentum_bars import load_all_closes

    day = et_date.isoformat()
    series: dict[str, list[float]] = defaultdict(list)
    last: dict[str, str] = {}
    for sym, d, c in load_all_closes():  # S&P 500 closes, chronological
        if d <= day:
            series[sym].append(c)
            last[sym] = d
    hits = []
    for sym, closes in series.items():
        if last[sym] != day:
            continue
        events = technical_events(closes)
        if not events:
            continue
        # RVOL from the abnormality state (session volume vs own norm); the momentum
        # store holds closes only. Unknown RVOL fails the >= 2 gate, never passes it.
        state = get_state(sym) or {}
        rvol = state.get("rvol_session") if state.get("session") == day else None
        if rvol is None or rvol < 2:
            continue
        hits.append({
            "symbol": sym,
            "why": f"{', '.join(events)} (close-based) on RVOL {_r(rvol)}",
            "events": events,
            "rvol_session": _r(rvol),
            "close": closes[-1],
        })
    hits.sort(key=lambda h: -h["rvol_session"])
    return {"hits": hits, "newest": _newest(list(last.values()))}


def own_norms(
    counts: dict[str, dict[str, int]], day: str, prior_days: list[str]
) -> dict[str, dict[str, Any]]:
    """Each name's count on `day` against its own daily mean over prior_days.

    A zero prior mean is floored at 1/len(prior_days) (one item in the whole window)
    so a first-ever mention ranks by its size rather than dividing by zero.
    """
    out = {}
    for sym, by_day in counts.items():
        today = by_day.get(day, 0)
        mean = sum(by_day.get(d, 0) for d in prior_days) / len(prior_days)
        out[sym] = {"today": today, "prior_daily_mean": round(mean, 2),
                    "ratio": round(today / max(mean, 1 / len(prior_days)), 1)}
    return out


def own_norm_spikes(
    counts: dict[str, dict[str, int]],
    day: str,
    prior_days: list[str],
    *,
    min_today: int,
    min_ratio: float,
) -> list[dict[str, Any]]:
    """Names whose count on `day` is >= min_today and >= min_ratio x their own norm."""
    out = [
        {"symbol": sym, **n}
        for sym, n in own_norms(counts, day, prior_days).items()
        if n["today"] >= min_today and n["ratio"] >= min_ratio
    ]
    out.sort(key=lambda r: (-r["ratio"], -r["today"]))
    return out


def _liquidity_floor(day: str) -> Any:
    """Predicate: the attention lane's $-volume display floor (quintile >= 3, the top
    40% of the options-eligible pool) for lanes whose own source has no liquidity notion.

    Read from the abnormality state, which holds ONE session (the latest). When it holds
    `day`, a name without a row is outside the pool and fails; when it does not (a
    rebuild of an older date), the floor is unknowable and every name passes.
    """
    from db.abnormality import get_state, list_state

    if not list_state(session=day, limit=1):
        return lambda _sym: True

    def ok(symbol: str) -> bool:
        state = get_state(symbol) or {}
        return state.get("session") == day and (state.get("quintile") or 0) >= 3

    return ok


def _spike_lane(
    counts: dict[str, dict[str, int]], newest: str | None, et_date: date, *,
    min_today: int, noun: str,
) -> dict[str, Any]:
    day = et_date.isoformat()
    prior = _prior_days(et_date)
    liquid = _liquidity_floor(day)
    hits = [
        {**s, "why": f"{s['today']} {noun} vs {s['prior_daily_mean']}/day own norm ({s['ratio']}x)"}
        for s in own_norm_spikes(counts, day, prior, min_today=min_today, min_ratio=3.0)
        if _PLAIN_TICKER.match(s["symbol"]) and liquid(s["symbol"])
    ]
    # Every name's day count vs its norm, for the cards (et_date, not the live day).
    norms = {k: v for k, v in own_norms(counts, day, prior).items() if v["today"]}
    return {"hits": hits, "newest": newest, "norms": norms}


_NORM_DAYS = 20


def _prior_days(et_date: date) -> list[str]:
    return [(et_date - timedelta(days=i)).isoformat() for i in range(1, _NORM_DAYS + 1)]


def _lane_news(et_date: date) -> dict[str, Any]:
    # No reader aggregates news per name per day, so count the store directly.
    # Roundup stories tagging >3 names are skipped: they say nothing about any one.
    from db.base import connect

    start, _ = _session_bounds(et_date - timedelta(days=_NORM_DAYS))
    _, end = _session_bounds(et_date)
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    newest = None
    with connect(DEFAULT_DB_PATH) as conn:
        for row in conn.execute(
            "SELECT ns.symbol, n.published_at FROM news_items n "
            "JOIN news_symbols ns ON ns.news_id = n.id "
            "WHERE n.published_at >= ? AND n.published_at < ? "
            "AND json_array_length(n.symbols) <= 3",
            (start, end),
        ):
            counts[row["symbol"]][_et_day(row["published_at"])] += 1
            newest = max(newest or "", row["published_at"])
    return _spike_lane(counts, newest, et_date, min_today=3, noun="stories")


def _lane_social(et_date: date) -> dict[str, Any]:
    # social_mentions buckets are ET hours ('YYYY-MM-DDTHH'); no reader gives the day total
    # against the name's own daily norm (get_buzz is hour-of-day), so aggregate directly.
    from db.base import connect

    first = (et_date - timedelta(days=_NORM_DAYS)).isoformat()
    last = et_date.isoformat() + "T99"
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    newest = None
    with connect(DEFAULT_DB_PATH) as conn:
        for row in conn.execute(
            "SELECT symbol, substr(bucket, 1, 10) AS d, SUM(mentions) AS n, MAX(bucket) AS b "
            "FROM social_mentions WHERE bucket >= ? AND bucket <= ? GROUP BY symbol, d",
            (first, last),
        ):
            counts[row["symbol"]][row["d"]] = int(row["n"])
            newest = max(newest or "", row["b"])
    return _spike_lane(counts, newest, et_date, min_today=20, noun="mentions")


def held_symbols() -> tuple[dict[str, set[str]], str | None]:
    """{symbol: {where held}} from STORED state only — never a broker call.

    Paper books: each book account's latest polled snapshot. Schwab: open longs
    reconciled from the imported transaction ledger (plain tickers only — the ledger
    carries expired option legs it never saw close). Measured 2026-09-26: the ledger
    set equalled the live Schwab positions exactly.
    """
    from db.realized_pnl import reconcile_positions
    from db.sqlite import get_latest_snapshot
    from db.trades import list_trade_accounts, list_trade_events
    from research.books import BOOKS

    held: dict[str, set[str]] = defaultdict(set)
    captured = []
    for book in BOOKS.values():
        snap = get_latest_snapshot("alpaca", account_id=book.account_id) or {}
        captured.append(snap.get("captured_at"))
        for p in snap.get("positions") or []:
            held[str(p.get("symbol") or "").upper()].add(f"paper:{book.book_id}")
    for acct in list_trade_accounts():
        if acct.get("broker") != "schwab":
            continue
        recon = reconcile_positions(list_trade_events(account_id=acct["id"], limit=1_000_000))
        for sym, r in recon.items():
            if r.status == "open_long" and _PLAIN_TICKER.match(sym):
                held[sym].add(f"schwab:{acct.get('label')}")
    held.pop("", None)
    return dict(held), _newest(captured)


async def _lane_held(et_date: date) -> dict[str, Any]:
    from db.abnormality import get_state

    held, captured = held_symbols()
    day = et_date.isoformat()
    hits = []
    for sym in sorted(held):
        att = await _rows("attention", {"symbol": sym, "session": day})
        state = get_state(sym) or {}
        rvol = state.get("rvol_session") if state.get("session") == day else None
        if not att and (rvol is None or rvol < 2):
            continue
        why = [f"held ({', '.join(sorted(held[sym]))})"]
        if att:
            why.append(f"attention angles {att[0].get('angles')}")
        if rvol is not None:
            why.append(f"RVOL {_r(rvol)}")
        hits.append({"symbol": sym, "why": "; ".join(why), "held_in": sorted(held[sym]),
                     "rvol_session": _r(rvol)})
    hits.sort(key=lambda h: -(h["rvol_session"] or 0))
    return {"hits": hits, "newest": captured, "held_count": len(held)}


def merge_lanes(
    lanes: dict[str, Any],
    classify: Any = _root_class,
    *,
    quotas: dict[str, int] | None = None,
    cap: int = MAX_CANDIDATES,
) -> list[dict[str, Any]]:
    """Dedupe lane hits into candidates: angles counted, ETFs/indexes dropped, capped.

    A lane admits at most its quota of names not already admitted by an earlier lane,
    but EVERY lit name — admitted or not — credits its lane's angle to a candidate
    that is on the board. Ranked: more angles first, then admission order.
    """
    quotas = quotas or LANE_QUOTAS
    order: list[str] = []
    cands: dict[str, dict[str, Any]] = {}
    for lane, quota in quotas.items():
        result = lanes.get(lane)
        if not isinstance(result, dict) or _is_error(result):
            continue
        admitted = 0
        for hit in result.get("hits") or []:
            sym = str(hit.get("symbol") or "").strip().upper()
            if not sym or classify(sym) in _EXCLUDED_CLASSES:
                continue
            if sym not in cands:
                if admitted >= quota:
                    continue
                admitted += 1
                cands[sym] = {"symbol": sym, "lanes": [], "why": {}, "lane_facts": {}}
                order.append(sym)
            cand = cands[sym]
            if lane not in cand["lanes"]:
                cand["lanes"].append(lane)
                cand["why"][lane] = hit.get("why")
                cand["lane_facts"][lane] = {k: v for k, v in hit.items() if k not in ("symbol", "why")}
    for cand in cands.values():
        cand["angles"] = sorted(set().union(*(LANE_ANGLES[lane] for lane in cand["lanes"])))
    ranked = sorted(order, key=lambda s: (-len(cands[s]["angles"]), order.index(s)))
    return [cands[s] for s in ranked[:cap]]


# --- candidate cards ----------------------------------------------------------------------


def fundamentals_snapshot(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """Latest discrete quarter: revenue, YoY growth, gross/operating margin, diluted EPS."""
    revenue = (metrics.get("revenue") or {}).get("quarterly") or []
    if not revenue:
        return None
    latest = max(revenue, key=lambda q: q["period_end"])
    end = date.fromisoformat(latest["period_end"])

    def same_period(key: str) -> float | None:
        for q in (metrics.get(key) or {}).get("quarterly") or []:
            if q["period_end"] == latest["period_end"]:
                return q["val"]
        return None

    out: dict[str, Any] = {
        "quarter_end": latest["period_end"],
        "fp": latest.get("fp"),
        "filed": latest.get("filed"),
        "revenue": latest["val"],
        "eps_diluted": same_period("eps_diluted"),
    }
    for q in revenue:
        if 350 <= (end - date.fromisoformat(q["period_end"])).days <= 380 and q["val"]:
            out["revenue_yoy_pct"] = round((latest["val"] / q["val"] - 1) * 100, 1)
            break
    for key, name in (("gross_profit", "gross_margin_pct"), ("operating_income", "operating_margin_pct")):
        val = same_period(key)
        if val is not None and latest["val"]:
            out[name] = round(val / latest["val"] * 100, 1)
    return out


def _day_stats(bars: list[dict[str, Any]], et_date: date) -> dict[str, Any] | None:
    """Close, % change, SMA20 and 20-day RVOL off the daily bars (last bar = et_date)."""
    if len(bars) < 2:
        return None
    last, prev = bars[-1], bars[-2]
    out: dict[str, Any] = {
        "bar_date": str(last["t"])[:10],
        "close": last["c"],
        "change_pct": round((last["c"] / prev["c"] - 1) * 100, 2) if prev["c"] else None,
        "volume": last["v"],
    }
    if out["bar_date"] != et_date.isoformat():
        out["note"] = f"latest daily bar is {out['bar_date']}, not {et_date.isoformat()}"
    if len(bars) >= 21:
        out["sma20"] = round(sum(b["c"] for b in bars[-20:]) / 20, 2)
        avg_vol = sum(b["v"] for b in bars[-21:-1]) / 20
        out["rvol_20d"] = round(last["v"] / avg_vol, 2) if avg_vol else None
    return out


_LEVEL_KEYS = ("poc", "vah", "val", "pdh", "pdl", "52wh", "52wl", "session_date")


async def _card(
    cand: dict[str, Any], et_date: date, insider: list[dict[str, Any]],
    norms: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    from ai.perception import build_symbol_context

    sym = cand["symbol"]
    since, until = _session_bounds(et_date)
    ctx, bars, prints, tape, fund = await asyncio.gather(
        _guard(f"{sym} context", build_symbol_context, sym),
        _guard(f"{sym} bars", _rows, "bars", {"symbol": sym, "timeframe": "1Day", "limit": 25}),
        _guard(f"{sym} prints", _rows, "options_flow",
               {"symbol": sym, "order": "premium", "event_type": "sweep,block",
                "since": since, "until": until, "limit": 5}),
        _guard(f"{sym} tape", _rows, "equity_tape", {"symbol": sym, "since": since, "limit": 50}),
        _guard(f"{sym} fundamentals", _rows, "fundamentals", {"symbol": sym, "periods": 5}),
    )
    card: dict[str, Any] = {**cand, "errors": {}}

    def take(name: str, value: Any) -> Any:
        if _is_error(value):
            card["errors"][name] = value["error"]
            return None
        return value

    ctx = take("context", ctx) or {}
    card["quote"] = ctx.get("quote")
    bars = take("bars", bars)
    if bars and bars[0].get("bars"):
        card["day"] = _day_stats(bars[0]["bars"], et_date)
    card["rvol_intraday"] = ctx.get("rvol")
    card["technicals"] = ctx.get("technicals")
    nodes = ctx.get("session_nodes") or {}
    card["levels"] = {k: _r(nodes[k]) for k in _LEVEL_KEYS if k in nodes} or None
    card["earnings"] = ctx.get("earnings")
    f = ctx.get("fundamentals") or {}
    card["fundamentals"] = {k: f[k] for k in ("pe", "eps", "market_cap", "div_yield") if k in f}
    fund = take("fundamentals", fund)
    if fund and fund[0].get("metrics"):
        card["fundamentals"]["quarter"] = fundamentals_snapshot(fund[0]["metrics"])
        card["fundamentals"]["sector"] = fund[0].get("sic_desc") or fund[0].get("sector")
    prints = take("prints", prints)
    card["option_prints"] = [_print_facts(p) for p in prints or [] if p.get("premium_usd")]
    tape = take("tape", tape) or []
    tape = [t for t in tape if str(t.get("ts") or "") < until.replace("Z", "+00:00")]
    card["tape_blocks"] = [
        {k: t.get(k) for k in ("ts", "event_type", "price", "size", "notional_usd", "direction")}
        for t in sorted(tape, key=lambda t: -(t.get("notional_usd") or 0))[:5]
    ]
    news = ctx.get("news")
    card["news"] = (
        [{"headline": n.get("headline"), "datetime": n.get("datetime")} for n in news[:5]]
        if isinstance(news, list) else news
    )
    for kind in ("news", "social"):
        card[f"{kind}_count_vs_norm"] = (norms.get(kind) or {}).get(sym)
    card["insider_buys_30d"] = [
        {k: i.get(k) for k in ("person", "role", "value_usd", "filed_at")}
        for i in insider
        if i.get("symbol") == sym
    ][:5]
    if not card["errors"]:
        del card["errors"]
    return card


async def _candidates_section(et_date: date, nxt: date) -> dict[str, Any]:
    lanes = {
        "attention": await _guard("lane attention", _lane_attention, et_date),
        "rare_flow": await _guard("lane rare_flow", _lane_rare_flow, et_date),
        "earnings": await _guard("lane earnings", _lane_earnings, et_date, nxt),
        "abnormality": await _guard("lane abnormality", _lane_abnormality, et_date),
        "technical": await _guard("lane technical", _lane_technical, et_date),
        "news": await _guard("lane news", _lane_news, et_date),
        "social": await _guard("lane social", _lane_social, et_date),
        "held": await _guard("lane held", _lane_held, et_date),
    }
    candidates = merge_lanes(lanes)

    from db.filings import list_recent

    cutoff = (et_date - timedelta(days=30)).isoformat()
    insider = await _guard("insider filings", lambda: list_recent(kinds=["insider_buy"], limit=300))
    insider_rows = [] if _is_error(insider) else [
        r for r in insider if str(r.get("filed_at") or "") >= cutoff
    ]

    norms = {k: lanes[k].get("norms") or {} for k in ("news", "social") if not _is_error(lanes[k])}
    sem = asyncio.Semaphore(4)

    async def one(cand: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            return await _card(cand, et_date, insider_rows, norms)

    cards = await asyncio.gather(*(one(c) for c in candidates))
    summary = {}
    for lane, result in lanes.items():
        if _is_error(result):
            summary[lane] = result
            continue
        summary[lane] = {
            "quota": LANE_QUOTAS[lane],
            "lit": len(result.get("hits") or []),
            "admitted": [c["symbol"] for c in candidates if c["lanes"][0] == lane],
            "newest": result.get("newest"),
        }
        if "held_count" in result:
            summary[lane]["held_count"] = result["held_count"]
    return {"lanes": summary, "cards": list(cards)}


# --- index frame --------------------------------------------------------------------------

_GEX_KEYS = (
    "spot",
    "oi_as_of",
    "net_gamma_usd_per_1pct_b",
    "call_gamma_usd_per_1pct_b",
    "put_gamma_usd_per_1pct_b",
    "gamma_flip_spot",
    "spot_above_flip_pct",
    "gamma_regime",
    "net_vanna_usd_per_volpt_b",
    "net_charm_usd_per_day_b",
    "charm_from_expiring_b",
)


async def _gex(index: str) -> dict[str, Any]:
    rows = await _rows("gex", {"index": index})
    row = rows[0] if rows else {}
    if row.get("error"):
        raise RuntimeError(f"gex: {row['error']}")
    if row.get("unavailable"):
        return {"unavailable": row.get("note")}
    # A build whose gex reader predates the `index` filter silently answers SPX for
    # every ask; never let that board go out labelled as NDX.
    if row.get("index") != index:
        raise RuntimeError(f"gex reader answered {row.get('index')!r} for index={index!r}")
    out = {k: row.get(k) for k in _GEX_KEYS}
    out["priced_at"] = row.get("quote_as_of")
    out["convention"] = (
        "net assumes dealers are long every call and short every put — the industry "
        "convention, not measured inventory; call/put gamma are the unconditional facts"
    )
    return out


async def _oi_walls(et_date: date) -> dict[str, Any]:
    from db.oi_walls import oi_board

    out: dict[str, Any] = {
        "caveat": "standing open interest only (T+1: the prior close's positions); no "
        "pin/magnet/support edge has been measured for these strikes",
    }
    for source, roots in (("opra", ("SPX", "SPXW")), ("opra_ndx", ("NDX", "NDXP"))):
        as_of, board = await asyncio.to_thread(oi_board, source=source, roots=roots)
        for root in roots:
            expiries = sorted({k[1] for k in board if k[0] == root and k[1] > et_date.isoformat()})
            per_root: dict[str, Any] = {"as_of": as_of, "expiries": {}}
            for exp in expiries[:3]:
                rows = await _rows("oi_walls", {"root": root, "expiry": exp, "limit": 5})
                per_root["expiries"][exp] = [
                    {k: r.get(k) for k in ("cp", "strike", "oi", "oi_change", "days_standing")}
                    for r in rows
                    if not r.get("unavailable")
                ]
            out[root] = per_root
    return out


async def _index_prints(et_date: date) -> dict[str, Any]:
    since, until = _session_bounds(et_date)
    rows: list[dict[str, Any]] = []
    for root in INDEX_PRINT_ROOTS:
        rows += await _rows(
            "options_flow",
            {"symbol": root, "order": "premium", "event_type": "sweep,block",
             "since": since, "until": until, "limit": 10},
        )
    rows.sort(key=lambda r: -(r.get("premium_usd") or 0))
    return {
        "caveat": _INDEX_PRINT_CAVEAT,
        "prints": [{"root": r.get("underlying"), **_print_facts(r)} for r in rows[:10]],
    }


async def _index_pressure(et_date: date, window: str) -> dict[str, Any]:
    rows = await _rows("index_pressure", {"date": et_date.isoformat(), "window": window})
    if not rows or rows[0].get("unavailable"):
        return {"unavailable": True, "date": et_date.isoformat(), "window": window}
    keys = ("underlying", "bull_usd", "bear_usd", "unclassified_usd", "net_usd", "classified_frac")
    return {
        "date": rows[0].get("date"),
        "window_et": rows[0].get("window_et"),
        "baseline_days": rows[0].get("baseline_days"),
        "caveat": "raw window premium, aggressor side where classifiable; the unclassified "
        "remainder is real flow of unknown side; not a validated signal",
        "rows": [{k: _r(r.get(k), 3) for k in keys} for r in rows],
    }


async def _flow_report(et_date: date) -> dict[str, Any]:
    rows = await _rows("flow_report", {"date": et_date.isoformat()})
    r = rows[0] if rows else {}
    if r.get("unavailable"):
        return {"unavailable": True, "date": et_date.isoformat()}
    keys = ("et_date", "title", "generated_at", "opex", "characterizations", "caveats")
    return {k: r.get(k) for k in keys}


async def _overnight_flow() -> dict[str, Any]:
    rows = await _rows("overnight_flow", {})
    r = rows[0] if rows else {}
    if r.get("unavailable"):
        return {"unavailable": r.get("note")}
    s = r.get("slice") or {}
    keys = ("prints", "notional", "put_share_notional", "zero_dte_share_notional",
            "macro_window_share")
    return {
        "session": r.get("session"),
        "generated_at": r.get("generated_at"),
        "window_et": r.get("window_et"),
        **{k: s.get(k) for k in keys},
        "baseline": r.get("baseline"),
        "top_prints": (s.get("top_prints") or [])[:5],
        "caveats": r.get("caveats"),
    }


_ROOT_INDEX = {"SPX": "SPX", "SPXW": "SPX", "NDX": "NDX", "NDXP": "NDX"}


def fill_index_otm(prints: list[dict[str, Any]], spots: dict[str, float]) -> None:
    """Index rows carry no underlying price (no index quote on the print lane), so their
    otm_pct is null. Fill `otm_pct_vs_close_spot` against the gex board's parity spot,
    same sign as the store's otm_pct (positive = out of the money)."""
    for p in prints:
        spot = spots.get(_ROOT_INDEX.get(str(p.get("root")), ""))
        if p.get("otm_pct") is not None or not spot or p.get("strike") is None:
            continue
        sign = 1 if p.get("right") == "call" else -1
        p["otm_pct_vs_close_spot"] = round(sign * (p["strike"] / spot - 1) * 100, 2)


async def _index_section(et_date: date) -> dict[str, Any]:
    gex = {idx: await _guard(f"gex {idx}", _gex, idx) for idx in ("SPX", "NDX")}
    prints = await _guard("index prints", _index_prints, et_date)
    if not _is_error(prints):
        fill_index_otm(prints["prints"], {k: g.get("spot") for k, g in gex.items() if _has_data(g)})
    return {
        "gex": gex,
        "oi_walls": await _guard("oi_walls", _oi_walls, et_date),
        "prints": prints,
        "index_pressure": {
            w: await _guard(f"index_pressure {w}", _index_pressure, et_date, w)
            for w in ("open", "close")
        },
        "flow_report": await _guard("flow_report", _flow_report, et_date),
        "overnight_flow": await _guard("overnight_flow", _overnight_flow),
        # data/futures_bars.db holds /ES /NQ, but nothing READS it yet (only the
        # archiver writes it); a reader is a separate data task.
        "futures": {"unavailable": "no futures-bars reader exists yet"},
    }


# --- macro frame --------------------------------------------------------------------------


async def _market() -> dict[str, Any]:
    rows = await _rows("market", {})
    row = rows[0] if rows else {}
    if row.get("unavailable"):
        return {"unavailable": True}
    macro = row.get("macro") or {}
    return {
        "regime": {k: row.get(k) for k in ("as_of", "term_spread", "vix", "regime_label",
                                            "fresh", "vix_percentile", "vix_percentile_window")},
        "axes": macro.get("axes"),
        "series": {
            k: {f: v.get(f) for f in ("date", "value", "pctile", "bucket", "history_since")}
            for k, v in (macro.get("series") or {}).items()
        },
        "events": macro.get("events"),
        "note": macro.get("note"),
    }


async def _macro_section() -> dict[str, Any]:
    from research.books import _market_frame_section

    return {
        "book_frame_md": await _guard(
            "book market frame", asyncio.to_thread, _market_frame_section, DEFAULT_DB_PATH
        ),
        "market": await _guard("market", _market),
    }


# --- long-horizon sectors -----------------------------------------------------------------


def _months_back(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    return date(y, m + 1, min(d.day, monthrange(y, m + 1)[1]))


def horizon_returns(bars: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    """Price return per horizon from the last bar on/before `as_of`, with its window.

    The start bar is the last one on/before (end - horizon). A horizon reaching past
    the first bar we hold is None, never a shorter window passed off as the full one.
    """
    bars = [b for b in bars if b["d"] <= as_of.isoformat()]
    if not bars:
        return {}
    end = bars[-1]
    out: dict[str, Any] = {}
    for label, months in HORIZONS:
        target = _months_back(date.fromisoformat(end["d"]), months).isoformat()
        starts = [b for b in bars if b["d"] <= target]
        if not starts:
            out[label] = None
            continue
        start = starts[-1]
        out[label] = {
            "pct": round((end["c"] / start["c"] - 1) * 100, 2),
            "from": start["d"],
            "to": end["d"],
        }
    return out


def sector_stats(
    bars: list[dict[str, Any]], spy_bars: list[dict[str, Any]] | None, as_of: date
) -> dict[str, Any]:
    bars = [b for b in bars if b["d"] <= as_of.isoformat()]
    if not bars:
        return {"unavailable": "no daily bars"}
    closes = [b["c"] for b in bars]
    peak = max(bars, key=lambda b: b["c"])
    out: dict[str, Any] = {
        "last_bar": bars[-1]["d"],
        "close": closes[-1],
        "data_since": bars[0]["d"],
        "returns": horizon_returns(bars, as_of),
        "drawdown_from_ath_pct": round((closes[-1] / peak["c"] - 1) * 100, 2),
        "ath_close": peak["c"],
        "ath_date": peak["d"],
    }
    if len(closes) >= 200:
        sma = sum(closes[-200:]) / 200
        out["sma200"] = round(sma, 2)
        out["vs_sma200_pct"] = round((closes[-1] / sma - 1) * 100, 2)
    if spy_bars is not None:
        spy = horizon_returns(spy_bars, as_of)
        out["rel_spy_pp"] = {
            label: round(ret["pct"] - spy[label]["pct"], 2)
            if ret and spy.get(label) else None
            for label, ret in out["returns"].items()
        }
    return out


def _daily_history(symbol: str, et_date: date) -> list[dict[str, Any]]:
    """Every cached daily bar through et_date, topping the cache up first.

    Tops up only the last ~2 weeks (a full-range get_daily_bars refetches whenever
    the first cached bar is after the requested start, i.e. every night); a symbol
    the cache barely knows gets one full backfill from 2016.
    """
    from research import bars as bars_mod

    db = bars_mod.DEFAULT_BARS_DB_PATH
    try:
        bars_mod.get_daily_bars(symbol, et_date - timedelta(days=15), et_date, db_path=db)
    except Exception:  # noqa: BLE001 — a network blip leaves the cache as it was
        logger.warning("brief board: bar top-up failed for %s; using cache", symbol)
    hist = bars_mod._load_cached(symbol, date(2000, 1, 1), et_date, "alpaca", db)
    if len(hist) < 300:
        hist = bars_mod.get_daily_bars(symbol, date(2016, 1, 1), et_date, db_path=db)
    return hist


def _sectors_section(et_date: date) -> dict[str, Any]:
    spy = _daily_history("SPY", et_date)
    sectors: dict[str, Any] = {}
    for sym in SECTOR_ETFS:
        try:
            sectors[sym] = sector_stats(_daily_history(sym, et_date), spy, et_date)
        except Exception as exc:  # noqa: BLE001
            logger.exception("brief board: sector %s failed", sym)
            sectors[sym] = {"error": f"{type(exc).__name__}: {exc}"}
    return {"basis": _SECTOR_BASIS, "SPY": sector_stats(spy, None, et_date), "sectors": sectors}


# --- events -------------------------------------------------------------------------------


def _events_section(et_date: date, nxt: date, macro: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"next_session": nxt.isoformat()}
    market = macro.get("market") if isinstance(macro, dict) else None
    events = market.get("events") if isinstance(market, dict) else None
    if isinstance(events, dict):
        out["macro_next_session"] = [
            e for e in events.get("upcoming") or [] if e.get("date") == nxt.isoformat()
        ]
        out["macro_today"] = events.get("today") or []
        out["not_covered"] = events.get("not_covered")
    else:
        out["macro_next_session"] = {"error": "market events unavailable (see macro.market)"}
    try:
        out["earnings"] = earnings_window(et_date, nxt)["rows"]
    except Exception as exc:  # noqa: BLE001
        logger.exception("brief board: earnings window failed")
        out["earnings"] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


# --- freshness ----------------------------------------------------------------------------


def _get(board: dict[str, Any], *path: str) -> Any:
    node: Any = board
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def freshness(board: dict[str, Any], et_date: date) -> dict[str, Any]:
    """Newest timestamp per source; STALE when it lags et_date past its cadence."""
    from trading.market_calendar import trading_days_between

    sources: dict[str, Any] = {}
    for lane in LANE_QUOTAS:
        sources[f"lane.{lane}"] = _get(board, "candidates", "lanes", lane, "newest")
    for idx in ("SPX", "NDX"):
        sources[f"index.gex.{idx}"] = _get(board, "index", "gex", idx, "oi_as_of")
    walls = _get(board, "index", "oi_walls") or {}
    sources["index.oi_walls"] = _newest([_get(walls, r, "as_of") for r in OI_ROOTS])
    sources["index.prints"] = _newest(
        [p.get("ts") for p in _get(board, "index", "prints", "prints") or []]
    )
    sources["index.index_pressure"] = _get(board, "index", "index_pressure", "close", "date")
    sources["index.flow_report"] = _get(board, "index", "flow_report", "et_date")
    sources["index.overnight_flow"] = _get(board, "index", "overnight_flow", "session")
    sources["macro.regime"] = _get(board, "macro", "market", "regime", "as_of")
    for axis, v in (_get(board, "macro", "market", "axes") or {}).items():
        sources[f"macro.axis.{axis}"] = v.get("as_of") if isinstance(v, dict) else None
    last_bars = [v.get("last_bar") for v in (_get(board, "sectors", "sectors") or {}).values()
                 if isinstance(v, dict)]
    # The stalest sector is the one that matters: one frozen series skews every rel.
    sources["sectors.bars"] = min([b for b in last_bars if b], default=None)

    out: dict[str, Any] = {}
    for name, ts in sources.items():
        cadence = _CADENCE_SESSIONS.get(name, _CADENCE_SESSIONS.get(name.rsplit(".", 1)[0], 0))
        entry: dict[str, Any] = {"newest": ts, "allowed_lag_sessions": cadence}
        if not ts:
            entry["status"] = "MISSING"
        else:
            day = _et_day(ts) if "T" in str(ts) else str(ts)[:10]
            lag = trading_days_between(date.fromisoformat(day), et_date)
            entry["lag_sessions"] = lag
            entry["status"] = "STALE" if lag > cadence else "ok"
        out[name] = entry
    return out


# --- build / persist / load ---------------------------------------------------------------


async def abuild_board(et_date: date | None = None) -> dict[str, Any]:
    from trading.market_calendar import last_closed_session

    et_date = et_date or last_closed_session()
    nxt = next_session(et_date)
    board: dict[str, Any] = {
        "et_date": et_date.isoformat(),
        "next_session": nxt.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "note": (
            "Perception, not a verdict: lanes say which angle lit a name, never how "
            "likely it is to move. Live readers (quote/technicals/news in the cards, gex, "
            "market, overnight_flow) reflect generated_at, not et_date."
        ),
    }
    board["candidates"] = await _guard("candidates", _candidates_section, et_date, nxt)
    board["index"] = await _guard("index", _index_section, et_date)
    board["macro"] = await _guard("macro", _macro_section)
    board["sectors"] = await _guard("sectors", asyncio.to_thread, _sectors_section, et_date)
    board["events"] = await _guard("events", _events_section, et_date, nxt, board["macro"])
    board["freshness"] = await _guard("freshness", freshness, board, et_date)
    return board


def build_board(et_date: date | None = None) -> dict[str, Any]:
    """The deterministic nightly board for `et_date` (default: the last CLOSED session)."""
    return asyncio.run(abuild_board(et_date))


def _has_data(v: Any) -> bool:
    return isinstance(v, dict) and bool(v) and not _is_error(v) and not v.get("unavailable")


def board_is_empty(board: dict[str, Any]) -> bool:
    cards = _get(board, "candidates", "cards") or []
    index = board.get("index") if _has_data(board.get("index")) else {}
    parts = [
        *(index.get("gex") or {}).values(),
        *(index.get("index_pressure") or {}).values(),
        *(index.get(k) for k in ("oi_walls", "prints", "flow_report", "overnight_flow")),
    ]
    return not cards and not any(_has_data(p) for p in parts)


def save_board(board: dict[str, Any], *, root: Path | None = None) -> Path:
    out_dir = (root or BRIEFS_DIR) / board["et_date"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "board.json").write_text(json.dumps(board, indent=1, default=str), encoding="utf-8")
    (out_dir / "board.md").write_text(render_board_md(board), encoding="utf-8")
    return out_dir


def load_board(et_date: str | None = None, *, root: Path | None = None) -> dict[str, Any] | None:
    """The stored board for `et_date` (YYYY-MM-DD), or the newest one; None if absent."""
    base = root or BRIEFS_DIR
    if et_date:
        path = base / et_date / "board.json"
    else:
        days = sorted(p for p in base.glob("*/board.json")) if base.exists() else []
        path = days[-1] if days else base / "_none_"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# --- markdown -----------------------------------------------------------------------------


def _money(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return "n/a"
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v / div:,.2f}{suffix}"
    return f"${v:,.0f}"


def _kv(d: Any, keys: tuple[str, ...] | None = None) -> str:
    if not isinstance(d, dict):
        return str(d)
    items = [(k, d.get(k)) for k in keys] if keys else list(d.items())
    return ", ".join(f"{k} {v}" for k, v in items if v is not None)


def _err_line(v: Any) -> str | None:
    if _is_error(v):
        return f"_ERROR: {v['error']}_"
    if isinstance(v, dict) and v.get("unavailable"):
        return f"_unavailable: {v['unavailable']}_" if v["unavailable"] is not True else "_unavailable_"
    return None


def _print_line(p: dict[str, Any]) -> str:
    return (
        f"{p.get('right')} {p.get('strike')} exp {p.get('expiry')} ({p.get('dte')}d, OTM "
        + (f"{p['otm_pct']}%" if p.get("otm_pct") is not None
           else f"{p.get('otm_pct_vs_close_spot')}% vs close spot")
        + f") {p.get('event_type')} {_money(p.get('premium_usd'))} "
        f"dir {p.get('direction')}/mid {p.get('mid_side')}"
        + (f" rarity_p {p['rarity_p']}" if p.get("rarity_p") is not None else "")
    )


def _render_card(c: dict[str, Any]) -> list[str]:
    lines = [f"### {c['symbol']} — angles: {', '.join(c.get('angles') or [])} "
             f"(lanes: {', '.join(c.get('lanes') or [])})"]
    for lane, why in (c.get("why") or {}).items():
        lines.append(f"- **{lane}**: {why}")
    if c.get("day"):
        lines.append(f"- day: {_kv(c['day'])}")
    if c.get("quote"):
        lines.append(f"- quote: {_kv(c['quote'])}")
    if c.get("rvol_intraday"):
        lines.append(f"- intraday rvol: {_kv(c['rvol_intraday'])}")
    if c.get("technicals"):
        lines.append(f"- technicals: {_kv(c['technicals'])}")
    if c.get("levels"):
        lines.append(f"- levels: {_kv(c['levels'])}")
    if c.get("earnings"):
        lines.append(f"- earnings: {_kv(c['earnings'])}")
    f = c.get("fundamentals") or {}
    if f:
        q = f.get("quarter")
        lines.append(
            f"- fundamentals: {_kv({k: v for k, v in f.items() if k != 'quarter'})}"
            + (f"; quarter: {_kv(q)}" if q else "")
        )
    if c.get("option_prints"):
        lines.append("- top option prints today:")
        lines += [f"  - {_print_line(p)}" for p in c["option_prints"]]
    if c.get("tape_blocks"):
        lines.append("- equity tape blocks: " + "; ".join(
            f"{t.get('event_type')} {t.get('size')} @ {t.get('price')} ({_money(t.get('notional_usd'))}, "
            f"{t.get('direction')})" for t in c["tape_blocks"]))
    if isinstance(c.get("news"), list) and c["news"]:
        lines.append("- news:")
        lines += [f"  - {n.get('datetime')} — {n.get('headline')}" for n in c["news"]]
    for kind in ("news", "social"):
        if c.get(f"{kind}_count_vs_norm"):
            lines.append(f"- {kind} items that day vs 20d own norm: "
                         f"{_kv(c[f'{kind}_count_vs_norm'])}")
    if c.get("insider_buys_30d"):
        lines.append("- insider buys (30d): " + "; ".join(_kv(i) for i in c["insider_buys_30d"]))
    if c.get("errors"):
        lines.append(f"- _missing blocks: {_kv(c['errors'])}_")
    return lines + [""]


def render_board_md(board: dict[str, Any]) -> str:
    out = [
        f"# Brief board — {board.get('et_date')}",
        "",
        f"Generated {board.get('generated_at')} · next session {board.get('next_session')}",
        "",
        f"_{board.get('note')}_",
        "",
        "## Candidates",
        "",
    ]
    cands = board.get("candidates")
    if _err_line(cands):
        out.append(_err_line(cands) or "")
    else:
        out.append("| lane | quota | lit | admitted | newest |")
        out.append("|---|---|---|---|---|")
        for lane, s in (cands.get("lanes") or {}).items():
            if _is_error(s):
                out.append(f"| {lane} | | | ERROR: {s['error']} | |")
            else:
                out.append(f"| {lane} | {s['quota']} | {s['lit']} | "
                           f"{', '.join(s['admitted']) or '—'} | {s.get('newest')} |")
        out.append("")
        for card in cands.get("cards") or []:
            out += _render_card(card)

    out += ["## Index frame", ""]
    index = board.get("index")
    if _err_line(index):
        out.append(_err_line(index) or "")
    else:
        index = {k: index.get(k) or {"unavailable": "not built"} for k in (
            "gex", "oi_walls", "prints", "index_pressure", "flow_report", "overnight_flow",
            "futures")}
        gex = index["gex"]
        if _err_line(gex):
            out.append(f"- **gex**: {_err_line(gex)}")
        for idx, g in ({} if _err_line(gex) else gex).items():
            out.append(f"- **{idx} gex**: " + (_err_line(g) or _kv(g, _GEX_KEYS + ("priced_at",))))
        out.append(f"  - _{_GEX_CONVENTION_MD}_")
        walls = index.get("oi_walls")
        out.append("- **OI walls** (next 3 expiries, top 5 by OI):" + (
            f" {_err_line(walls)}" if _err_line(walls) else ""))
        if not _err_line(walls):
            for root in OI_ROOTS:
                for exp, rows in ((walls.get(root) or {}).get("expiries") or {}).items():
                    out.append(f"  - {root} {exp}: " + "; ".join(
                        f"{r['cp']}{r['strike']:g} OI {r['oi']:,} (Δ {r.get('oi_change')})"
                        for r in rows))
            out.append(f"  - _{walls.get('caveat')}_")
        prints = index.get("prints")
        out.append("- **Largest index/ETF prints today:**" + (
            f" {_err_line(prints)}" if _err_line(prints) else ""))
        if not _err_line(prints):
            out += [f"  - {p['root']} {_print_line(p)}" for p in prints["prints"]]
            out.append(f"  - _{prints['caveat']}_")
        pressure = index["index_pressure"]
        if _err_line(pressure):
            out.append(f"- **index pressure**: {_err_line(pressure)}")
        for w, ip in ({} if _err_line(pressure) else pressure).items():
            out.append(f"- **index pressure ({w})**: " + (_err_line(ip) or (
                f"{ip.get('date')} {ip.get('window_et')} — " + "; ".join(
                    f"{r['underlying']} net {_money(r['net_usd'])} "
                    f"(classified {r['classified_frac']})" for r in ip["rows"]))))
        fr = index.get("flow_report")
        out.append("- **flow report**: " + (_err_line(fr) or f"{fr.get('title')}"))
        if not _err_line(fr):
            out += [f"  - {c.get('text')}" for c in fr.get("characterizations") or []]
        ov = index.get("overnight_flow")
        out.append("- **overnight (GTH)**: " + (_err_line(ov) or _kv(ov, (
            "session", "prints", "notional", "put_share_notional", "zero_dte_share_notional",
            "macro_window_share"))))
        if isinstance(ov, dict) and isinstance(ov.get("baseline"), dict):
            out.append(f"  - vs baseline: {_kv(ov['baseline'])}")
        out.append(f"- **futures**: {_err_line(index.get('futures'))}")
    out.append("")

    out += ["## Macro frame", ""]
    macro = board.get("macro")
    if _err_line(macro):
        out.append(_err_line(macro) or "")
    else:
        m = macro.get("market") or {"unavailable": "not built"}
        if _err_line(m):
            out.append(f"- market: {_err_line(m)}")
        else:
            out.append(f"- regime: {_kv(m.get('regime'))}")
            for axis, v in (m.get("axes") or {}).items():
                out.append(f"- axis {axis}: " + _kv(v, ("series", "value", "as_of", "pctile",
                                                         "bucket", "history_since")))
            out.append("- series: " + "; ".join(
                f"{k} {v.get('value')} (p{v.get('pctile')}, {v.get('date')})"
                for k, v in (m.get("series") or {}).items()))
            out.append(f"- _{m.get('note')}_")
        frame = macro.get("book_frame_md") or "_not built_"
        out += ["", "### Book-pack market frame", "",
                _err_line(frame) or str(frame)]
    out.append("")

    out += ["## Long-horizon sectors", ""]
    sectors = board.get("sectors")
    if _err_line(sectors):
        out.append(_err_line(sectors) or "")
    else:
        out.append(f"_{sectors.get('basis')}_")
        out.append("")
        labels = [h for h, _ in HORIZONS]
        out.append("| ETF | " + " | ".join(f"{h} (rel SPY)" for h in labels)
                   + " | from ATH | vs 200d | last bar |")
        out.append("|---" * (len(labels) + 4) + "|")
        rows = [("SPY", sectors.get("SPY"))] + list((sectors.get("sectors") or {}).items())
        for sym, s in rows:
            if _err_line(s) or not isinstance(s, dict):
                out.append(f"| {sym} | {_err_line(s)} |")
                continue
            cells = []
            for h in labels:
                ret = (s.get("returns") or {}).get(h)
                rel = (s.get("rel_spy_pp") or {}).get(h)
                cells.append("n/a" if not ret else f"{ret['pct']}%" + (
                    f" ({rel:+})" if rel is not None else ""))
            out.append(f"| {sym} | " + " | ".join(cells) + f" | {s.get('drawdown_from_ath_pct')}% "
                       f"| {s.get('vs_sma200_pct')}% | {s.get('last_bar')} |")
        spy_ret = (sectors.get("SPY") or {}).get("returns") or {}
        out.append("")
        out.append("Windows: " + "; ".join(
            f"{h} {r['from']}→{r['to']}" for h, r in spy_ret.items() if r))
    out.append("")

    out += ["## Events", ""]
    ev = board.get("events")
    if _err_line(ev):
        out.append(_err_line(ev) or "")
    else:
        nxt = ev.get("macro_next_session") or []
        out.append(f"- macro releases {ev.get('next_session')}: " + (
            _err_line(nxt) or (", ".join(e.get("release", "") for e in nxt) or "none scheduled")))
        if ev.get("not_covered"):
            out.append(f"  - _not covered: {ev['not_covered']}_")
        earn = ev.get("earnings") or []
        out.append("- earnings after the close / before the next open: " + (
            _err_line(earn) or (", ".join(f"{e['symbol']} ({e['when']})" for e in earn) or "none")))
    out.append("")

    out += ["## Freshness", ""]
    fresh = board.get("freshness")
    if _err_line(fresh):
        out.append(_err_line(fresh) or "")
    else:
        out.append("| source | newest | lag (sessions) | allowed | status |")
        out.append("|---|---|---|---|---|")
        for name, f in fresh.items():
            out.append(f"| {name} | {f.get('newest')} | {f.get('lag_sessions', '')} | "
                       f"{f['allowed_lag_sessions']} | {f['status']} |")
    return "\n".join(out) + "\n"


_GEX_CONVENTION_MD = (
    "net = call gamma − put gamma ASSUMES dealers long calls / short puts — a convention, "
    "not measured inventory; untested against forward returns"
)
