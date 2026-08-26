"""Attention join — both detectors' angles fused into one per-(symbol, session)
ledger row (ATTENTION_JOIN_DESIGN_2026-08-17.md §4.2).

Angle 1 (range): today's first abnormality fire — already the registered study
object (p90 within cell, pool-fenced, half-hour grid) by the time it lands in
`abnormality_events`. Angle 2 (flow): rare-sized sweep/block prints
(`rarity_p <= 0.05` on the largest-print basis), equity roots only (the design's
ETF fence), deduped by cell-day. Rank = angle count, then the max within-angle
percentile — never a composite score. Timestamps normalize to UTC (abnormality
stamps ET-offset, flow stamps Z).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

RARE_P = 0.05
SHORT_DATED_DTE = 30  # <=30 DTE — the corpus's thrice-replicated positive tenor

_ANGLE_NAMES = {
    (True, False): "unusual range",
    (False, True): "rare options flow",
    (True, True): "unusual range + rare options flow",
}

CAVEAT = (
    "Attention only — angles are booleans, never a score, and none is a "
    "forecast. Range angle: trailing-60-min range in the top decile of this "
    "name's (time-of-day x $-vol) peer cell; in-sample fired names' next-2h max "
    "move ran ~1.3x their own norm vs ~0.94x for non-fired peers, direction a "
    "coin flip. Flow angle: a largest single option print rarer than 95% of "
    "this name's own session-peak prints in its right/tenor/moneyness bucket "
    "(single-leg reference — how quiet the bucket usually is, not a signal). "
    "No win%, no direction implied."
)


def _to_utc_iso(ts: str) -> str | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    except (ValueError, AttributeError):
        return None


def _session_utc_bounds(session: str) -> tuple[str, str]:
    """The ET calendar day as UTC ISO prefixes (lexical compare against Z-stamped ts)."""
    start = datetime.fromisoformat(session).replace(tzinfo=_ET)
    fmt = "%Y-%m-%dT%H:%M:%S"
    return (
        start.astimezone(UTC).strftime(fmt),
        (start + timedelta(days=1)).astimezone(UTC).strftime(fmt),
    )


def _flow_rare_events(session: str, db_path: Path | None) -> list[dict[str, Any]]:
    from db.base import DEFAULT_DB_PATH
    from db.base import connect as _connect
    from db.options_flow import _classify_events

    since, until = _session_utc_bounds(session)
    resolved = db_path if db_path is not None else DEFAULT_DB_PATH
    with _connect(resolved) as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT ts, underlying, "right", dte, premium_usd, max_print_usd,
                       rarity_p, rarity_cell
                FROM options_flow_events
                WHERE event_type IN ('sweep', 'block') AND rarity_p IS NOT NULL
                  AND rarity_p <= ? AND ts >= ? AND ts < ?
                ORDER BY ts
                """,
                (RARE_P, since, until),
            ).fetchall()
        ]
    # The design's ETF fence: index/ETF roots are hedging plumbing, not a
    # name-attention fact. Fence failure leaves rows unclassified -> excluded
    # (deny-by-default), logged inside _classify_events.
    _classify_events(rows)
    return [r for r in rows if r.get("underlying_class") == "equity"]


