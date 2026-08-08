"""Fusion Phase C2 — the DOSSIER BUILDER (deterministic claim matrix).

Pure code. **Zero LLM calls, zero network — store reads only.** Given an admitted
Fusion episode (one open evidence stack per symbol, ``db/fusion.py`` — ``market_day``
is the episode's *opening* session only, not a day key), assemble the fixed set of
perception ANGLES into a stable claim matrix that the Phase C3 investigator
(T1 null-hypothesis triage → REASON v1/v2) consumes. The investigator renders angle
VALUES from these claims deterministically and cites claim ids STRICTLY, so:

  * ``claim_id`` is deterministic + stable — ``f"{angle}:{n}"`` with ``n`` assigned
    in a FIXED angle order and a FIXED intra-angle sort (freshest-ts first, then
    value). The same episode state + same ``now`` in yields the same ids out
    (``tests/test_dossier.py``).
  * an angle with nothing to say is OMITTED (no empty claims); a dossier is valid
    with as few as the ``evidence`` angle alone.
  * ``now`` selects the primary ET window for flow/tape/news/catalyst/context
    (and the since-open upper bound on multi-day episodes), so it affects claim
    VALUES and ordering — not just freshness. Windowed aggregates name their
    window in the statement text.

★ CONSUMER CONTRACT (founder ruling 2026-08-02): **the investigator builds its prompt
from ``claims``; ``summary`` is a human/debug rendering.** ``claims`` is complete and
uncapped (``db/idea_dossiers`` persists the whole matrix in ``claims_json``); only
``summary`` is truncated at ``DOSSIER_SUMMARY_MAX_CHARS``. A claim whose line falls
outside that cap is still real and still citable — but a consumer that prompts off
``summary`` would make it BOTH invisible and uncitable, since the investigator cites
``claim_id``s it can only see in the text it was given. Build from ``claims``.

CONSUMER SURFACES (wired 2026-08-08; the REASON lane itself stays dark until armed):
  * ``research/reason.py`` — the C3 investigator; the ONLY caller of
    ``build_and_store_dossier`` (persists via ``db/idea_dossiers``).
  * ``ai/perception/resources.py`` ``dossier`` resource — chat agent, in-process
    books and headless book sessions; builds EPHEMERALLY (never stores).
  * ``GET /api/fusion/episodes/{id}/dossier`` — the evidence-stack widget's
    dossier view; also ephemeral.
Nothing else may write a dossier; the on-demand read paths call ``build_dossier``
only.
"""

from __future__ import annotations

import html
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from config.idea_engine import DOSSIER_SUMMARY_MAX_CHARS
from db import equity_tape as equity_tape_db
from db import fusion as fusion_db
from db import news as news_db
from db import options_flow as options_flow_db
from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.chart_features import init_chart_features
from db.earnings_calendar import last_earnings_date, next_earnings_date
from db.idea_dossiers import put_dossier
from db.index_pressure import day_summary
from db.macro import DEFAULT_MACRO_DB_PATH
from db.managed_positions import list_open_positions
from db.social_mentions import get_buzz
from research.edgar_bulk import DEFAULT_FACTS_DB_PATH
from research.fundamentals import DEFAULT_FUNDAMENTALS_DB_PATH, FINANCIAL_CONCEPTS
from signals.fusion.scorer import options_direction, tape_direction
from signals.macro_substrate import REPORT_AXES, regime_report
from trading.market_calendar import is_trading_day

_ET = ZoneInfo("America/New_York")

# Fixed angle order — governs how angles appear in the matrix + summary, and (with
# claim_id embedding the angle) makes the id scheme stable. Changing this order is a
# NEW estimand for the investigator, never a silent edit. ``evidence`` is LAST so the
# 4,000-char summary cap truncates the (still-redundant) evidence tail, not analysis.
# ``news`` sits next to ``catalyst`` (same event/catalyst family) — intentional NEW
# estimand (Evidence Bus §2.9 first post-v1 component, alone).
# ``fundamentals`` sits immediately before ``context`` — the company's own reported
# state reads ahead of the market-wide riders it is read against. Also a NEW
# estimand (Evidence Bus §2.9, one component at a time).
ANGLE_ORDER: tuple[str, ...] = (
    "flow",
    "tape",
    "structure",
    "attention",
    "catalyst",
    "news",
    "fundamentals",
    "context",
    "portfolio",
    "history",
    "evidence",
)

# Freshness horizons (labeled, deliberately simple). Intraday angles carry
# event-driven prints; session angles are current-state reads; catalyst/history are
# mechanical facts that don't stale on this timescale.
FRESH_INTRADAY_MINUTES = 30
_INTRADAY_ANGLES = frozenset({"evidence", "flow", "tape"})
# ``news`` is SESSION, not intraday: a 30-min horizon would mark same-session
# headlines stale within the hour, while a 3-day-old headline must not render as
# ``fresh``. Same-ET-day matches how a reader treats a headline's relevance.
_SESSION_ANGLES = frozenset({"structure", "attention", "context", "portfolio", "news"})
_ALWAYS_FRESH_ANGLES = frozenset({"catalyst", "history"})
# ``fundamentals`` is in NONE of the three on purpose: every fundamentals proto
# declares ``fresh`` explicitly (see EDGAR_CHECK_MAX_AGE_DAYS), so the angle-rule
# fallback never runs for it. Left out rather than guessed at, and the unreached
# fallback fails SAFE — the session rule would call a months-old 10-Q stale.

# Macro series are daily FRED prints with a publication lag — FRED almost never
# has a same-ET-day observation during a trading session, so the session-angle
# same-calendar-day rule would mark EVERY macro claim [stale] forever (measured
# 2026-08-02). A prior-day close IS today's regime; a week-old dollar is not.
# 4 calendar days covers a normal weekend (Fri→Mon = 3) or weekend + one market
# holiday (Fri→Tue = 4); 7 (= a week) is past this and must render stale.
# Independent of macro_substrate.REPORT_STALE_DAYS (that one is value-vs-missing).
MACRO_FRESH_MAX_GAP_DAYS = 4

# Social buzz below this ratio is not worth an attention claim (matches the intake's
# spike gate spirit — a boring reading is silence, not a dot).
_ATTENTION_MIN_RATIO = 1.0

# Headline claims: max 3 — slot A = min tag breadth in the window, B/C = freshest
# remaining (founder 2026-08-02: pure freshest-first favoured wide Benzinga roundups).
# Remainder counts the rest so the total is never silently deleted.
_NEWS_HEADLINE_CAP = 3
_NEWS_HEADLINE_MAX_CHARS = 140

# Fundamentals: four as-reported anchors — top line, bottom line, and two
# balance-sheet scales. Deliberately narrow. The summary is capped at
# DOSSIER_SUMMARY_MAX_CHARS and this angle competes with nine others; a wide
# statement dump would push flow/tape/news out of the render for a reader who is
# looking at one trade, not auditing a filing. The us-gaap tag preference lists
# come from ``research.fundamentals.FINANCIAL_CONCEPTS`` (filers are inconsistent,
# revenue most of all) — reused, never a second list.
_FUNDAMENTALS_METRICS: tuple[str, ...] = ("revenue", "net_income", "assets", "equity")

