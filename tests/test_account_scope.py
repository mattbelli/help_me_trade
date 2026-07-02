"""Tests for db.account_scope.resolve_perf_scope (read-only, no live Schwab)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from db.account_scope import resolve_perf_scope
from db.trades import create_trade_account, import_trade_events, list_trade_events
from imports.base import ParsedTradeEvent


def _fake_schwab_accounts() -> dict:
    return {
        "accounts": [
            {"id": "schwab_1025", "last4": "1025", "label": "Schwab Individual"},
        ]
    }


@pytest.fixture
def seeded_db(tmp_path):
    db = tmp_path / "t.db"
    schwab = create_trade_account("Schwab Individual", "schwab", db_path=db)
    paper = create_trade_account("Alpaca Paper", "alpaca", db_path=db)
    return db, schwab["id"], paper["id"]


@pytest.mark.parametrize(
    "workspace_id",
    [None, "", "overview", "all", "all_real"],
)
def test_aggregate_real_scopes(seeded_db, workspace_id):
    db, _, _ = seeded_db
    assert resolve_perf_scope(
        workspace_id,
        get_schwab_accounts=_fake_schwab_accounts,
        db_path=db,
    ) == (None, True, False)


def test_all_paper_scope(seeded_db):
    db, _, _ = seeded_db
    assert resolve_perf_scope(
        "all_paper",
        get_schwab_accounts=_fake_schwab_accounts,
        db_path=db,
    ) == (None, False, True)


def test_schwab_last4_resolves_to_ledger_id(seeded_db):
    db, schwab_id, _ = seeded_db
    assert resolve_perf_scope(
        "schwab_1025",
        get_schwab_accounts=_fake_schwab_accounts,
        db_path=db,
    ) == (schwab_id, False, False)


def test_unknown_schwab_falls_back_to_aggregate_real(seeded_db):
    db, _, _ = seeded_db
    assert resolve_perf_scope(
        "schwab_9999",
        get_schwab_accounts=_fake_schwab_accounts,
        db_path=db,
    ) == (None, True, False)


def test_valid_acct_id_returns_itself(seeded_db):
    db, _, paper_id = seeded_db
    assert resolve_perf_scope(
        paper_id,
        get_schwab_accounts=_fake_schwab_accounts,
        db_path=db,
    ) == (paper_id, False, False)


def test_unknown_acct_id_falls_back(seeded_db):
    db, _, _ = seeded_db
    assert resolve_perf_scope(
        "acct_bogus",
        get_schwab_accounts=_fake_schwab_accounts,
        db_path=db,
    ) == (None, True, False)


def test_garbage_input_falls_back(seeded_db):
    db, _, _ = seeded_db
    assert resolve_perf_scope(
        "nonsense",
        get_schwab_accounts=_fake_schwab_accounts,
        db_path=db,
    ) == (None, True, False)


def _ev(sym, key):
    return ParsedTradeEvent(
        event_type="trade",
        trade_date=date(2026, 6, 20),
        symbol=sym,
        side="buy",
        quantity=Decimal("1"),
        price=Decimal("1"),
        fees=Decimal("0"),
        amount=Decimal("-1"),
        description="",
        external_key=key,
        raw={},
    )


def test_paper_only_excludes_real(tmp_path):
    db = tmp_path / "t.db"
    real = create_trade_account("Schwab Individual", "schwab", db_path=db)["id"]
    paper = create_trade_account("Alpaca", "alpaca", db_path=db)["id"]
    import_trade_events(real, [_ev("NVDA", "r1")], source="schwab_api", db_path=db)
    import_trade_events(paper, [_ev("AAPL", "p1")], source="alpaca_api", db_path=db)

    assert {e["symbol"] for e in list_trade_events(db_path=db)} == {"NVDA", "AAPL"}
    assert {
        e["symbol"] for e in list_trade_events(paper_only=True, db_path=db)
    } == {"AAPL"}


def test_real_only_wins_when_both_flags_true(tmp_path):
    db = tmp_path / "t.db"
    real = create_trade_account("Schwab Individual", "schwab", db_path=db)["id"]
    paper = create_trade_account("Alpaca", "alpaca", db_path=db)["id"]
    import_trade_events(real, [_ev("NVDA", "r1")], source="schwab_api", db_path=db)
    import_trade_events(paper, [_ev("AAPL", "p1")], source="alpaca_api", db_path=db)

    assert {
        e["symbol"]
        for e in list_trade_events(real_only=True, paper_only=True, db_path=db)
    } == {"NVDA"}
