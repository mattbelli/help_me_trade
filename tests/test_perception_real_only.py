"""my_trades perception resource: real (Schwab) only — no paper-sandbox blend."""

from datetime import date
from decimal import Decimal

from ai.perception.resources import _my_trade_events
from db.trades import create_trade_account, import_trade_events
from imports.base import ParsedTradeEvent


def _ev(symbol, key):
    return ParsedTradeEvent(
        event_type="trade", trade_date=date(2026, 6, 20), symbol=symbol, side="buy",
        quantity=Decimal("1"), price=Decimal("1"), fees=Decimal("0"),
        amount=Decimal("-1"), description="", external_key=key, raw={},
    )


def test_my_trades_excludes_paper_by_default(tmp_path):
    db = tmp_path / "t.db"
    real = create_trade_account("Schwab Individual", "schwab", db_path=db)["id"]
    paper = create_trade_account("Alpaca", "alpaca", db_path=db)["id"]
    import_trade_events(real, [_ev("NVDA", "r1")], source="schwab_api", db_path=db)
    import_trade_events(paper, [_ev("AAPL", "p1")], source="alpaca_api", db_path=db)

    default = _my_trade_events({}, db_path=db)
    symbols = {e["symbol"] for e in default}
    assert symbols == {"NVDA"}  # real only — paper AAPL excluded

    # explicit account_id still reaches a specific (even paper) account
    scoped = _my_trade_events({"account_id": paper}, db_path=db)
    assert {e["symbol"] for e in scoped} == {"AAPL"}
