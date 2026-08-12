"""Portfolio exposure + realized P&L grouped by sector/industry (read-only).

Fuses three existing read-only sources — nothing here reimplements them:
  - **current positions**: real Schwab (``brokers.schwab_accounts.get_schwab_accounts_sync``)
    or the latest paper snapshot (``db.sqlite.get_latest_snapshot``), matching the same
    real/paper firewall used across the app (never blended into one figure).
  - **realized P&L per symbol**: FIFO round-trips (``db.analytics.compute_round_trips``)
    over the SAME account scope as the positions, summed per symbol and filtered to the
    requested period by ``close_date``.
  - **sector/industry per symbol**: Finnhub's company profile, read from the persistent
    cache (``signals.finnhub.profile_from_disk``) — the request path never hits the
    network. Fail-soft: a symbol whose profile can't be resolved lands in
    ``unclassified`` rather than erroring the whole request.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from db.account_scope import resolve_perf_scope
from db.analytics import compute_round_trips, resolve_period
from db.base import DEFAULT_DB_PATH
from db.trades import list_trade_events
from imports.money import decimal_to_str
from signals.finnhub import afetch_company_profile, profile_from_disk

# Fetching sectors inline doesn't scale: a ~100-position account burst 100 requests
# at a 60/min free tier, 429'd most of them, and fail-soft turned that into 91%
# "unclassified" — on EVERY load, since the old cache was in-process and success-only.
# So the request path is cache-only and misses fill in the background, paced under
# the budget. A cold symbol set completes in ~2min; every load after is instant.
_FILL_INTERVAL_SECONDS = 1.2  # ~50/min, inside signals.finnhub's 55/min budget
_fill_inflight: set[str] = set()
_fill_tasks: set[asyncio.Task[None]] = set()


def _relevant_trade_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["event_type"] in ("trade", "option_expired")]


def _resolve_positions(
    account_id: str | None,
) -> list[dict[str, Any]]:
    """Live positions for the requested workspace account scope.

    None/""/"overview"/"all"/"all_real" => aggregate every REAL (Schwab) account — the
    paper Alpaca sandbox is never blended into the real total. ``all_paper`` => every
    paper account. ``schwab_<last4>`` => that one real account. Anything else is
    treated as a paper trade-account id.

    The aggregate tokens come from ``db.account_scope`` so this stays in step with
    ``resolve_perf_scope``, which supplies the realized-P&L half of the same widget.
    A local copy of the list silently omitted "overview" — the selector's DEFAULT
    value — so the exposure half was resolving it as a trade-account id.
    """
    from db.account_scope import AGGREGATE_REAL

    if account_id in AGGREGATE_REAL:
        from brokers.schwab_accounts import get_schwab_accounts_sync

        data = get_schwab_accounts_sync()
        positions: list[dict[str, Any]] = []
        for acct in data.get("accounts") or []:
            positions.extend(acct.get("positions") or [])
        return positions

    if account_id == "all_paper":
        from db.sqlite import get_all_latest_snapshots

        positions = []
        for snap in get_all_latest_snapshots():
            positions.extend(snap.get("positions") or [])
        return positions

    if str(account_id).startswith("schwab_"):
        from brokers.schwab_accounts import get_schwab_accounts_sync

        data = get_schwab_accounts_sync()
        acct = next(
            (a for a in data.get("accounts") or [] if a.get("id") == account_id),
            None,
        )
        return acct.get("positions") or [] if acct else []

    from db.sqlite import get_latest_snapshot

    snap = get_latest_snapshot(account_id=account_id)
    return snap.get("positions") or [] if snap else []


def _merge_positions(positions: list[dict[str, Any]]) -> dict[str, dict[str, Decimal]]:
    """Sum market_value/unrealized_pl per symbol (a symbol could repeat across
    multiple aggregated real accounts)."""
    merged: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"market_value": Decimal("0"), "unrealized_pl": Decimal("0")}
    )
    for p in positions:
        sym = str(p.get("symbol") or "").strip().upper()
        if not sym:
            continue
        merged[sym]["market_value"] += Decimal(str(p.get("market_value") or 0))
        merged[sym]["unrealized_pl"] += Decimal(str(p.get("unrealized_pl") or 0))
    return merged


def _realized_pnl_by_symbol(
    *,
    trade_account_id: str | None,
    real_only: bool,
    paper_only: bool = False,
    from_date: str | None,
    to_date: str | None,
    db_path: Path,
) -> dict[str, Decimal]:
    # FIFO needs full history through to_date so sells match earlier buys, same as
    # db.analytics.performance_dashboard.
    history_events = list_trade_events(
        account_id=trade_account_id,
        to_date=to_date,
        real_only=real_only,
        paper_only=paper_only,
        limit=100_000,
        db_path=db_path,
    )
    round_trips = compute_round_trips(_relevant_trade_events(history_events))
    out: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for rt in round_trips:
        close_date = rt.get("close_date")
        if from_date and close_date and close_date < from_date:
            continue
        if to_date and close_date and close_date > to_date:
            continue
        out[rt["symbol"]] += Decimal(rt["pnl"])
    return out


async def _fill_profiles(symbols: list[str]) -> None:
    """Populate the persistent profile cache one symbol at a time, under the budget."""
    try:
        for sym in symbols:
            await afetch_company_profile(sym)
            await asyncio.sleep(_FILL_INTERVAL_SECONDS)
    finally:
        _fill_inflight.difference_update(symbols)


def _schedule_fill(symbols: list[str]) -> None:
    pending = [s for s in symbols if s not in _fill_inflight]
    if not pending:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # called outside a loop (a script/test) — nothing to fill into
        return
    _fill_inflight.update(pending)
    task = loop.create_task(_fill_profiles(pending))
    _fill_tasks.add(task)  # a bare create_task can be garbage-collected mid-flight
    task.add_done_callback(_fill_tasks.discard)


def _sectors_for_symbols(symbols: list[str]) -> dict[str, str | None]:
    """Industry per symbol from the persistent cache. Never blocks on the network:
    unknown symbols come back None and are fetched in the background for next load."""
    out: dict[str, str | None] = {}
    misses: list[str] = []
    for sym in symbols:
        profile = profile_from_disk(sym)
        if profile is None:
            misses.append(sym)
            out[sym] = None
        else:
            out[sym] = profile.get("finnhubIndustry") or None
    _schedule_fill(misses)
    return out


async def sector_breakdown(
    *,
    account_id: str | None = None,
    period: str = "all",
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Portfolio exposure AND realized P&L, grouped by sector/industry.

    The symbol universe is every currently-held symbol UNION every symbol with
    realized P&L in the period, so a fully-closed position's sector performance
    isn't silently dropped just because it's no longer held (it simply carries
    $0 market_value / 0 weight_pct).
    """
    from_date, to_date = resolve_period(period)
    positions = _resolve_positions(account_id)
    ledger_account_id, real_only, paper_only, _fallback = resolve_perf_scope(
        account_id, db_path=db_path
    )
    pos_by_symbol = _merge_positions(positions)
    realized_by_symbol = _realized_pnl_by_symbol(
        trade_account_id=ledger_account_id,
        real_only=real_only,
        paper_only=paper_only,
        from_date=from_date,
        to_date=to_date,
        db_path=db_path,
    )

    universe = sorted(set(pos_by_symbol) | set(realized_by_symbol))
    sector_by_symbol = _sectors_for_symbols(universe)

    groups: dict[str, dict[str, Any]] = {}
    unclassified_symbols: list[str] = []
    unclassified_mv = Decimal("0")
    unclassified_realized = Decimal("0")
    total_mv = Decimal("0")

    for sym in universe:
        mv = pos_by_symbol.get(sym, {}).get("market_value", Decimal("0"))
        upl = pos_by_symbol.get(sym, {}).get("unrealized_pl", Decimal("0"))
        realized = realized_by_symbol.get(sym, Decimal("0"))
        total_mv += mv

        sector = sector_by_symbol.get(sym)
        if not sector:
            unclassified_symbols.append(sym)
            unclassified_mv += mv
            unclassified_realized += realized
            continue

        g = groups.setdefault(
            sector,
            {
                "market_value": Decimal("0"),
                "unrealized_pl": Decimal("0"),
                "realized_pnl": Decimal("0"),
                "symbols": [],
                "position_count": 0,
            },
        )
        g["market_value"] += mv
        g["unrealized_pl"] += upl
        g["realized_pnl"] += realized
        g["symbols"].append(sym)
        if sym in pos_by_symbol:
            g["position_count"] += 1

    sectors_out = [
        {
            "sector": sector,
            "weight_pct": round(float(g["market_value"] / total_mv), 4) if total_mv else 0.0,
            "market_value": decimal_to_str(g["market_value"]),
            "unrealized_pl": decimal_to_str(g["unrealized_pl"]),
            "realized_pnl": decimal_to_str(g["realized_pnl"]),
            "position_count": g["position_count"],
            "symbols": sorted(g["symbols"]),
        }
        for sector, g in groups.items()
    ]
    sectors_out.sort(key=lambda s: s["weight_pct"], reverse=True)

    return {
        "as_of": datetime.now(UTC).isoformat(),
        "sectors": sectors_out,
        "unclassified": {
            "symbols": sorted(unclassified_symbols),
            "market_value": decimal_to_str(unclassified_mv),
            "realized_pnl": decimal_to_str(unclassified_realized),
        },
    }