# Periodic reports only. 8-Ks and prospectuses carry XBRL facts too, but their
# facts are not a REPORTING PERIOD — the claim is "the company's latest reported
# period", and an 8-K earnings exhibit would mislabel that.
_PERIODIC_FORMS: tuple[str, ...] = ("10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F")
_ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "20-F", "40-F"})

# Expected span of the filing's own reporting period, used to pick the fiscal
# period out of a filing that ALSO carries year-to-date figures ending on the very
# same date (a Q3 10-Q reports 3-month AND 9-month revenue for period_end).
_ANNUAL_PERIOD_DAYS = 365
_QUARTER_PERIOD_DAYS = 91

# A prior-year comparative as reported IN THE SAME FILING: same tag, same unit,
# same span (±tolerance for 13-week fiscal calendars), ending about a year back.
_YOY_MIN_GAP_DAYS = 330
_YOY_MAX_GAP_DAYS = 400
_YOY_SPAN_TOLERANCE_DAYS = 10

# How old the EDGAR ingest check may be before a fundamentals claim stops
# asserting "this IS the latest filed period" (founder ruling 2026-08-02).
#
# NOT a horizon on the FILING: a 90-day-old 10-Q *is* the company's current
# reported state and stays current until the next one lands, so D4's day-count
# horizon is the wrong tool here. This is a horizon on OUR LOOKING — from inside
# the store, "no newer filing exists" and "we have not checked" are the same
# observation, so the only thing that can make the claim honest is knowing when
# we last asked SEC. Periodic reports land 0–3 days after the earnings release
# (measured: TSLA/GOOGL/MSFT all +1 day, NVDA same-day), so a check older than 3
# days is older than the entire release→filing window: a report could have come
# out AND been filed since we last looked. 3 also spans a normal Friday-check /
# Monday-build weekend, so a daily ingest never renders stale — while an ingest
# that has silently stopped does, which is exactly the failure this catches.
EDGAR_CHECK_MAX_AGE_DAYS = 3

_DIRECTIONAL = frozenset({"bull", "bear"})


# --------------------------------------------------------------------------- utils


def _as_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result else 0.0  # NaN guard


