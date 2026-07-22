"""EOD options-flow scan — live data → daily rows → UV-1/UV-2 events.

Pure-ish orchestration: adapter is injected (testable with a fake). Fail-soft
per underlying — exceptions are logged and skipped, never raised to the job
runner. Read-only Mode A; no notifications (Phase 2).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from db import options_flow as of_db
from signals.options_flow.constants import (
    DTE_BUCKETS,
    OPTIONS_FLOW_MAX_CONTRACTS_PER_UNDERLYING,
    OPTIONS_FLOW_MAX_UNDERLYINGS,
    dte_bucket,
)
from signals.options_flow.detect import detect_uv1, detect_uv2

logger = logging.getLogger(__name__)

_BARS_CHUNK = 100


def eligible_underlyings(symbols: list[str]) -> list[str]:
    """Watchlist symbols that can actually carry Alpaca options, capped.

    Shared by the nightly scan and the intraday poll. Two traps this closes:
    futures roots (/ES, /CL, /NQ) 400 on the options endpoints, so each one burned an
    underlying slot AND a REST call per cycle for nothing; and a silent truncation
    drops whatever the caller ordered LAST — input order is meaningful (the flow
    list arrives pinned-first, hot-lane promotions last), so the clip preserves it
    and drops the tail. Going over the cap is a warning, not a quiet drop.
    """
    eligible = [
        s.strip().upper()
        for s in symbols
        if s and str(s).strip() and not str(s).strip().startswith("/")
    ]
    if len(eligible) > OPTIONS_FLOW_MAX_UNDERLYINGS:
        logger.warning(
            "options_flow: %d option-eligible underlyings exceeds the cap of %d — "
            "NOT watching %s",
            len(eligible),
            OPTIONS_FLOW_MAX_UNDERLYINGS,
            ",".join(eligible[OPTIONS_FLOW_MAX_UNDERLYINGS:]),
        )
    return eligible[:OPTIONS_FLOW_MAX_UNDERLYINGS]


def _int_oi(v: Any) -> int | None:
    """Parse open_interest from Alpaca (string, int, float, or None)."""
    if v is None:
        return None
    try:
        s = str(v).strip()
        if not s:
            return None
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _dte_days(expiration: str, event_date: str) -> int | None:
    """Days from ``event_date`` to ``expiration`` (both ISO YYYY-MM-DD)."""
    try:
        exp = date.fromisoformat(str(expiration).strip()[:10])
        day = date.fromisoformat(str(event_date).strip()[:10])
        return (exp - day).days
    except (TypeError, ValueError):
        return None


def _normalize_right(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s.startswith("c"):
        return "call"
    if s.startswith("p"):
        return "put"
    return s or "call"


def _bar_volume_close(bars: list[dict] | None) -> tuple[int, float] | None:
    """Pick today's volume/close from a bars list; None if missing or vol 0."""
    if not bars:
        return None
    bar = bars[-1] if isinstance(bars, list) else None
    if not isinstance(bar, dict):
        return None
    try:
        vol = int(float(bar.get("v") or 0))
    except (TypeError, ValueError):
        return None
    if vol <= 0:
        return None
    try:
        close = float(bar.get("c"))
    except (TypeError, ValueError):
        return None
    return vol, close


def _prior_day(event_date: str) -> str:
    return (date.fromisoformat(event_date[:10]) - timedelta(days=1)).isoformat()


