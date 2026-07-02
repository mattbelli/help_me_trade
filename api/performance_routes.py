"""Performance dashboard API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ai.retrospective import generate_retrospective_report
from db.account_scope import resolve_perf_scope
from db.analytics import ai_performance_context, performance_dashboard
from db.trades import daily_trade_stats, symbol_trade_stats
from signals.sector_breakdown import sector_breakdown
from trading.sync import sync_all_enabled_brokers

router = APIRouter(prefix="/api/performance", tags=["performance"])


@router.get("/dashboard")
def api_performance_dashboard(
    account_id: str | None = None,
    period: str = Query(default="month"),
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    scope = resolve_perf_scope(account_id)
    return {
        **performance_dashboard(
            account_id=scope.ledger_account_id,
            real_only=scope.real_only,
            paper_only=scope.paper_only,
            period=period,
            custom_from=from_date,
            custom_to=to_date,
        ),
        "scope_fallback": scope.fallback,
    }


@router.get("/ai-context")
def api_ai_performance_context(
    account_id: str | None = None,
    period: str = Query(default="year"),
    include_trade_context: bool = Query(default=False),
) -> dict[str, Any]:
    scope = resolve_perf_scope(account_id)
    return {
        **ai_performance_context(
            account_id=scope.ledger_account_id,
            real_only=scope.real_only,
            paper_only=scope.paper_only,
            period=period,
            include_trade_context=include_trade_context,
        ),
        "scope_fallback": scope.fallback,
    }


@router.post("/sync")
def api_sync_trades() -> dict[str, Any]:
    results = sync_all_enabled_brokers()
    return {"results": results}


@router.post("/retrospective")
def api_generate_retrospective(
    account_id: str | None = None,
    period: str = Query(default="all"),
) -> dict[str, Any]:
    return generate_retrospective_report(account_id=account_id, period=period)


@router.get("/calendar")
def api_performance_calendar(
    account_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    scope = resolve_perf_scope(account_id)
    return {
        "days": daily_trade_stats(
            account_id=scope.ledger_account_id,
            from_date=from_date,
            to_date=to_date,
            real_only=scope.real_only,
            paper_only=scope.paper_only,
        ),
        "scope_fallback": scope.fallback,
    }


@router.get("/symbols")
def api_performance_symbols(
    account_id: str | None = None,
    period: str = "all",
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    from db.analytics import resolve_period

    scope = resolve_perf_scope(account_id)
    f, t = resolve_period(period, custom_from=from_date, custom_to=to_date)
    return {
        "symbols": symbol_trade_stats(
            account_id=scope.ledger_account_id,
            from_date=f,
            to_date=t,
            real_only=scope.real_only,
            paper_only=scope.paper_only,
        ),
        "scope_fallback": scope.fallback,
    }


@router.get("/sectors")
async def api_performance_sectors(
    account_id: str | None = None,
    period: str = Query(default="all"),
) -> dict[str, Any]:
    return await sector_breakdown(account_id=account_id, period=period)
