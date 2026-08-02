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

This module is INERT: nothing in production imports it yet (C3's investigator becomes
its caller). It never writes to any store except via ``build_and_store_dossier`` →
``db/idea_dossiers``.
"""

from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime, timezone
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
from db.managed_positions import list_open_positions
from db.regime import latest_observation
from db.social_mentions import get_buzz
from signals.fusion.scorer import options_direction, tape_direction
from trading.market_calendar import is_trading_day

_ET = ZoneInfo("America/New_York")

# Fixed angle order — governs how angles appear in the matrix + summary, and (with
# claim_id embedding the angle) makes the id scheme stable. Changing this order is a
# NEW estimand for the investigator, never a silent edit. ``evidence`` is LAST so the
# 4,000-char summary cap truncates the (still-redundant) evidence tail, not analysis.
# ``news`` sits next to ``catalyst`` (same event/catalyst family) — intentional NEW
# estimand (Evidence Bus §2.9 first post-v1 component, alone).
ANGLE_ORDER: tuple[str, ...] = (
    "flow",
    "tape",
    "structure",
    "attention",
    "catalyst",
    "news",
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

# Social buzz below this ratio is not worth an attention claim (matches the intake's
# spike gate spirit — a boring reading is silence, not a dot).
_ATTENTION_MIN_RATIO = 1.0

# Headline claims: max 3 — slot A = min tag breadth in the window, B/C = freshest
# remaining (founder 2026-08-02: pure freshest-first favoured wide Benzinga roundups).
# Remainder counts the rest so the total is never silently deleted.
_NEWS_HEADLINE_CAP = 3
_NEWS_HEADLINE_MAX_CHARS = 140

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


def _context_protos(
    episode: dict[str, Any],
    symbol: str,
    as_of_day: str,
    state_ts: str,
    db_path: Path,
) -> list[dict[str, Any]]:
    """Regime tag + index-pressure state + market-shock flag (riders — never directional).

    Reads are as-of ``now``'s ET date (named in each statement), not the episode birth
    day — ``market_day`` is opening-session-only under the Round-3 episode model."""
    protos: list[dict[str, Any]] = []
    as_of = as_of_day or None
    vix = latest_observation("VIXCLS", as_of=as_of, db_path=db_path)
    if vix is not None:
        vix_val = vix[1]
        vol_tag = "elevated-vol" if vix_val >= 20 else "calm-vol"
        statement = f"regime: VIX {vix_val:.1f} ({vol_tag})"
        dgs10 = latest_observation("DGS10", as_of=as_of, db_path=db_path)
        dgs2 = latest_observation("DGS2", as_of=as_of, db_path=db_path)
        if dgs10 is not None and dgs2 is not None:
            term = dgs10[1] - dgs2[1]
            curve = "inverted" if term < 0 else "normal"
            statement += f", term spread {term:+.2f} ({curve} curve)"
        if as_of_day:
            statement += f" [as of {as_of_day}]"
        protos.append(
            {
                "statement": statement,
                "value": vix_val,
                "ts": state_ts,
                "direction": "none",
            }
        )

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
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the deterministic claim matrix for an admitted Fusion episode.

    Returns ``{episode_id, symbol, market_day, claims: [...], summary: str}``. Each
    claim is ``{claim_id, angle, statement, value, ts, direction, fresh,
    contradicts}``. Pure: same episode state + same ``now`` in -> identical out.
    ``now`` defaults to the wall clock; it selects the primary ET window for
    flow/tape/news/catalyst/context (and the since-open upper bound), so it
    affects claim values and ordering as well as freshness.
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
        "context": _context_protos(
            episode, symbol, window_day, state_ts, db_path
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
                    "fresh": _is_fresh(angle, proto["ts"], now),
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
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the dossier and persist it (upsert on episode_id) via db/idea_dossiers."""
    dossier = build_dossier(episode, db_path=db_path, now=now)
    put_dossier(dossier, db_path=db_path)
    return dossier
