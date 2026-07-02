"""Portfolio exposure + realized P&L grouped by sector/industry (read-only).

Fuses three existing read-only sources — nothing here reimplements them:
  - **current positions**: real Schwab (``brokers.schwab_accounts.get_schwab_accounts_sync``)
    or the latest paper snapshot (``db.sqlite.get_latest_snapshot``), matching the same
    real/paper firewall used across the app (never blended into one figure).
  - **realized P&L per symbol**: FIFO round-trips (``db.analytics.compute_round_trips``)
    over the SAME account scope as the positions, summed per symbol and filtered to the
    requested period by ``close_date``.
  - **sector/industry per symbol**: Finnhub's company profile
    (``signals.finnhub.afetch_company_profile``, already 24h-cached). Fetched with bounded
    concurrency to respect the free-tier rate limit. Fail-soft: a symbol whose profile
    can't be resolved lands in ``unclassified`` rather than erroring the whole request.
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
from signals.finnhub import afetch_company_profile

# Free-tier Finnhub is ~60 req/min; the 24h profile cache means only symbols never
# seen before actually hit the network, so a small bound is plenty.
_SECTOR_FETCH_CONCURRENCY = 5


def _relevant_trade_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["event_type"] in ("trade", "option_expired")]


def _resolve_positions(
    account_id: str | None,
) -> list[dict[str, Any]]:
    """Live positions for the requested workspace account scope.

    None/""/"all"/"all_real" => aggregate every REAL (Schwab) account — the paper Alpaca
    sandbox is never blended into the real total. ``schwab_<last4>`` => that one real
    account. Anything else is treated as a paper trade-account id.
    """
    aggregate = account_id in (None, "", "all", "all_real")
    if aggregate:
        from brokers.schwab_accounts import get_schwab_accounts_sync

        data = get_schwab_accounts_sync()
        positions: list[dict[str, Any]] = []
        for acct in data.get("accounts") or []:
            positions.extend(acct.get("positions") or [])
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


async def _sectors_for_symbols(symbols: list[str]) -> dict[str, str | None]:
    if not symbols:
        return {}
    sem = asyncio.Semaphore(_SECTOR_FETCH_CONCURRENCY)

    async def _one(sym: str) -> tuple[str, str | None]:
        async with sem:
            profile = await afetch_company_profile(sym)
        return sym, (profile.get("finnhubIndustry") or None)

    pairs = await asyncio.gather(*[_one(s) for s in symbols])
    return dict(pairs)


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
    ledger_account_id, real_only, paper_only = resolve_perf_scope(
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
    sector_by_symbol = await _sectors_for_symbols(universe)

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