def _scan_underlying(
    adapter: Any,
    underlying: str,
    event_date: str,
    db_path: Path | None,
) -> tuple[int, int]:
    """Scan one underlying. Returns (daily_rows_written, events_inserted)."""
    u = underlying.strip().upper()
    contracts_raw = adapter.get_option_contracts(
        u, expiration_date_gte=event_date, limit=1000
    )
    if not isinstance(contracts_raw, list):
        return 0, 0

    ranked: list[tuple[int, dict]] = []
    for c in contracts_raw:
        if not isinstance(c, dict):
            continue
        oi = _int_oi(c.get("open_interest"))
        if oi is None:
            continue
        sym = str(c.get("symbol") or "").strip().upper()
        if not sym:
            continue
        ranked.append((oi, c))
    ranked.sort(key=lambda t: t[0], reverse=True)
    ranked = ranked[:OPTIONS_FLOW_MAX_CONTRACTS_PER_UNDERLYING]
    if not ranked:
        return 0, 0

    occ_list = [str(c.get("symbol") or "").strip().upper() for _, c in ranked]
    bars_by_occ: dict[str, list[dict]] = {}
    for i in range(0, len(occ_list), _BARS_CHUNK):
        chunk = occ_list[i : i + _BARS_CHUNK]
        got = adapter.get_option_bars(chunk, start=event_date, timeframe="1Day")
        if isinstance(got, dict):
            for occ, items in got.items():
                bars_by_occ[str(occ).strip().upper()] = items

    daily_rows: list[dict] = []
    contract_meta: list[dict] = []
    for oi, c in ranked:
        occ = str(c.get("symbol") or "").strip().upper()
        vc = _bar_volume_close(bars_by_occ.get(occ))
        if vc is None:
            continue
        volume, close = vc
        expiry = str(c.get("expiration_date") or "").strip()[:10]
        dte = _dte_days(expiry, event_date)
        if dte is None:
            continue
        try:
            strike = float(c.get("strike_price"))
        except (TypeError, ValueError):
            continue
        right = _normalize_right(c.get("type"))
        bucket = dte_bucket(dte)
        premium = float(volume) * close * 100.0
        daily_rows.append(
            {
                "underlying": u,
                "occ_symbol": occ,
                "date": event_date,
                "right": right,
                "dte": dte,
                "dte_bucket": bucket,
                "volume": volume,
                "prints": None,
                "premium_usd": premium,
                "close_oi": oi,
            }
        )
        contract_meta.append(
            {
                "occ_symbol": occ,
                "right": right,
                "strike": strike,
                "expiry": expiry,
                "dte": dte,
                "volume": volume,
                "oi": oi,
                "close": close,
            }
        )

    n_daily = of_db.upsert_daily(daily_rows, db_path=db_path) if daily_rows else 0
    n_events = 0

    for meta in contract_meta:
        ev = detect_uv1(
            underlying=u,
            occ_symbol=meta["occ_symbol"],
            right=meta["right"],
            strike=meta["strike"],
            expiry=meta["expiry"],
            dte=meta["dte"],
            otm_pct=None,
            volume=meta["volume"],
            oi=meta["oi"],
            close=meta["close"],
            event_date=event_date,
        )
        if ev is not None:
            of_db.insert_event(ev, db_path=db_path)
            n_events += 1

    bucket_vol: dict[str, float] = defaultdict(float)
    for row in daily_rows:
        b = row["dte_bucket"]
        if b == "out":
            continue
        bucket_vol[b] += float(row["volume"] or 0)

    # Known bucket labels from constants (stable order) plus any extras present.
    known = [f"{lo}-{hi}" for lo, hi in DTE_BUCKETS]
    for b in known:
        if b not in bucket_vol:
            continue
        today_vol = bucket_vol[b]
        baseline = of_db.daily_baselines(
            u, b, lookback_days=20, as_of=_prior_day(event_date), db_path=db_path
        )
        ev2 = detect_uv2(
            underlying=u,
            dte_bucket=b,
            today_vol=today_vol,
            baseline=baseline,
            event_date=event_date,
        )
        if ev2 is not None:
            # Schema requires occ_symbol NOT NULL; UV-2 is underlying-level.
            if ev2.get("occ_symbol") is None:
                ev2 = {**ev2, "occ_symbol": ""}
            of_db.insert_event(ev2, db_path=db_path)
            n_events += 1

    return n_daily, n_events


def run_eod_scan(
    adapter: Any,
    *,
    symbols: list[str],
    event_date: str,
    db_path: Path | None = None,
) -> dict:
    """Nightly EOD scan: contracts + bars → daily rows + UV-1/UV-2 events.

    Returns ``{"underlyings": n, "events": k, "daily_rows": m}`` for logging.
    """
    capped = eligible_underlyings(symbols)

    n_underlyings = 0
    n_events = 0
    n_daily = 0

    for sym in capped:
        try:
            d, e = _scan_underlying(adapter, sym, event_date, db_path)
            n_underlyings += 1
            n_daily += d
            n_events += e
        except Exception:
            logger.exception("options_flow EOD: skip underlying %s", sym)
            continue

    of_db.prune(now_iso=f"{event_date}T23:59:59Z", db_path=db_path)
    return {
        "underlyings": n_underlyings,
        "events": n_events,
        "daily_rows": n_daily,
    }