def _as_float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _parse_ts(ts: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:  # stores emit UTC ISO; a missing offset means UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _et_date(ts: Any) -> str:
    dt = _parse_ts(ts)
    return dt.astimezone(_ET).date().isoformat() if dt is not None else ""


def _is_fresh(angle: str, ts: Any, now: datetime) -> bool:
    """Value's ts within the angle's staleness horizon (see the constants above)."""
    if angle in _ALWAYS_FRESH_ANGLES:
        return True
    dt = _parse_ts(ts)
    if dt is None:
        return False
    if angle in _INTRADAY_ANGLES:
        return (now - dt).total_seconds() <= FRESH_INTRADAY_MINUTES * 60
    # session angles: fresh if read within the current ET session
    return dt.astimezone(_ET).date() == now.astimezone(_ET).date()


def _value_sort(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _trading_days_between(from_date: str, to_date: str) -> int:
    """Trading days from ``from_date`` (exclusive) to ``to_date`` (inclusive),
    NYSE-calendar-aware. ``-1`` on unparseable input, ``0`` when to<=from. Local
    stand-in for the earnings-drift counter, kept here so the dossier never pulls
    the research feature chain (INERT)."""
    from datetime import date, timedelta

    try:
        start = date.fromisoformat(from_date[:10])
        end = date.fromisoformat(to_date[:10])
    except ValueError:
        return -1
    if end <= start:
        return 0
    days = 0
    cur = start
    while cur < end:
        cur += timedelta(days=1)
        if is_trading_day(cur):
            days += 1
    return days


def _size_bucket(shares: float) -> str:
    magnitude = abs(shares)
    if magnitude < 100:
        return "small"
    if magnitude < 1000:
        return "medium"
    return "large"


# ------------------------------------------------------------------- angle builders
# Each returns a list of proto-claims (no claim_id yet) — dicts with keys
# statement / value / ts / direction. An angle with nothing to say returns [].


def _evidence_protos(episode_id: str, db_path: Path) -> list[dict[str, Any]]:
    """Episode dots aggregated by ``(component, actor_group, direction)``.

    One claim per key (count + dollar total for actor groups) — never one claim per
    dot. Aggregation preserves minority surfaces (e.g. news) as counted rows;
    capping/dropping dots would silently delete them. ``background_crowd`` raw is a
    multiple (RVOL), never dollars — keep the freshest reading, never sum it.
    """
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for dot in fusion_db.episode_dots(episode_id, db_path=db_path):
        component = str(dot.get("component") or "")
        group = str(dot.get("actor_group") or "")
        direction = str(dot.get("direction") or "none")
        key = (component, group, direction)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = {
                "count": 0,
                "points": 0.0,
                "raw_sum": 0.0,
                "raw_any": False,
                "crowd_raw": None,
                "ts": "",
            }
            buckets[key] = bucket
        bucket["count"] += 1
        bucket["points"] += _as_float(dot.get("points"))
        ts = str(dot.get("ts") or "")
        raw = _as_float_or_none(dot.get("raw_value"))
        if group in ("options_actor", "equity_actor") and raw is not None:
            bucket["raw_sum"] += raw
            bucket["raw_any"] = True
        if ts >= bucket["ts"]:
            bucket["ts"] = ts
            if group == "background_crowd" and raw is not None:
                bucket["crowd_raw"] = raw

    protos: list[dict[str, Any]] = []
    for (component, group, direction), bucket in buckets.items():
        statement = (
            f"{component} · {group} · {direction} · {bucket['count']:,} dots"
        )
        if group in ("options_actor", "equity_actor") and bucket["raw_any"]:
            statement += f" · ${bucket['raw_sum']:,.0f} total"
        elif group == "background_crowd" and bucket["crowd_raw"] is not None:
            statement += f" · {bucket['crowd_raw']:.2f}× RVOL-ex-prints"
        protos.append(
            {
                "statement": statement,
                "value": bucket["points"],
                "ts": bucket["ts"],
                "direction": direction,
            }
        )
    return protos


def _window_label(kind: str, *, window_day: str, opened_day: str) -> str:
    """Suffix that names the claim's window — undated aggregates hid the birth-day bug."""
    if kind == "primary":
        return f"[{window_day}]"
    return f"since episode opened {opened_day}"


def _flow_protos(
    symbol: str,
    *,
    window_day: str,
    opened_day: str,
    db_path: Path,
) -> list[dict[str, Any]]:
    """Options-flow aggregates for ``now``'s ET day + (when multi-day) since episode open.

    ``market_day`` on the episode is the *birth* session only (Round-3 schema); filtering
    on it alone blinds a multi-day episode to everything after open. Primary window =
    ``window_day``; since-open covers ``opened_day``..``window_day`` inclusive and is
    emitted as a separate labelled claim — never merged into the primary number.
    """
    options_flow_db.init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT ts, right, direction, premium_usd "
            "FROM options_flow_events WHERE underlying = ?",
            (symbol,),
        ).fetchall()

    windows: list[str] = ["primary"]
    if opened_day and window_day and opened_day != window_day:
        windows.append("since_open")

    protos: list[dict[str, Any]] = []
    for kind in windows:
        sums = {"bull": 0.0, "bear": 0.0}
        counts = {"bull": 0, "bear": 0}
        last_ts = {"bull": "", "bear": ""}
        largest: dict[str, Any] | None = None
        largest_prem = -1.0
        for row in rows:
            ts = str(row["ts"] or "")
            day = _et_date(ts)
            if kind == "primary":
                if day != window_day:
                    continue
            else:
                # Inclusive of window_day on purpose: since-open is birth..now as one
                # labelled view, overlapping the primary day. Do not make this
                # exclusive of today or sum primary+since_open into one number.
                if not day or day < opened_day or day > window_day:
                    continue
            prem = _as_float(row["premium_usd"])
            direction = options_direction(row["right"], row["direction"])
            if direction in sums:
                sums[direction] += prem
                counts[direction] += 1
                last_ts[direction] = max(last_ts[direction], ts)
            if prem > largest_prem:
                largest_prem, largest = prem, dict(row)

        label = _window_label(kind, window_day=window_day, opened_day=opened_day)
        for direction in ("bull", "bear"):
            if counts[direction]:
                side = "ask/bull" if direction == "bull" else "bid/bear"
                protos.append(
                    {
                        "statement": (
                            f"options {side} premium ${sums[direction]:,.0f} across "
                            f"{counts[direction]} print(s) {label}"
                        ),
                        "value": sums[direction],
                        "ts": last_ts[direction] or window_day,
                        "direction": direction,
                    }
                )
        if largest is not None and largest_prem > 0:
            ld = options_direction(largest["right"], largest["direction"])
            protos.append(
                {
                    "statement": (
                        f"largest options print ${largest_prem:,.0f} "
                        f"{str(largest['right'] or '?').upper()} {ld} {label}"
                    ),
                    "value": largest_prem,
                    "ts": str(largest["ts"] or ""),
                    "direction": ld,
                }
            )
    return protos


def _tape_protos(
    symbol: str,
    *,
    window_day: str,
    opened_day: str,
    db_path: Path,
) -> list[dict[str, Any]]:
    """Equity-tape aggregates for ``now``'s ET day + (when multi-day) since episode open."""
    equity_tape_db.init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT ts, event_type, direction, notional_usd "
            "FROM equity_tape_events WHERE symbol = ?",
            (symbol,),
        ).fetchall()

    windows: list[str] = ["primary"]
    if opened_day and window_day and opened_day != window_day:
        windows.append("since_open")

    protos: list[dict[str, Any]] = []
    for kind in windows:
        sums = {"bull": 0.0, "bear": 0.0}
        counts = {"bull": 0, "bear": 0}
        last_ts = {"bull": "", "bear": ""}
        largest: dict[str, Any] | None = None
        largest_notional = -1.0
        for row in rows:
            ts = str(row["ts"] or "")
            day = _et_date(ts)
            if kind == "primary":
                if day != window_day:
                    continue
            else:
                # Inclusive of window_day on purpose: since-open is birth..now as one
                # labelled view, overlapping the primary day. Do not make this
                # exclusive of today or sum primary+since_open into one number.
                if not day or day < opened_day or day > window_day:
                    continue
            notional = _as_float(row["notional_usd"])
            direction = tape_direction(row["direction"])
            if direction in sums:
                sums[direction] += notional
                counts[direction] += 1
                last_ts[direction] = max(last_ts[direction], ts)
            if notional > largest_notional:
                largest_notional, largest = notional, dict(row)

        label = _window_label(kind, window_day=window_day, opened_day=opened_day)
        for direction in ("bull", "bear"):
            if counts[direction]:
                protos.append(
                    {
                        "statement": (
                            f"equity {direction} tape notional ${sums[direction]:,.0f} "
                            f"across {counts[direction]} print(s) {label}"
                        ),
                        "value": sums[direction],
                        "ts": last_ts[direction] or window_day,
                        "direction": direction,
                    }
                )
        if largest is not None and largest_notional > 0:
            ld = tape_direction(largest["direction"])
            protos.append(
                {
                    "statement": (
                        f"largest tape print ${largest_notional:,.0f} "
                        f"{str(largest['event_type'] or 'block')} {ld} {label}"
                    ),
                    "value": largest_notional,
                    "ts": str(largest["ts"] or ""),
                    "direction": ld,
                }
            )
    return protos


