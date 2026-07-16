"""Filled-tab fill time: list_trade_events surfaces a broker fill timestamp
from raw_json (Alpaca transaction_time / Schwab time) as a display-only
`fill_time`, without changing the money-path `exec_time` (Schwab $.time only)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from db.trades import create_trade_account, import_trade_events, list_trade_events
from imports.base import ParsedTradeEvent


def _parsed(key: str, symbol: str, raw: dict) -> ParsedTradeEvent:
    return ParsedTradeEvent(
        event_type="trade",
        trade_date=date(2026, 7, 10),
        symbol=symbol,
        side="buy",
        quantity=Decimal("1"),
        price=None,
        fees=Decimal("0"),
        amount=Decimal("-100"),
        description=f"buy {symbol}",
        external_key=key,
        raw=raw,
    )


def test_fill_time_from_alpaca_schwab_and_none(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.db"
    account = create_trade_account("Fill", "alpaca", db_path=db_path)
    import_trade_events(
        account["id"],
        [
            _parsed("alp", "AAA", {"transaction_time": "2026-07-10T14:48:00.443245Z"}),
            _parsed("swb", "BBB", {"time": "2026-07-10T18:48:00+0000"}),
            _parsed("date_only", "CCC", {}),  # manual export, no time-of-day
        ],
        source="test",
        db_path=db_path,
    )

    events = {e["symbol"]: e for e in list_trade_events(db_path=db_path)}

    # Display fill_time covers BOTH broker key shapes.
    assert events["AAA"]["fill_time"] == "2026-07-10T14:48:00.443245Z"
    assert events["BBB"]["fill_time"] == "2026-07-10T18:48:00+0000"
    # Date-only rows carry no fill time.
    assert events["CCC"]["fill_time"] is None

    # Money-path exec_time is unchanged: Schwab $.time only, never Alpaca's key.
    assert events["AAA"]["exec_time"] is None
    assert events["BBB"]["exec_time"] == "2026-07-10T18:48:00+0000"
    assert events["CCC"]["exec_time"] is None