def build_attention_rows(
    session: str | None = None, *, db_path: Path | None = None
) -> list[dict[str, Any]]:
    """One ledger row per name lit today by either angle."""
    from db.abnormality import events_today, get_state_many

    if session is None:
        session = datetime.now(_ET).date().isoformat()

    # Angle 1 — first fire per name (repeats counted, never re-lit).
    abn_first: dict[str, dict[str, Any]] = {}
    abn_repeats: dict[str, int] = {}
    for ev in events_today(session, db_path=db_path):
        sym = str(ev["symbol"])
        if ev.get("repeat"):
            abn_repeats[sym] = abn_repeats.get(sym, 0) + 1
        elif sym not in abn_first:  # events_today orders by ts
            abn_first[sym] = ev

    # Angle 2 — per-name aggregation of the rare prints.
    flow: dict[str, dict[str, Any]] = {}
    for ev in _flow_rare_events(session, db_path):
        sym = str(ev["underlying"])
        agg = flow.setdefault(
            sym,
            {
                "first_ts": ev["ts"], "cells": set(), "min_p": 1.0, "max_print": 0.0,
                "call_prem": 0.0, "put_prem": 0.0, "short_dated": 0, "n": 0,
            },
        )
        agg["cells"].add(ev.get("rarity_cell"))
        agg["min_p"] = min(agg["min_p"], float(ev["rarity_p"]))
        prem = float(ev.get("premium_usd") or 0.0)
        # Pre-restart rows lack max_print_usd; the order sum is an upper bound.
        agg["max_print"] = max(agg["max_print"], float(ev.get("max_print_usd") or prem))
        if ev.get("right") == "call":
            agg["call_prem"] += prem
        else:
            agg["put_prem"] += prem
        if ev.get("dte") is not None and int(ev["dte"]) <= SHORT_DATED_DTE:
            agg["short_dated"] += 1
        agg["n"] += 1

    symbols = sorted(set(abn_first) | set(flow))
    if not symbols:
        return []

    # Quintile: the fire row's own, else the live state row's (slow-moving
    # $-vol property; a prior session's assignment is fine), else unknown.
    state = get_state_many([s for s in symbols if s not in abn_first], db_path=db_path)

    rows: list[dict[str, Any]] = []
    for sym in symbols:
        a = abn_first.get(sym)
        f = flow.get(sym)
        abn_ts = _to_utc_iso(str(a["ts"])) if a else None
        flow_ts = _to_utc_iso(str(f["first_ts"])) if f else None
        pctiles = []
        if a and a.get("range_pctile") is not None:
            pctiles.append(float(a["range_pctile"]))
        if f:
            pctiles.append((1.0 - f["min_p"]) * 100.0)
        quintile = a.get("quintile") if a else (state.get(sym) or {}).get("quintile")
        rows.append(
            {
                "symbol": sym,
                "session": session,
                "quintile": quintile,
                "abn_lit": int(a is not None),
                "abn_first_ts": abn_ts,
                "abn_bucket": a.get("bucket") if a else None,
                "abn_range_ratio": a.get("range_ratio") if a else None,
                "abn_range_pctile": a.get("range_pctile") if a else None,
                "abn_vol_ratio": a.get("vol_ratio") if a else None,
                "abn_vol_pctile": a.get("vol_pctile") if a else None,
                "abn_rvol_session": a.get("rvol_session") if a else None,
                "abn_repeats": abn_repeats.get(sym, 0),
                "base_state": (a.get("base_state") if a else None) or "none",
                "flow_lit": int(f is not None),
                "flow_first_ts": flow_ts,
                "flow_cell_days": len(f["cells"]) if f else 0,
                "flow_min_p": f["min_p"] if f else None,
                "flow_max_print_usd": f["max_print"] if f else None,
                "flow_call_prem": f["call_prem"] if f else None,
                "flow_put_prem": f["put_prem"] if f else None,
                "flow_short_dated_share": (f["short_dated"] / f["n"]) if f else None,
                "flow_events": f["n"] if f else 0,
                "angles": int(a is not None) + int(f is not None),
                "rank_pctile": max(pctiles) if pctiles else None,
                "first_lit_ts": min(t for t in (abn_ts, flow_ts) if t),
                "label": f"attention — {_ANGLE_NAMES[(a is not None, f is not None)]}",
                "caveat": CAVEAT,
            }
        )
    return rows


def refresh_attention(session: str | None = None, *, db_path: Path | None = None) -> int:
    """Build + upsert the tick's picture; returns rows written. Both-angle
    names above the display floor also enter the bounded look_into queue
    (design §4.3 — one item per name-day, idempotent, consumed one per
    investigator run; nothing is injected into other prompts)."""
    from db.attention import enqueue_pending, upsert_rows

    rows = build_attention_rows(session, db_path=db_path)
    n = upsert_rows(rows, db_path=db_path)
    if rows:
        enqueue_pending(rows[0]["session"], min_quintile=3, db_path=db_path)
    return n
