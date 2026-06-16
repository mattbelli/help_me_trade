"""Tests for Schwab transaction JSON import."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from db.trades import (
    create_trade_account,
    import_trade_events,
    list_trade_events,
    trade_summary_stats,
)
from imports.schwab_transactions import parse_schwab_transactions_json

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "schwab_transactions_sample.json"


@pytest.fixture
def schwab_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_schwab_sample_row_count(schwab_payload: dict) -> None:
    result = parse_schwab_transactions_json(schwab_payload)
    assert len(result.events) == len(schwab_payload["BrokerageTransactions"])
    assert result.from_date == "2026-01-01"
    assert result.to_date == "2026-06-16"


def test_parse_buy_sell_amounts(schwab_payload: dict) -> None:
    result = parse_schwab_transactions_json(schwab_payload)
    tsla_sell = next(
        e
        for e in result.events
        if e.symbol == "TSLA"
        and e.trade_date.isoformat() == "2026-06-15"
        and e.side == "sell"
        and e.quantity is not None
        and e.quantity == 100
    )
    assert str(tsla_sell.amount) == "41014.14"
    assert str(tsla_sell.fees) == "0.86"

    nvda_buy = next(
        e
        for e in result.events
        if e.symbol == "NVDA"
        and e.trade_date.isoformat() == "2026-06-12"
        and e.side == "buy"
    )
    assert str(nvda_buy.amount) == "-30795.00"


def test_import_dedup(tmp_path: Path, schwab_payload: dict) -> None:
    db_path = tmp_path / "trades.db"
    account = create_trade_account("Schwab Test", "schwab", db_path=db_path)
    parsed = parse_schwab_transactions_json(schwab_payload, account_id=account["id"])

    first = import_trade_events(
        account["id"],
        parsed.events,
        source="schwab_json_import",
        db_path=db_path,
    )
    assert first.inserted == len(parsed.events)
    assert first.duplicates == 0

    second = import_trade_events(
        account["id"],
        parsed.events,
        source="schwab_json_import",
        db_path=db_path,
    )
    assert second.inserted == 0
    assert second.duplicates == len(parsed.events)

    stored = list_trade_events(account_id=account["id"], db_path=db_path)
    assert len(stored) == len(parsed.events)


def test_summary_stats(tmp_path: Path, schwab_payload: dict) -> None:
    db_path = tmp_path / "trades.db"
    account = create_trade_account("Schwab Test", "schwab", db_path=db_path)
    parsed = parse_schwab_transactions_json(schwab_payload, account_id=account["id"])
    import_trade_events(
        account["id"],
        parsed.events,
        source="schwab_json_import",
        db_path=db_path,
    )

    stats = trade_summary_stats(account_id=account["id"], db_path=db_path)
    assert stats["event_count"] == len(parsed.events)
    assert stats["trade_count"] > 0
    assert float(stats["total_fees"]) > 0
