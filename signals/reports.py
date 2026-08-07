"""Report generation — reuses existing signal/compute helpers (no duplicate math).

Two families live here:
  * ``morning_levels`` / ``levels`` — deterministic plaintext bodies built from
    session-node math (no LLM).
  * ``morning_brief`` / ``evening_brief`` / ``on_request`` — LLM-authored briefs
    that gather the app's own data and ask the configured (provider-agnostic)
    LLM to WRITE the report, rendered to HTML. Generation is fail-soft: an
    LLM/data failure stores an error-state report, it never raises.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm
from ai.llm.structured import parse_json_object
from ai.research.report import render_report_html
from config.settings import short_selling_allowed
from db.base import DEFAULT_DB_PATH
from db.reports import REPORT_KIND_LABELS as _REPORT_KIND_LABELS
from db.reports import save_report
from db.strategies import get_strategy
from signals.session_node_strategy import compute_session_levels
from trading.market_hours import _ET

logger = logging.getLogger(__name__)

SESSION_NODE_STRATEGY_ID = "session_node_spy"

# Human labels per kind (title prefixes + HTML meta line for the LLM briefs).
# Moved to db/reports.py so the API can serve it as `kind_label` — re-exported
# here because this module's callers have always imported it from here.
REPORT_KIND_LABELS = _REPORT_KIND_LABELS

# Shared JSON contract appended to every brief system prompt.
_BRIEF_JSON_CONTRACT = (
    "Return JSON only, with keys: title (string), summary (string, 1-3 "
    "sentences), metrics (array of {label, value} — short numeric callouts), "
    "sections (array of {heading, body}), risks (array of strings), next_steps "
    "(array of strings). Ground every statement in the provided data — never "
    "invent prices, percentages, or win probabilities. When a data field is "
    "missing or empty, say so plainly instead of guessing."
)


def _format_levels_line(levels: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for lv in sorted(levels, key=lambda x: float(x["price"]), reverse=True):
        name = str(lv["name"])
        price = float(lv["price"])
        label = f"{name} {price:.2f}"
        if name == "POC":
            label += " ★"
        parts.append(label)
    return " / ".join(parts)


def _format_strategy_line(*, strategy_enabled: bool, shorts_on: bool) -> str:
    armed = "armed" if strategy_enabled else "disarmed"
    mode = "long+short" if shorts_on else "long-only"
    return f"Session-Node: {armed} · {mode}"


async def generate_morning_levels_report(
    symbol: str = "SPY",
    *,
    db_path: Path | None = None,
    et_date: date | None = None,
) -> dict[str, Any] | None:
    """Build and store a morning levels report from the latest completed session."""
    resolved = db_path if db_path is not None else DEFAULT_DB_PATH
    sym = symbol.strip().upper()
    levels = await compute_session_levels(sym, db_path=resolved)
    if levels is None:
        return None

    strat = get_strategy(SESSION_NODE_STRATEGY_ID, db_path=resolved)
    strategy_enabled = bool(strat and strat.get("enabled"))
    shorts_on = short_selling_allowed()

    if et_date is None:
        et_date = datetime.now(_ET).date()

    levels_line = _format_levels_line(levels["levels"])
    strategy_line = _format_strategy_line(
        strategy_enabled=strategy_enabled,
        shorts_on=shorts_on,
    )
    body = f"{levels_line}\n{strategy_line}"
    title = f"Morning Levels · {sym}"

    payload: dict[str, Any] = {
        **levels,
        "strategy_enabled": strategy_enabled,
        "shorts_on": shorts_on,
        "et_date": et_date.isoformat(),
    }
    return save_report(
        "morning_levels",
        sym,
        title,
        body,
        payload,
        db_path=resolved,
    )


async def generate_levels_report(
    symbols: list[str],
    *,
    db_path: Path | None = None,
    et_date: date | None = None,
) -> dict[str, Any] | None:
    """Deterministic session-node levels for one or more symbols (plaintext body).

    Generalizes ``morning_levels`` to any symbol set. Returns None when no
    symbol has session data (nothing worth pushing)."""
    resolved = db_path if db_path is not None else DEFAULT_DB_PATH
    syms = [s.strip().upper() for s in symbols if s and s.strip()]
    if not syms:
        return None
    if et_date is None:
        et_date = datetime.now(_ET).date()

    lines: list[str] = []
    any_levels = False
    for sym in syms:
        levels = await compute_session_levels(sym, db_path=resolved)
        if levels is None:
            lines.append(f"{sym}: no session data")
            continue
        any_levels = True
        lines.append(f"{sym}: {_format_levels_line(levels['levels'])}")
    if not any_levels:
        return None

    title = "Levels · " + ", ".join(syms)
    payload = {
        "symbols": syms,
        "et_date": et_date.isoformat(),
        "dedup_key": ",".join(syms),
    }
    return save_report(
        "levels", syms[0], title, "\n".join(lines), payload, db_path=resolved
    )


# --- LLM-authored briefs -------------------------------------------------------


def _shape_llm_report(
    raw: str, *, title: str, symbol: str | None
) -> dict[str, Any]:
    """Parse the LLM's JSON into the render_report_html dict shape (fail-soft)."""
    data = parse_json_object(raw) or {}

    sections: list[dict[str, Any]] = []
    raw_sections = data.get("sections")
    if isinstance(raw_sections, list):
        for sec in raw_sections:
            if isinstance(sec, dict):
                sections.append(
                    {
                        "heading": str(sec.get("heading", "Section")),
                        "body": str(sec.get("body", "")),
                        "citations": [],
                    }
                )

    metrics: list[dict[str, str]] = []
    raw_metrics = data.get("metrics")
    if isinstance(raw_metrics, list):
        for m in raw_metrics:
            if isinstance(m, dict):
                metrics.append(
                    {
                        "label": str(m.get("label", "")),
                        "value": str(m.get("value", "")),
                    }
                )

    summary = str(data.get("summary", ""))
    return {
        "title": str(data.get("title") or title),
        "symbol": symbol,
        "summary": summary,
        "metrics": metrics,
        "sections": sections
        or [{"heading": "Brief", "body": summary or "No content generated.", "citations": []}],
        "risks": [str(r) for r in (data.get("risks") or []) if r],
        "next_steps": [str(n) for n in (data.get("next_steps") or []) if n],
        "sources": [],
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _error_report_dict(*, title: str, symbol: str | None, message: str) -> dict[str, Any]:
    return {
        "title": title,
        "symbol": symbol,
        "summary": f"This report could not be generated: {message}",
        "metrics": [],
        "sections": [{"heading": "Generation error", "body": message, "citations": []}],
        "risks": [],
        "next_steps": ["Retry once data and the LLM provider are available."],
        "sources": [],
        "generated_at": datetime.now(UTC).isoformat(),
    }


async def _author_brief(
    *, system_prompt: str, inputs: dict[str, Any], title: str, symbol: str | None
) -> dict[str, Any]:
    llm = get_llm()
    messages = [
        LLMMessage(role="system", content=f"{system_prompt}\n\n{_BRIEF_JSON_CONTRACT}"),
        LLMMessage(
            role="user",
            content=json.dumps(inputs, indent=2, default=str)[:14000],
        ),
    ]
    raw = await llm.acomplete(messages, max_tokens=1800)
    return _shape_llm_report(raw, title=title, symbol=symbol)


async def _store_brief(
    *,
    kind: str,
    title: str,
    symbol: str,
    system_prompt: str,
    inputs: dict[str, Any],
    dedup_key: str | None,
    symbols: list[str],
    et_date: date,
    db_path: Path,
) -> dict[str, Any]:
    """Author + render + store an LLM brief. Never raises — an LLM/data failure
    is captured as an error-state HTML report so the scheduler keeps running."""
    meta_label = REPORT_KIND_LABELS.get(kind, "Brief")
    try:
        report_json = await _author_brief(
            system_prompt=system_prompt, inputs=inputs, title=title, symbol=symbol
        )
        error = False
    except Exception as exc:  # noqa: BLE001 — fail-soft by contract
        logger.exception("Report brief authoring failed: kind=%s", kind)
        report_json = _error_report_dict(title=title, symbol=symbol, message=str(exc))
        error = True

    html_body = render_report_html(report_json, meta_label=meta_label)
    payload: dict[str, Any] = {
        "report": report_json,
        "symbols": symbols,
        "et_date": et_date.isoformat(),
        "dedup_key": dedup_key,
        "error": error,
    }
    return save_report(
        kind,
        symbol,
        report_json["title"],
        html_body,
        payload,
        format="html",
        db_path=db_path,
    )


async def _gather_symbol_contexts(
    symbols: list[str], *, db_path: Path, limit: int = 8
) -> list[dict[str, Any]]:
    from ai.perception.context import build_symbol_context

    out: list[dict[str, Any]] = []
    for sym in symbols[:limit]:
        try:
            out.append(await build_symbol_context(sym, db_path=db_path))
        except Exception:
            logger.debug("Symbol context failed for %s", sym, exc_info=True)
    return out


def _upcoming_earnings(symbols: list[str], *, db_path: Path) -> list[dict[str, str]]:
    from db.earnings_calendar import next_earnings_date

    today = datetime.now(_ET).date().isoformat()
    out: list[dict[str, str]] = []
    for sym in symbols:
        try:
            nxt = next_earnings_date(sym, today, db_path=db_path)
        except Exception:
            nxt = None
        if nxt:
            out.append({"symbol": sym, "date": nxt})
    return sorted(out, key=lambda r: r["date"])


def _portfolio_summary(*, db_path: Path) -> list[dict[str, Any]]:
    from db.sqlite import get_all_latest_snapshots
    from portfolio.analytics import compute_portfolio_analytics

    summaries: list[dict[str, Any]] = []
    try:
        snapshots = get_all_latest_snapshots(db_path=db_path)
    except Exception:
        logger.debug("Portfolio snapshot fetch failed", exc_info=True)
        return summaries
    for snap in snapshots:
        analytics = snap.get("analytics")
        if analytics is None:
            try:
                analytics = compute_portfolio_analytics(snap)
            except Exception:
                analytics = None
        positions = snap.get("positions") or []
        summaries.append(
            {
                "account": snap.get("account_id") or snap.get("broker"),
                "captured_at": snap.get("captured_at"),
                "analytics": analytics,
                "positions": positions[:15],
            }
        )
    return summaries


def _resolve_symbols(symbols: list[str] | None) -> list[str]:
    if symbols:
        return [s.strip().upper() for s in symbols if s and s.strip()]
    from config.settings import get_watchlist

    try:
        return get_watchlist()
    except Exception:
        logger.debug("Watchlist fetch failed", exc_info=True)
        return ["SPY"]


async def generate_morning_brief_report(
    symbols: list[str] | None = None,
    *,
    db_path: Path | None = None,
    et_date: date | None = None,
) -> dict[str, Any]:
    """Pre-open brief: overnight/premarket moves, watchlist status, upcoming
    earnings, key levels, and a plan — LLM-authored from the app's own data."""
    resolved = db_path if db_path is not None else DEFAULT_DB_PATH
    if et_date is None:
        et_date = datetime.now(_ET).date()
    syms = _resolve_symbols(symbols)

    contexts = await _gather_symbol_contexts(syms, db_path=resolved)
    inputs = {
        "as_of": datetime.now(_ET).isoformat(),
        "et_date": et_date.isoformat(),
        "watchlist": contexts,
        "upcoming_earnings": _upcoming_earnings(syms, db_path=resolved),
        "positions": _portfolio_summary(db_path=resolved),
    }
    system_prompt = (
        "You are a pre-market trading brief writer for a busy trader who steps "
        "away during the day. Using the provided app data (per-symbol contexts "
        "with quotes/RVOL/session-node levels/technicals/earnings, upcoming "
        "earnings, and open positions), write a concise MORNING BRIEF for "
        "today's US session. Cover, in this order: overnight / premarket moves, "
        "watchlist status, upcoming earnings, key session levels, and a short "
        "actionable plan."
    )
    title = f"Morning Brief · {et_date.isoformat()}"
    return await _store_brief(
        kind="morning_brief",
        title=title,
        symbol="MARKET",
        system_prompt=system_prompt,
        inputs=inputs,
        dedup_key="morning_brief",
        symbols=syms,
        et_date=et_date,
        db_path=resolved,
    )


async def generate_evening_brief_report(
    symbols: list[str] | None = None,
    *,
    db_path: Path | None = None,
    et_date: date | None = None,
) -> dict[str, Any]:
    """Post-close brief: day recap, positions + P&L, what moved, tomorrow's
    calendar — LLM-authored from the app's own data."""
    resolved = db_path if db_path is not None else DEFAULT_DB_PATH
    if et_date is None:
        et_date = datetime.now(_ET).date()
    syms = _resolve_symbols(symbols)

    contexts = await _gather_symbol_contexts(syms, db_path=resolved)
    inputs = {
        "as_of": datetime.now(_ET).isoformat(),
        "et_date": et_date.isoformat(),
        "positions": _portfolio_summary(db_path=resolved),
        "watchlist": contexts,
        "upcoming_earnings": _upcoming_earnings(syms, db_path=resolved),
    }
    system_prompt = (
        "You are a post-close trading brief writer. Using the provided app data "
        "(portfolio snapshot with positions and P&L, watchlist symbol contexts "
        "with day changes, and upcoming earnings), write a concise EVENING "
        "BRIEF. Cover, in this order: a recap of the day, open positions and "
        "their P&L, what moved on the watchlist, and a preview of tomorrow's "
        "calendar."
    )
    title = f"Evening Brief · {et_date.isoformat()}"
    return await _store_brief(
        kind="evening_brief",
        title=title,
        symbol="PORTFOLIO",
        system_prompt=system_prompt,
        inputs=inputs,
        dedup_key="evening_brief",
        symbols=syms,
        et_date=et_date,
        db_path=resolved,
    )


async def generate_on_request_report(
    topic: str,
    symbols: list[str] | None = None,
    *,
    db_path: Path | None = None,
    et_date: date | None = None,
) -> dict[str, Any]:
    """Founder-supplied topic/prompt — LLM writes a focused report grounded in
    the app data for any named symbols (plus the portfolio)."""
    resolved = db_path if db_path is not None else DEFAULT_DB_PATH
    if et_date is None:
        et_date = datetime.now(_ET).date()
    clean_topic = topic.strip() or "On-request report"
    syms = [s.strip().upper() for s in (symbols or []) if s and s.strip()]

    contexts = await _gather_symbol_contexts(syms, db_path=resolved) if syms else []
    inputs = {
        "as_of": datetime.now(_ET).isoformat(),
        "request": clean_topic,
        "symbols": syms,
        "symbol_contexts": contexts,
        "positions": _portfolio_summary(db_path=resolved),
    }
    system_prompt = (
        "You are a trading research assistant. Write a focused report that "
        "answers the user's request below using ONLY the provided app data. "
        f"Request: {clean_topic}"
    )
    symbol = syms[0] if syms else "TOPIC"
    return await _store_brief(
        kind="on_request",
        title=clean_topic[:120],
        symbol=symbol,
        system_prompt=system_prompt,
        inputs=inputs,
        dedup_key=None,
        symbols=syms,
        et_date=et_date,
        db_path=resolved,
    )


# ---------------------------------------------------------------------------
# Flow decomposition — deterministic post-close report (no LLM): what the
# index-pressure gauge's premium was actually made of. Numbers-of-record come
# from the gauge DB; order/tenor/concentration decomposition comes from the
# print-lab capture. See signals/options_flow/daily_report.py for the rules.
# ---------------------------------------------------------------------------

_FLOW_WINDOW_TITLES = {"open": "Open window", "close": "Close window"}
# Don't generate a same-day report before the close-window finalize (~16:06 ET)
# has had a chance to land; the 15-min scheduler poll simply retries.
_FLOW_REPORT_READY_ET = (16, 20)


def _fmt_musd(v: float) -> str:
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v) / 1e6:,.1f}M"


