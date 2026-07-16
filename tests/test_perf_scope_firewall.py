"""Paper↔real performance scope firewall — LLM context must not blend money types."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from db.account_scope import resolve_perf_scope
from db.analytics import ai_performance_context
from db.trades import create_trade_account, import_trade_events
from imports.base import ParsedTradeEvent
from imports.money import parse_dollar


def _fake_schwab_accounts() -> dict:
    return {
        "accounts": [
            {"id": "schwab_1025", "last4": "1025", "label": "Schwab Individual"},
        ]
    }


def _closed_trade(
    sym: str,
    *,
    buy_amount: Decimal,
    sell_amount: Decimal,
    buy_key: str,
    sell_key: str,
    buy_date: str = "2026-03-01",
    sell_date: str = "2026-03-05",
) -> list[ParsedTradeEvent]:
    return [
        ParsedTradeEvent(
            event_type="trade",
            trade_date=date.fromisoformat(buy_date),
            symbol=sym,
            side="buy",
            quantity=Decimal("10"),
            price=Decimal("100"),
            fees=Decimal("0"),
            amount=buy_amount,
            description=f"buy {sym}",
            external_key=buy_key,
            raw={},
        ),
        ParsedTradeEvent(
            event_type="trade",
            trade_date=date.fromisoformat(sell_date),
            symbol=sym,
            side="sell",
            quantity=Decimal("10"),
            price=Decimal("110"),
            fees=Decimal("0"),
            amount=sell_amount,
            description=f"sell {sym}",
            external_key=sell_key,
            raw={},
        ),
    ]


def _open_buy(sym: str, *, key: str, amount: Decimal = Decimal("-1000")) -> ParsedTradeEvent:
    return ParsedTradeEvent(
        event_type="trade",
        trade_date=date(2026, 4, 1),
        symbol=sym,
        side="buy",
        quantity=Decimal("10"),
        price=Decimal("100"),
        fees=Decimal("0"),
        amount=amount,
        description=f"buy {sym}",
        external_key=key,
        raw={},
    )


def _position(
    symbol: str,
    *,
    market_value: float,
    unrealized_pl: float,
    qty: float = 10.0,
) -> dict:
    return {
        "symbol": symbol,
        "qty": qty,
        "side": "long",
        "market_value": market_value,
        "unrealized_pl": unrealized_pl,
        "avg_entry_price": 100.0,
        "current_price": market_value / qty if qty else 0.0,
    }


def _collect_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_collect_strings(item))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_collect_strings(item))
        return out
    return []


def _payload_contains_symbol(payload: dict[str, Any], symbol: str) -> bool:
    return symbol in json.dumps(payload)


@pytest.fixture
def firewall_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "firewall.db"
    from db import trades

    def _with_db(fn):
        def wrapped(*args, **kwargs):
            kwargs.setdefault("db_path", db_path)
            return fn(*args, **kwargs)

        return wrapped

    monkeypatch.setattr("db.analytics.list_trade_events", _with_db(trades.list_trade_events))
    monkeypatch.setattr(
        "db.analytics.trade_summary_stats",
        _with_db(trades.trade_summary_stats),
    )
    monkeypatch.setattr(
        "db.analytics.daily_trade_stats",
        _with_db(trades.daily_trade_stats),
    )
    monkeypatch.setattr(
        "db.trades.symbol_trade_stats",
        _with_db(trades.symbol_trade_stats),
    )
    monkeypatch.setattr(
        "db.trades.get_trade_account",
        _with_db(trades.get_trade_account),
    )

    schwab = create_trade_account("Schwab Individual", "schwab", db_path=db_path)
    paper = create_trade_account("Alpaca Paper", "alpaca", db_path=db_path)

    real_buy = Decimal("-1000")
    real_sell = Decimal("1200")
    paper_buy = Decimal("-500")
    paper_sell = Decimal("600")

    import_trade_events(
        schwab["id"],
        _closed_trade(
            "REALX",
            buy_amount=real_buy,
            sell_amount=real_sell,
            buy_key="r-b",
            sell_key="r-s",
        ),
        source="schwab_api",
        db_path=db_path,
    )
    import_trade_events(
        paper["id"],
        _closed_trade(
            "PAPERX",
            buy_amount=paper_buy,
            sell_amount=paper_sell,
            buy_key="p-b",
            sell_key="p-s",
            buy_date="2026-02-01",
            sell_date="2026-02-05",
        ),
        source="alpaca_api",
        db_path=db_path,
    )

    expected_real_closed = real_buy + real_sell

    return {
        "db_path": db_path,
        "schwab_id": schwab["id"],
        "paper_id": paper["id"],
        "expected_real_closed": expected_real_closed,
    }


REAL_SCOPES = [None, "overview", "all_real"]
PAPER_SCOPES = ["all_paper"]


@pytest.mark.parametrize("workspace_id", REAL_SCOPES)
def test_real_aggregate_scopes_exclude_paper_symbol(
    firewall_db: dict[str, Any],
    workspace_id: str | None,
) -> None:
    scope = resolve_perf_scope(
        workspace_id,
        get_schwab_accounts=_fake_schwab_accounts,
        db_path=firewall_db["db_path"],
    )
    payload = ai_performance_context(
        account_id=scope.ledger_account_id,
        real_only=scope.real_only,
        paper_only=scope.paper_only,
        period="all",
    )
    assert not _payload_contains_symbol(payload, "PAPERX")
    assert parse_dollar(payload["realized"]["realized_closed"]) == firewall_db[
        "expected_real_closed"
    ]


@pytest.mark.parametrize("workspace_id", PAPER_SCOPES)
def test_paper_aggregate_scopes_exclude_real_symbol(
    firewall_db: dict[str, Any],
    workspace_id: str,
) -> None:
    scope = resolve_perf_scope(
        workspace_id,
        get_schwab_accounts=_fake_schwab_accounts,
        db_path=firewall_db["db_path"],
    )
    payload = ai_performance_context(
        account_id=scope.ledger_account_id,
        real_only=scope.real_only,
        paper_only=scope.paper_only,
        period="all",
    )
    assert not _payload_contains_symbol(payload, "REALX")


def test_real_ledger_account_scope_excludes_paper(firewall_db: dict[str, Any]) -> None:
    scope = resolve_perf_scope(
        firewall_db["schwab_id"],
        get_schwab_accounts=_fake_schwab_accounts,
        db_path=firewall_db["db_path"],
    )
    payload = ai_performance_context(
        account_id=scope.ledger_account_id,
        real_only=scope.real_only,
        paper_only=scope.paper_only,
        period="all",
    )
    assert not _payload_contains_symbol(payload, "PAPERX")
    assert parse_dollar(payload["realized"]["realized_closed"]) == firewall_db[
        "expected_real_closed"
    ]


def test_paper_ledger_account_scope_excludes_real(firewall_db: dict[str, Any]) -> None:
    scope = resolve_perf_scope(
        firewall_db["paper_id"],
        get_schwab_accounts=_fake_schwab_accounts,
        db_path=firewall_db["db_path"],
    )
    payload = ai_performance_context(
        account_id=scope.ledger_account_id,
        real_only=scope.real_only,
        paper_only=scope.paper_only,
        period="all",
    )
    assert not _payload_contains_symbol(payload, "REALX")


def test_real_scope_omits_alpaca_snapshot_unrealized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "snapshot.db"
    from db import trades

    def _with_db(fn):
        def wrapped(*args, **kwargs):
            kwargs.setdefault("db_path", db_path)
            return fn(*args, **kwargs)

        return wrapped

    monkeypatch.setattr("db.analytics.list_trade_events", _with_db(trades.list_trade_events))
    monkeypatch.setattr(
        "db.analytics.trade_summary_stats",
        _with_db(trades.trade_summary_stats),
    )
    monkeypatch.setattr(
        "db.analytics.daily_trade_stats",
        _with_db(trades.daily_trade_stats),
    )
    monkeypatch.setattr(
        "db.trades.symbol_trade_stats",
        _with_db(trades.symbol_trade_stats),
    )
    monkeypatch.setattr(
        "db.trades.get_trade_account",
        _with_db(trades.get_trade_account),
    )
    monkeypatch.setattr(
        "db.sqlite.get_latest_snapshot",
        lambda **kwargs: {
            "positions": [
                _position("PAPERX", market_value=500.0, unrealized_pl=50.0),
            ],
        },
    )

    schwab = create_trade_account("Schwab Individual", "schwab", db_path=db_path)
    paper = create_trade_account("Alpaca Paper", "alpaca", db_path=db_path)
    import_trade_events(
        schwab["id"],
        [_open_buy("REALX", key="r-open")],
        source="schwab_api",
        db_path=db_path,
    )
    import_trade_events(
        paper["id"],
        [_open_buy("PAPERX", key="p-open")],
        source="alpaca_api",
        db_path=db_path,
    )

    real_payload = ai_performance_context(
        account_id=None,
        real_only=True,
        paper_only=False,
        period="all",
    )
    assert real_payload["unrealized"] == {
        "unavailable": "no real-time broker snapshot for real accounts",
    }
    assert not _payload_contains_symbol(real_payload, "PAPERX")

    paper_payload = ai_performance_context(
        account_id=paper["id"],
        real_only=False,
        paper_only=False,
        period="all",
    )
    assert paper_payload["unrealized"]["by_symbol"]["PAPERX"]["broker_unrealized_pl"] == "50.0"


def test_dashboard_route_reports_scope_fallback() -> None:
    from api.main import app

    resp = TestClient(app).get("/api/performance/dashboard?account_id=acct_zzz")
    assert resp.status_code == 200
    assert resp.json()["scope_fallback"] is True