def _active_alert_prices(symbol: str, db_path: Path) -> list[float]:
    """Active founder price-alert levels for ``symbol`` on ``db_path`` (store read).

    Reuses the ``list_price_alerts`` active predicate (``triggered = 0 OR
    repeat = 1``) but honors ``db_path`` — the public accessor pins DEFAULT_DB_PATH,
    which would read the wrong DB under a test tmp path (mirrors intake's helper)."""
    prices: list[float] = []
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT price FROM price_alerts "
                "WHERE symbol = ? AND (triggered = 0 OR repeat = 1)",
                (symbol,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    for row in rows:
        value = _as_float_or_none(row["price"])
        if value is not None:
            prices.append(value)
    return prices


def _structure_protos(
    episode: dict[str, Any], symbol: str, state_ts: str, db_path: Path
) -> list[dict[str, Any]]:
    """Nearest cached session level + active price alerts (structure context)."""
    init_chart_features(db_path)
    levels = fusion_db.get_session_levels(symbol, db_path=db_path)
    alerts = _active_alert_prices(symbol, db_path)
    ref = _as_float_or_none(episode.get("anchor_price"))

    protos: list[dict[str, Any]] = []
    if levels:
        priced = [
            (level, _as_float_or_none(level.get("price")))
            for level in levels
            if _as_float_or_none(level.get("price")) is not None
        ]
        if ref is not None and priced:
            nearest, price = min(priced, key=lambda item: abs(item[1] - ref))
            name = str(nearest.get("name") or "level")
            protos.append(
                {
                    "statement": (
                        f"nearest session level {name} @ {price:g} "
                        f"({abs(price - ref):.2f} from {ref:g})"
                    ),
                    "value": price,
                    "ts": state_ts,
                    "direction": "none",
                }
            )
        else:
            names = ", ".join(str(level.get("name") or "level") for level in levels)
            protos.append(
                {
                    "statement": f"{len(levels)} cached session level(s): {names}",
                    "value": len(levels),
                    "ts": state_ts,
                    "direction": "none",
                }
            )
    if alerts:
        if ref is not None:
            nearest_alert = min(alerts, key=lambda price: abs(price - ref))
            protos.append(
                {
                    "statement": (
                        f"{len(alerts)} active price alert(s); nearest @ {nearest_alert:g}"
                    ),
                    "value": nearest_alert,
                    "ts": state_ts,
                    "direction": "none",
                }
            )
        else:
            protos.append(
                {
                    "statement": f"{len(alerts)} active price alert(s)",
                    "value": len(alerts),
                    "ts": state_ts,
                    "direction": "none",
                }
            )
    return protos


def _attention_protos(
    symbol: str, state_ts: str, now: datetime, db_path: Path
) -> list[dict[str, Any]]:
    """Social buzz vs baseline — OMITTED entirely while the store reports warming."""
    buzz = get_buzz(symbol, db_path=db_path, now=now)
    if buzz.get("state") != "ready":
        return []  # thin history stays silent
    ratio = buzz.get("anomaly_ratio")
    if ratio is None or float(ratio) < _ATTENTION_MIN_RATIO:
        return []
    return [
        {
            "statement": (
                f"social buzz {float(ratio):.1f}x same-hour baseline "
                f"({int(buzz.get('today_total') or 0)} mentions today, "
                f"{buzz.get('day_part')})"
            ),
            "value": float(ratio),
            "ts": state_ts,
            "direction": "none",
        }
    ]


def _catalyst_protos(
    symbol: str, as_of_day: str, state_ts: str, db_path: Path
) -> list[dict[str, Any]]:
    """Earnings proximity in trading days as of ``now``'s ET date (not episode birth).

    Dates only — never the bmo/amc hour (directionally biased, per the earnings
    store's own warning). No parsing of 8-K timestamps here, so the UTC->ET / CIK
    traps don't apply: the calendar store already holds normalized ISO dates.
    The as-of day is named in the statement so an undated count cannot silently
    drift with the episode's birth date."""
    nxt = next_earnings_date(symbol, as_of_day, db_path=db_path)
    if nxt:
        td = _trading_days_between(as_of_day, nxt)
        if td >= 0:
            return [
                {
                    "statement": (
                        f"next earnings in {td} trading day(s) ({nxt}) "
                        f"[as of {as_of_day}]"
                    ),
                    "value": td,
                    "ts": state_ts,
                    "direction": "none",
                }
            ]
    last = last_earnings_date(symbol, as_of_day, db_path=db_path)
    if last:
        since = _trading_days_between(last, as_of_day)
        if since >= 0:
            return [
                {
                    "statement": (
                        f"most recent earnings {since} trading day(s) ago ({last}) "
                        f"[as of {as_of_day}]"
                    ),
                    "value": since,
                    "ts": state_ts,
                    "direction": "none",
                }
            ]
    return []


def _normalize_news_ts(published_at: str) -> str:
    """Canonical UTC ISO with microseconds — mixed store formats must not be
    compared or sorted as raw strings (`.` sorts before ``Z``)."""
    dt = _parse_ts(published_at)
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _format_headline(raw: str) -> str:
    """HTML-unescape + char-budget truncate; never paraphrase."""
    text = html.unescape(str(raw or "")).strip()
    if len(text) <= _NEWS_HEADLINE_MAX_CHARS:
        return text
    return text[: _NEWS_HEADLINE_MAX_CHARS - 1].rstrip() + "…"


def _news_tag_breadth(symbols_json: Any) -> int:
    try:
        symbols = json.loads(symbols_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0
    if not isinstance(symbols, list):
        return 0
    return len(symbols)


def _news_protos(
    symbol: str,
    *,
    window_day: str,
    opened_day: str,
    db_path: Path,
) -> list[dict[str, Any]]:
    """Symbol-tagged news: windowed counts + selected headlines + remainder count.

    Exact-symbol join via ``news_symbols`` (never headline LIKE). The WHOLE angle
    is scoped to the episode's windows (same contract as flow/tape): headlines,
    total, and remainder come from the since-open pool on multi-day episodes (or
    the primary day alone on a single-day episode) — never all-time store history.
    Counts are uncapped (full join scan + parse; never ``len(recent_news(...))``).
    Direction is always ``none``. Tag breadth rides each headline; each headline
    statement names its ET publish date (``_render_summary`` prints statements
    only).

    Headline slots (max 3, fewer if the window is thinner): slot A = minimum tag
    breadth in the window (tie-break freshest-first, then id) so wide roundups
    cannot crowd out the only company-tagged story; slots B/C = the 2 freshest
    remaining. No editorial "company-specific" label — breadth is already printed.
    Angle is OMITTED when nothing falls inside the episode window.
    """
    news_db.init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT n.id, n.headline, n.published_at, n.symbols
            FROM news_items n
            JOIN news_symbols ns ON ns.news_id = n.id
            WHERE ns.symbol = ?
            """,
            (symbol,),
        ).fetchall()

    # Parse once — never lexically compare published_at (mixed ms / no-ms formats).
    items: list[dict[str, Any]] = []
    for row in rows:
        published = str(row["published_at"] or "")
        dt = _parse_ts(published)
        if dt is None:
            continue
        items.append(
            {
                "id": str(row["id"]),
                "headline": str(row["headline"] or ""),
                "published_at": published,
                "ts": _normalize_news_ts(published),
                "day": dt.astimezone(_ET).date().isoformat(),
                "dt": dt,
                "breadth": _news_tag_breadth(row["symbols"]),
            }
        )

    items.sort(key=lambda item: (item["dt"], item["id"]), reverse=True)

    multi_day = bool(opened_day and window_day and opened_day != window_day)

    def _in_primary(item: dict[str, Any]) -> bool:
        return item["day"] == window_day

    def _in_since_open(item: dict[str, Any]) -> bool:
        day = item["day"]
        return bool(day) and day >= opened_day and day <= window_day

    # Headline/total/remainder pool = episode window only (never pre-open history).
    pool = (
        [item for item in items if _in_since_open(item)]
        if multi_day
        else [item for item in items if _in_primary(item)]
    )
    if not pool:
        return []

    protos: list[dict[str, Any]] = []

    windows: list[str] = ["primary"]
    if multi_day:
        windows.append("since_open")

    for kind in windows:
        matched = [
            item
            for item in items
            if (_in_primary(item) if kind == "primary" else _in_since_open(item))
        ]
        if not matched:
            continue
        label = _window_label(kind, window_day=window_day, opened_day=opened_day)
        freshest_ts = matched[0]["ts"]  # matched preserves freshest-first walk order
        protos.append(
            {
                "statement": f"{len(matched)} news item(s) {label}",
                "value": len(matched),
                "ts": freshest_ts or window_day,
                "direction": "none",
            }
        )

    shown = _select_news_headlines(pool, cap=_NEWS_HEADLINE_CAP)
    for item in shown:
        headline = _format_headline(item["headline"])
        breadth = item["breadth"]
        protos.append(
            {
                "statement": (
                    f'"{headline}" [{item["day"]}] · {breadth} symbol(s) tagged'
                ),
                "value": breadth,
                "ts": item["ts"],
                "direction": "none",
            }
        )

    total = len(pool)
    remainder = total - len(shown)
    if remainder > 0:
        shown_ids = {item["id"] for item in shown}
        not_shown = [item for item in pool if item["id"] not in shown_ids]
        rem_ts = not_shown[0]["ts"] if not_shown else shown[-1]["ts"]
        pool_label = _window_label(
            "since_open" if multi_day else "primary",
            window_day=window_day,
            opened_day=opened_day,
        )
        protos.append(
            {
                "statement": (
                    f"+{remainder} more tagged headline(s) "
                    f"({total} total) {pool_label}"
                ),
                "value": total,
                "ts": rem_ts,
                "direction": "none",
            }
        )

    return protos


def _select_news_headlines(
    pool: list[dict[str, Any]], *, cap: int = _NEWS_HEADLINE_CAP
) -> list[dict[str, Any]]:
    """Slot A = min tag breadth (freshest, then id); B/C = freshest remaining.

    Cap is a hard maximum; returns fewer when the window is thinner. No-op shape
    when every story shares the same breadth (slot A is just the freshest).
    """
    if not pool or cap <= 0:
        return []
    # Min breadth; among ties, freshest-first then id (matches pool's total order).
    min_breadth = min(item["breadth"] for item in pool)
    narrow = [item for item in pool if item["breadth"] == min_breadth]
    slot_a = max(narrow, key=lambda item: (item["dt"], item["id"]))
    remaining = [
        item
        for item in sorted(pool, key=lambda item: (item["dt"], item["id"]), reverse=True)
        if item["id"] != slot_a["id"]
    ]
    return [slot_a, *remaining[: max(0, cap - 1)]]


def _et_noon_iso(day: str) -> str:
    """ISO ts at 12:00 America/New_York on ``day``.

    Macro observation dates are calendar days, not UTC midnights — feeding a bare
    ``YYYY-MM-DD`` through ``_parse_ts`` yields UTC midnight whose ET date is the
    *previous* day, so ``_is_fresh`` would mark a same-session reading stale.
    """
    d = date.fromisoformat(str(day)[:10])
    return datetime(d.year, d.month, d.day, 12, 0, tzinfo=_ET).isoformat()


def _macro_obs_fresh(date_used: str, as_of_day: str) -> bool:
    """Publication-lag freshness for a macro observation (not same-ET-day)."""
    try:
        gap = (date.fromisoformat(as_of_day[:10]) - date.fromisoformat(date_used[:10])).days
    except ValueError:
        return False
    return 0 <= gap <= MACRO_FRESH_MAX_GAP_DAYS


def _macro_protos(
    as_of_day: str,
    *,
    macro_db_path: Path,
) -> list[dict[str, Any]]:
    """Macro substrate axes from ``macro.db`` via ``regime_report`` (store-only).

    Supersedes the old ``regime_macro`` VIX + hand-computed term-spread line — same
    two axes, deeper history, with percentile + bucket + reference depth. Missing or
    stale axes are stated, never dropped. ``direction`` is always ``none`` (riders).
    Absent / empty ``macro.db`` → no claims (do not raise; worktrees often lack data/).
    Claim ``ts`` is ET-noon on ``date_used`` (honest observation time). ``fresh`` is
    set explicitly against ``MACRO_FRESH_MAX_GAP_DAYS`` — not via ``_is_fresh`` —
    because daily FRED prints lag the session calendar. Missing → ``fresh=False``.
    """
    if not as_of_day or not macro_db_path.is_file():
        return []
    try:
        report = regime_report([as_of_day], db_path=macro_db_path)
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return []

    axes = report.get("axes") or {}
    # Empty store (schema only, no history) ≈ absent — do not spam four "missing" lines.
    if not any(int((block.get("history") or {}).get("n") or 0) for block in axes.values()):
        return []

    protos: list[dict[str, Any]] = []
    for axis in REPORT_AXES:  # fixed order: vol, credit, curve, dollar
        block = axes.get(axis) or {}
        series = str(block.get("series") or REPORT_AXES[axis])
        hist_n = int((block.get("history") or {}).get("n") or 0)
        obs = (block.get("per_date") or {}).get(as_of_day) or {}
        value = obs.get("value")
        date_used = obs.get("date_used")
        if value is None or date_used is None:
            protos.append(
                {
                    "statement": (
                        f"macro {axis} {series}: missing "
                        f"(n={hist_n}) [as of {as_of_day}]"
                    ),
                    "value": None,
                    "ts": "",
                    "direction": "none",
                    "fresh": False,
                }
            )
            continue
        pctile = obs.get("pctile")
        bucket = obs.get("bucket")
        # date_used (observation date), never window_day — FRED lags (D1 lesson).
        protos.append(
            {
                "statement": (
                    f"macro {axis} {series}: {float(value):g} · "
                    f"pctile {pctile} ({bucket}, n={hist_n}) "
                    f"[as of {date_used}]"
                ),
                "value": float(value),
                "ts": _et_noon_iso(str(date_used)),
                "direction": "none",
                "fresh": _macro_obs_fresh(str(date_used), as_of_day),
            }
        )
    return protos


def _ro_connect(path: Path) -> sqlite3.Connection:
    """Read-only sqlite handle. ``mode=ro`` neither creates the file nor writes to
    it — ``edgar_facts.db`` is a 16 GB shared corpus and this module is a reader."""
    conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _symbol_cik(symbol: str, fundamentals_db_path: Path) -> int | None:
    """Symbol -> CIK from the curated ``companies`` table — the ONE mapping in the
    repo (``research/fundamentals.py`` builds it). Queried directly rather than via
    that module's ``_company_row``, which runs ``_ensure_schema`` first and would
    CREATE the file on a read path (D4's stub-DB finding)."""
    if not symbol or not fundamentals_db_path.is_file():
        return None
    try:
        with closing(_ro_connect(fundamentals_db_path)) as conn:
            row = conn.execute(
                "SELECT cik FROM companies WHERE symbol = ?", (symbol,)
            ).fetchone()
    except sqlite3.Error:
        return None
    try:
        return int(row["cik"]) if row is not None else None
    except (TypeError, ValueError):
        return None


def _edgar_ingest_watermark(cik: int) -> dict[str, Any] | None:
    """When did we last ask SEC about this filer? — Evidence Bus §3 contract.

    The writer + reader land on a parallel branch; here the import simply fails and
    ``None`` (never checked) is the correct, honest answer. Called with the callee's
    own default store path so it keeps working wherever that reader lands. Tests
    fake this function.
    """
    try:
        from research.edgar_bulk import edgar_ingest_watermark
    except ImportError:
        return None
    try:
        return edgar_ingest_watermark(cik)
    except (OSError, sqlite3.Error, TypeError, ValueError, KeyError):
        return None


def _edgar_corpus_asof(
    watermark: dict[str, Any] | None, corpus_latest_filed: Any, as_of_day: str
) -> tuple[bool, str]:
    """``(fresh, suffix)`` for the CORPUS — never for the filing's age.

    ``fresh`` asserts "this IS the company's latest filed period", which from
    inside the store is unknowable: "no newer filing exists" and "we never looked"
    read identically. So it is True only when the ingest watermark says we checked
    within ``EDGAR_CHECK_MAX_AGE_DAYS`` AND the corpus is not behind what that
    check saw. A missing watermark is never-checked, and the suffix says exactly
    that rather than implying no newer filing exists.
    """
    unknown = "[SEC recency unknown — corpus never checked]"
    checked_at = _parse_ts((watermark or {}).get("checked_at"))
    if checked_at is None:
        return False, unknown
    checked_day = checked_at.astimezone(_ET).date().isoformat()
    try:
        gap = (
            date.fromisoformat(as_of_day[:10]) - date.fromisoformat(checked_day)
        ).days
    except ValueError:
        return False, unknown
    if not 0 <= gap <= EDGAR_CHECK_MAX_AGE_DAYS:
        return False, f"[SEC not checked since {checked_day}]"
    # The check saw filings we have not ingested — the newest period we HOLD is
    # then not the newest period that EXISTS, whatever the check's own recency.
    seen = str((watermark or {}).get("latest_filed") or "")
    held = str(corpus_latest_filed or "")
    if seen and held and seen > held:
        return False, (
            f"[SEC checked {checked_day}; corpus behind SEC "
            f"(filings through {seen} not ingested)]"
        )
    return True, f"[SEC checked {checked_day}]"


def _period_span_days(row: Any) -> int:
    """Duration of a fact's period in days; ``0`` for an instant (no period_start)."""
    start = str(row["period_start"] or "")
    if not start:
        return 0
    try:
        return (
            date.fromisoformat(str(row["period_end"])[:10])
            - date.fromisoformat(start[:10])
        ).days
    except ValueError:
        return 0


def _pick_reported_fact(
    rows: list[Any],
    concept_ids: dict[int, str],
    tags: tuple[str, ...],
    period_end: str,
    expected_days: int,
) -> tuple[str, Any] | None:
    """First tag (in the filer-preference order) that reports ``period_end``.

    A filing repeats the same concept ending on the same date for several spans —
    a Q3 10-Q carries both 3-month and 9-month revenue — so the span closest to the
    filing's own reporting period wins. Instants (balance-sheet dates) have no
    competing spans, so the same rule passes them through untouched. USD before a
    second reporting currency; the rest of the key is only for determinism.
    """
    for tag in tags:
        candidates = [
            row
            for row in rows
            if concept_ids.get(int(row["concept_id"])) == tag
            and str(row["period_end"]) == period_end
        ]
        if not candidates:
            continue
        candidates.sort(
            key=lambda row: (
                abs(_period_span_days(row) - expected_days),
                0 if str(row["unit"]) == "USD" else 1,
                str(row["period_start"]),
                _as_float(row["val"]),
            )
        )
        return tag, candidates[0]
    return None


def _prior_year_fact(
    rows: list[Any], concept_ids: dict[int, str], tag: str, current: Any
) -> Any | None:
    """The prior-year comparative the company reported in the SAME filing.

    Same tag + unit, same span, ending about a year earlier. Instants are excluded:
    a 10-Q's balance-sheet comparative is the prior FISCAL-YEAR END, not the prior
    year's same date, and calling that "YoY" would be a mislabel. Returns ``None``
    when the comparison period is absent — the claim then states the level alone.
    """
    span = _period_span_days(current)
    if span <= 0:
        return None
    try:
        end = date.fromisoformat(str(current["period_end"])[:10])
    except ValueError:
        return None
    candidates: list[tuple[int, Any]] = []
    for row in rows:
        if concept_ids.get(int(row["concept_id"])) != tag:
            continue
        if str(row["unit"]) != str(current["unit"]):
            continue
        if abs(_period_span_days(row) - span) > _YOY_SPAN_TOLERANCE_DAYS:
            continue
        try:
            gap = (end - date.fromisoformat(str(row["period_end"])[:10])).days
        except ValueError:
            continue
        if _YOY_MIN_GAP_DAYS <= gap <= _YOY_MAX_GAP_DAYS:
            candidates.append((gap, row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (abs(item[0] - 365), str(item[1]["period_start"])))
    return candidates[0][1]


def _fiscal_period_label(fy: Any, fp: Any) -> str:
    """``Q1 FY2027`` / ``FY2026`` / ``period`` — from the filing's own fy/fp."""
    try:
        year = f"FY{int(fy)}"
    except (TypeError, ValueError):
        return "period"
    period = str(fp or "").upper()
    if period == "FY":
        return year
    if period.startswith("Q") and period[1:].isdigit():
        return f"{period} {year}"
    return year


def _fundamentals_protos(
    symbol: str,
    as_of_day: str,
    *,
    facts_db_path: Path,
    fundamentals_db_path: Path,
) -> list[dict[str, Any]]:
    """As-reported financials from the company's latest periodic filing (EDGAR).

    Store-only, read-only, deterministic: no network, no LLM, and both DBs are
    opened ``mode=ro`` so a worktree without ``data/`` degrades to zero claims
    rather than writing stub files. ``direction`` is always ``none`` — reported
    financials are context, never a directional call.

    Freshness is DECLARED (never ``_is_fresh``): a 90-day-old 10-Q is the current
    reported state, so what stales the claim is our own ingest recency, not the
    filing's age. See ``_edgar_corpus_asof`` / ``EDGAR_CHECK_MAX_AGE_DAYS``. Every
    claim names both its fiscal period and the corpus as-of, so a reader can tell
    "Q1 FY2027, checked on the 2nd" from "Q1 FY2027, and we never looked".
    """
    cik = _symbol_cik(symbol, fundamentals_db_path)
    if cik is None or not facts_db_path.is_file():
        return []

    tag_to_metric = {
        tag: metric
        for metric in _FUNDAMENTALS_METRICS
        for tag in FINANCIAL_CONCEPTS[metric]
    }
    try:
        with closing(_ro_connect(facts_db_path)) as conn:
            # Resolve tags to concept ids first: concept_id is the second column
            # of the facts primary key, so (cik, concept_id) seeks. Filtering on
            # the tag name instead would scan every fact the filer ever reported.
            concept_ids = {
                int(row["id"]): str(row["tag"])
                for row in conn.execute(
                    "SELECT id, tag FROM concepts WHERE taxonomy = 'us-gaap' "
                    f"AND tag IN ({','.join('?' * len(tag_to_metric))})",
                    tuple(tag_to_metric),
                )
            }
            if not concept_ids:
                return []
            id_q = ",".join("?" * len(concept_ids))
            form_q = ",".join("?" * len(_PERIODIC_FORMS))
            filing = conn.execute(
                f"SELECT filed, accn, fy, fp, form FROM facts "
                f"WHERE cik = ? AND concept_id IN ({id_q}) AND form IN ({form_q}) "
                f"ORDER BY filed DESC, accn DESC LIMIT 1",
                (cik, *concept_ids, *_PERIODIC_FORMS),
            ).fetchone()
            if filing is None:
                return []
            rows = conn.execute(
                f"SELECT concept_id, period_start, period_end, unit, val "
                f"FROM facts WHERE cik = ? AND concept_id IN ({id_q}) "
                f"AND filed = ? AND accn = ?",
                (cik, *concept_ids, filing["filed"], filing["accn"]),
            ).fetchall()
            corpus_latest_filed = conn.execute(
                "SELECT max(filed) FROM facts WHERE cik = ?", (cik,)
            ).fetchone()[0]
    except sqlite3.Error:
        return []
    if not rows:
        return []

    # The fiscal period end, taken over the REPORTED FINANCIAL rows only. NOT
    # max(period_end) over the whole filing: a 10-Q also carries cover-page
    # instants dated near the filing date (measured — NVDA's Q1 FY2027 10-Q filed
    # 2026-05-20 has facts at 2026-05-15 as well as the real quarter end
    # 2026-04-26), and taking those would silently mislabel the period.
    period_end = max(str(row["period_end"]) for row in rows)
    expected_days = (
        _ANNUAL_PERIOD_DAYS
        if str(filing["form"] or "") in _ANNUAL_FORMS
        or str(filing["fp"] or "").upper() == "FY"
        else _QUARTER_PERIOD_DAYS
    )
    fresh, as_of_note = _edgar_corpus_asof(
        _edgar_ingest_watermark(cik), corpus_latest_filed, as_of_day
    )
    label = _fiscal_period_label(filing["fy"], filing["fp"])
    filed = str(filing["filed"] or "")

    protos: list[dict[str, Any]] = []
    for metric in _FUNDAMENTALS_METRICS:
        picked = _pick_reported_fact(
            rows, concept_ids, FINANCIAL_CONCEPTS[metric], period_end, expected_days
        )
        if picked is None:
            continue  # a concept this filer does not tag (banks skip Revenues) —
            # silence, never a substitute figure computed by us
        tag, row = picked
        value = _as_float(row["val"])
        unit = str(row["unit"] or "")
        amount = f"${value:,.0f}" if unit == "USD" else f"{value:,.0f} {unit}".strip()
        statement = (
            f"fundamentals {metric.replace('_', ' ')} {amount} · "
            f"{label} ended {period_end} ({filing['form']} filed {filed})"
        )
        prior = _prior_year_fact(rows, concept_ids, tag, row)
        prior_value = _as_float(prior["val"]) if prior is not None else 0.0
        if prior is not None and prior_value:
            pct = (value - prior_value) / abs(prior_value) * 100.0
            statement += f" · YoY {pct:+.1f}% (derived vs {prior['period_end']})"
        protos.append(
            {
                "statement": f"{statement} {as_of_note}",
                "value": value,
                # Honest observation time: when the company filed it (ET noon, so a
                # bare date cannot parse to UTC midnight = the previous ET day).
                "ts": _et_noon_iso(filed) if filed else "",
                "direction": "none",
                "fresh": fresh,
            }
        )
    return protos


def _context_protos(
    episode: dict[str, Any],
    symbol: str,
    as_of_day: str,
    state_ts: str,
    db_path: Path,
    *,
    macro_db_path: Path = DEFAULT_MACRO_DB_PATH,
) -> list[dict[str, Any]]:
    """Macro substrate + index-pressure + market-shock (riders — never directional).

    Macro rides ``signals.macro_substrate.regime_report`` against ``macro.db`` (not
    the thin ``regime_macro`` table in trading.db). Reads are as-of ``now``'s ET
    date; each macro claim names its observation ``date_used``. ``market_day`` is
    opening-session-only under the Round-3 episode model."""
    protos: list[dict[str, Any]] = []
    protos.extend(_macro_protos(as_of_day, macro_db_path=macro_db_path))

    if as_of_day:
        try:
            pressure = day_summary(as_of_day, db_path=db_path)
        except sqlite3.OperationalError:
            pressure = []
        for entry in pressure:
            if str(entry.get("underlying") or "").upper() == symbol:
                net = _as_float(entry.get("net_usd"))
                lean = "bull-lean" if net > 0 else ("bear-lean" if net < 0 else "flat")
                protos.append(
                    {
                        "statement": (
                            f"index-pressure {symbol}: net ${net:,.0f} ({lean}) "
                            f"[{as_of_day}]"
                        ),
                        "value": net,
                        "ts": state_ts,
                        "direction": "none",
                    }
                )
                break

    if int(episode.get("market_shock") or 0):
        protos.append(
            {
                "statement": "market-shock window active (broad SPY/VIX move stamped on this episode)",
                "value": 1,
                "ts": state_ts,
                "direction": "none",
            }
        )
    return protos


def _portfolio_protos(
    symbol: str, state_ts: str, db_path: Path
) -> list[dict[str, Any]]:
    """Held position: yes/no + direction/size bucket — NEVER dollar amounts.

    Managed positions are execution long-only, so held == long. Kept out of the
    contradiction graph (direction ``none``): it's exposure context, not a
    market-read signal — v1 scoping."""
    held = [
        position
        for position in list_open_positions(db_path=db_path)
        if str(position.get("symbol") or "").upper() == symbol
    ]
    if not held:
        return []
    shares = sum(_as_float(position.get("qty")) for position in held)
    bucket = _size_bucket(shares)
    return [
        {
            "statement": f"holding {symbol}: long, {bucket} size",
            "value": "long",
            "ts": state_ts,
            "direction": "none",
        }
    ]


def _history_protos(
    symbol: str, state_ts: str, now: datetime, db_path: Path
) -> list[dict[str, Any]]:
    """The symbol's own GRADED idea history — mechanical facts, no win% language."""
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT status, resolved_at FROM idea_outcomes "
                "WHERE symbol = ? AND status IN ('won', 'lost', 'expired') "
                "AND synthetic = 0 ORDER BY resolved_at DESC",
                (symbol,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    if not rows:
        return []
    won = sum(1 for row in rows if row["status"] == "won")
    lost = sum(1 for row in rows if row["status"] == "lost")
    expired = sum(1 for row in rows if row["status"] == "expired")
    total = len(rows)
    recent = rows[0]
    days = _calendar_days_ago(recent["resolved_at"], now)
    recency = f"{days} day(s) ago" if days is not None else "recently"
    statement = (
        f"{total} graded idea(s) for {symbol}: {won} won, {lost} lost, "
        f"{expired} expired; most recent {recent['status']} {recency}"
    )
    return [
        {
            "statement": statement,
            "value": total,
            "ts": state_ts,
            "direction": "none",
        }
    ]


def _calendar_days_ago(resolved_at: Any, now: datetime) -> int | None:
    dt = _parse_ts(resolved_at)
    if dt is None:
        return None
    delta = now.astimezone(_ET).date() - dt.astimezone(_ET).date()
    return max(0, delta.days)


# ------------------------------------------------------------------------- assembly


def _link_contradictions(claims: list[dict[str, Any]]) -> None:
    """v1 contradictions (deterministic, minimal): opposing DIRECTIONS between
    directional claims ACROSS angles get reciprocal ``contradicts`` links. Only
    evidence/flow/tape ever carry bull|bear, so this naturally links exactly the
    market-read angles (e.g. flow bull vs tape bear). Nothing fancier — v1."""
    for i, left in enumerate(claims):
        for right in claims[i + 1 :]:
            if left["angle"] == right["angle"]:
                continue
            if {left["direction"], right["direction"]} == _DIRECTIONAL:
                left["contradicts"].append(right["claim_id"])
                right["contradicts"].append(left["claim_id"])
    for claim in claims:
        claim["contradicts"] = sorted(set(claim["contradicts"]))


def _render_summary(claims: list[dict[str, Any]]) -> str:
    """Deterministic plain-text matrix, one line per claim, grouped by angle order,
    hard-capped at ``DOSSIER_SUMMARY_MAX_CHARS``. Truncation drops WHOLE trailing
    claims (never mid-line) and appends the "(+N claims truncated)" marker."""
    if not claims:
        return ""
    lines: list[str] = []
    for claim in claims:
        line = f"[{claim['angle']}] {claim['statement']}"
        if not claim["fresh"]:
            line += " [stale]"
        if claim["contradicts"]:
            line += f" [contradicts {', '.join(claim['contradicts'])}]"
        lines.append(line)

    full = "\n".join(lines)
    if len(full) <= DOSSIER_SUMMARY_MAX_CHARS:
        return full

    # Reserve worst-case marker room so appending it can never breach the cap.
    max_marker = len(f"\n… (+{len(lines)} claims truncated)")
    kept: list[str] = []
    for line in lines:
        candidate = "\n".join(kept + [line])
        if len(candidate) + max_marker <= DOSSIER_SUMMARY_MAX_CHARS:
            kept.append(line)
        else:
            break
    dropped = len(lines) - len(kept)
    if not kept:
        return f"… (+{dropped} claims truncated)"
    text = "\n".join(kept)
    if dropped:
        text += f"\n… (+{dropped} claims truncated)"
    return text


def build_dossier(
    episode: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
    macro_db_path: Path = DEFAULT_MACRO_DB_PATH,
    facts_db_path: Path = DEFAULT_FACTS_DB_PATH,
    fundamentals_db_path: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the deterministic claim matrix for an admitted Fusion episode.

    Returns ``{episode_id, symbol, market_day, claims: [...], summary: str}``. Each
    claim is ``{claim_id, angle, statement, value, ts, direction, fresh,
    contradicts}``. Pure: same episode state + same ``now`` in -> identical out.
    ``now`` defaults to the wall clock; it selects the primary ET window for
    flow/tape/news/catalyst/context (and the since-open upper bound), so it
    affects claim values and ordering as well as freshness.
    ``macro_db_path`` is the separate macro substrate store (not trading.db);
    ``facts_db_path`` / ``fundamentals_db_path`` are the read-only EDGAR corpus and
    the curated symbol->CIK cache (both outside trading.db, both may be absent).
    """
    now = now or datetime.now(timezone.utc)
    episode_id = str(episode["id"])
    symbol = str(episode.get("symbol") or "").strip().upper()
    market_day = str(episode.get("market_day") or "")  # episode birth / opening session
    window_day = now.astimezone(_ET).date().isoformat()
    state_ts = now.isoformat()  # state-angle reads are as-of build time (deterministic)

    by_angle: dict[str, list[dict[str, Any]]] = {
        "evidence": _evidence_protos(episode_id, db_path),
        "flow": _flow_protos(
            symbol, window_day=window_day, opened_day=market_day, db_path=db_path
        ),
        "tape": _tape_protos(
            symbol, window_day=window_day, opened_day=market_day, db_path=db_path
        ),
        "structure": _structure_protos(episode, symbol, state_ts, db_path),
        "attention": _attention_protos(symbol, state_ts, now, db_path),
        "catalyst": _catalyst_protos(symbol, window_day, state_ts, db_path),
        "news": _news_protos(
            symbol, window_day=window_day, opened_day=market_day, db_path=db_path
        ),
        "fundamentals": _fundamentals_protos(
            symbol,
            window_day,
            facts_db_path=facts_db_path,
            fundamentals_db_path=fundamentals_db_path,
        ),
        "context": _context_protos(
            episode,
            symbol,
            window_day,
            state_ts,
            db_path,
            macro_db_path=macro_db_path,
        ),
        "portfolio": _portfolio_protos(symbol, state_ts, db_path),
        "history": _history_protos(symbol, state_ts, now, db_path),
    }

    claims: list[dict[str, Any]] = []
    for angle in ANGLE_ORDER:
        protos = by_angle.get(angle) or []
        # Freshest-first (ts desc), then value, then statement — so a summary-cap
        # overflow keeps recent claims, not the oldest. Deterministic under same now.
        ordered = sorted(
            protos,
            key=lambda proto: (
                str(proto["ts"]),
                _value_sort(proto["value"]),
                str(proto["statement"]),
            ),
            reverse=True,
        )
        for index, proto in enumerate(ordered):
            claims.append(
                {
                    "claim_id": f"{angle}:{index}",
                    "angle": angle,
                    "statement": proto["statement"],
                    "value": proto["value"],
                    "ts": proto["ts"],
                    "direction": proto.get("direction", "none"),
                    # Proto may declare freshness (macro publication lag); else angle rule.
                    "fresh": (
                        proto["fresh"]
                        if "fresh" in proto
                        else _is_fresh(angle, proto["ts"], now)
                    ),
                    "contradicts": [],
                }
            )

    _link_contradictions(claims)
    return {
        "episode_id": episode_id,
        "symbol": symbol,
        "market_day": market_day,
        "claims": claims,
        "summary": _render_summary(claims),
    }


def build_and_store_dossier(
    episode: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
    macro_db_path: Path = DEFAULT_MACRO_DB_PATH,
    facts_db_path: Path = DEFAULT_FACTS_DB_PATH,
    fundamentals_db_path: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the dossier and persist it (upsert on episode_id) via db/idea_dossiers."""
    dossier = build_dossier(
        episode,
        db_path=db_path,
        macro_db_path=macro_db_path,
        facts_db_path=facts_db_path,
        fundamentals_db_path=fundamentals_db_path,
        now=now,
    )
    put_dossier(dossier, db_path=db_path)
    return dossier