def _flow_gauge_lane_sums(gauge_rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    from signals.options_flow.constants import INDEX_MEGACAP_SET

    lanes = {
        "index": {"bull_usd": 0.0, "bear_usd": 0.0, "unclassified_usd": 0.0},
        "single_name": {"bull_usd": 0.0, "bear_usd": 0.0, "unclassified_usd": 0.0},
    }
    for r in gauge_rows:
        lane = "index" if r["underlying"] in INDEX_MEGACAP_SET else "single_name"
        for k in ("bull_usd", "bear_usd", "unclassified_usd"):
            lanes[lane][k] += float(r[k])
    for d in lanes.values():
        d["net_usd"] = d["bull_usd"] - d["bear_usd"]
    return lanes


def _load_print_lab_rows(db_path: Path, date_iso: str) -> list[dict[str, Any]] | None:
    """Parse one day's print-lab JSONL (None = file absent). Bad lines skipped."""
    path = db_path.parent / "research" / "print_lab" / f"{date_iso}.jsonl"
    gz_path = path.with_suffix(".jsonl.gz")
    if path.exists():
        f = path.open()
    elif gz_path.exists():
        # Nightly retention gzips prior sessions' files in place.
        f = gzip.open(gz_path, "rt")
    else:
        return None
    rows: list[dict[str, Any]] = []
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _flow_window_section(
    window: str,
    analysis: dict[str, Any],
    gauge_rows: list[dict[str, Any]],
    chars: list[dict[str, str]],
    warnings: list[str],
) -> dict[str, str]:
    from signals.options_flow.daily_report import WINDOW_LABELS

    lines: list[str] = []
    for w in warnings:
        lines.append(f"**{w}**")
    if gauge_rows:
        lanes = _flow_gauge_lane_sums(gauge_rows)
        gross = sum(
            v[k] for v in lanes.values() for k in ("bull_usd", "bear_usd", "unclassified_usd")
        )
        classified = sum(v["bull_usd"] + v["bear_usd"] for v in lanes.values())
        idx, sn = lanes["index"], lanes["single_name"]
        lines.append(
            f"Gauge: index net {_fmt_musd(idx['net_usd'])} "
            f"({_fmt_musd(idx['bull_usd'])} bull / {_fmt_musd(idx['bear_usd'])} bear) · "
            f"single names net {_fmt_musd(sn['net_usd'])} "
            f"({_fmt_musd(sn['bull_usd'])} / {_fmt_musd(sn['bear_usd'])}) · "
            f"{(classified / gross if gross else 0):.0%} of premium side-classified"
        )
        top_names = sorted(gauge_rows, key=lambda r: -abs(float(r["net_usd"])))[:4]
        lines.append(
            "By name (net): "
            + " · ".join(f"{r['underlying']} {_fmt_musd(float(r['net_usd']))}" for r in top_names)
        )
    else:
        lines.append("**No gauge rows recorded for this window.**")

    conc = analysis["concentration"]
    if conc["n_orders"]:
        lines.append("")
        lines.append("Largest orders (prints clustered per contract/side/second):")
        for o in analysis["top_orders"]:
            stance = "bull" if o["stance"] == "bull" else "bear"
            lines.append(
                f"{o['time_et']} ET — {o['contracts']:,}x {o['occ']} ({o['dte']}d, "
                f"{'call' if o['right'] == 'c' else 'put'} {o['dir'].replace('_side', '')}) "
                f"= {_fmt_musd(o['premium_usd'])} [{stance}]"
            )
        lines.append(
            f"Concentration: top-10 orders = {conc['top10_share']:.0%} of classified "
            f"order premium ({conc['n_orders']:,} orders / {conc['n_prints']:,} prints)"
        )
        if gauge_rows:
            gauge_classified = sum(float(r["bull_usd"]) + float(r["bear_usd"]) for r in gauge_rows)
            if gauge_classified > 0 and conc["classified_usd"] > gauge_classified * 1.15:
                extra = conc["classified_usd"] - gauge_classified
                lines.append(
                    f"Basis note: the decomposition carries ~{_fmt_musd(extra)} more "
                    "classified premium than the gauge — prints the gauge excludes "
                    "(multi-leg/hygiene), so large orders above may include spread legs."
                )
        tenor_bits = []
        for t in analysis["tenor"]:
            net = t["bull_usd"] - t["bear_usd"]
            if t["bull_usd"] or t["bear_usd"]:
                tenor_bits.append(f"{t['bucket']} {_fmt_musd(net)}")
        if tenor_bits:
            lines.append("Tenor (net, classified): " + " · ".join(tenor_bits))
    else:
        lines.append("Print-lab decomposition unavailable for this window.")

    if chars:
        lines.append("")
        lines.append("Reads (rule-based, heuristic):")
        for c in chars:
            lines.append(f"• {c['text']}")
    return {
        "heading": f"{_FLOW_WINDOW_TITLES.get(window, window)} ({WINDOW_LABELS.get(window, '')})",
        "body": "\n".join(lines),
    }


async def generate_flow_decomp_report(
    *,
    db_path: Path | None = None,
    et_date: date | None = None,
    now_et: datetime | None = None,
) -> dict[str, Any] | None:
    """Build + store the post-close flow decomposition report.

    Returns None (scheduler retries) when it's a non-trading day, when today's
    close-window finalize hasn't had time to land yet, or when there is no flow
    data at all for the date.
    """
    from db.index_pressure import day_completeness, day_summary
    from signals.options_flow import daily_report as flow
    from trading.market_calendar import is_early_close, is_monthly_opex, is_trading_day

    resolved = db_path if db_path is not None else DEFAULT_DB_PATH
    if now_et is None:
        now_et = datetime.now(_ET)
    if et_date is None:
        et_date = now_et.date()
    if not is_trading_day(et_date):
        return None
    date_iso = et_date.isoformat()

    early = is_early_close(et_date)
    opex = is_monthly_opex(et_date)
    expected_windows = ["open"] if early else ["open", "close"]

    completeness = {r["window"]: r for r in day_completeness(date_iso, db_path=resolved)}
    if et_date == now_et.date():
        # Mid-window flushes write the day row with finalized_at NULL — only a
        # non-null finalized_at means the window's accounting is complete.
        missing = [
            w for w in expected_windows if not (completeness.get(w) or {}).get("finalized_at")
        ]
        if missing and (now_et.hour, now_et.minute) < _FLOW_REPORT_READY_ET:
            return None

    gauge = {w: day_summary(date_iso, window=w, db_path=resolved) for w in expected_windows}
    prints = await asyncio.to_thread(_load_print_lab_rows, resolved, date_iso)
    if prints is None and not any(gauge.values()):
        return None

    windows: dict[str, dict[str, Any]] = {}
    characterizations: list[dict[str, str]] = []
    sections: list[dict[str, str]] = []
    for w in expected_windows:
        analysis = flow.analyze_window(prints or [], w)
        chars = flow.characterize(analysis, is_opex=opex)
        warnings: list[str] = []
        if not (completeness.get(w) or {}).get("finalized_at"):
            warnings.append(f"{w} window was never finalized — data may be incomplete.")
        if prints is None:
            warnings.append("Print-lab capture missing — gauge totals only.")
        sections.append(_flow_window_section(w, analysis, gauge[w], chars, warnings))
        analysis.pop("orders", None)  # full order list is large; payload keeps top_orders
        windows[w] = analysis
        characterizations.extend(chars)

    metrics: list[dict[str, str]] = []
    for w in expected_windows:
        lanes = _flow_gauge_lane_sums(gauge[w])
        metrics.append({"label": f"{w.capitalize()} index net", "value": _fmt_musd(lanes["index"]["net_usd"])})
    metrics.append({"label": "Session", "value": "Monthly OpEx" if opex else ("Early close" if early else "Regular")})

    summary_bits = []
    for w in expected_windows:
        lanes = _flow_gauge_lane_sums(gauge[w])
        summary_bits.append(f"{w}: index net {_fmt_musd(lanes['index']['net_usd'])}")
    summary = "Gauge " + "; ".join(summary_bits) + "."
    if characterizations:
        summary += " " + characterizations[0]["text"]
    else:
        summary += " No characterization rule fired — flow looked broad rather than block-driven."

    title = f"Flow Decomposition · {date_iso}" + (" · OpEx" if opex else "")
    report_json: dict[str, Any] = {
        "title": title,
        "symbol": "INDEX",
        "summary": summary,
        "metrics": metrics,
        "sections": sections,
        "risks": list(flow.CAVEATS),
        "next_steps": [],
        "generated_at": datetime.now(_ET).isoformat(),
    }
    html_body = render_report_html(report_json, meta_label="Flow decomposition")
    payload: dict[str, Any] = {
        "et_date": date_iso,
        # The scheduler's once-per-day guard matches on payload dedup_key —
        # omitting it means regenerate + re-push every 15-min poll (review C1).
        "dedup_key": "flow_decomp",
        "opex": opex,
        "session": "early_close" if early else "full",
        "completeness": completeness,
        "gauge": gauge,
        "windows": windows,
        "characterizations": characterizations,
        "caveats": list(flow.CAVEATS),
    }
    return save_report(
        "flow_decomp", "INDEX", title, html_body, payload, format="html", db_path=resolved
    )
