"""list_trade_events(real_only=True) excludes the paper Alpaca sandbox (firewall)."""

from datetime import date
from decimal import Decimal

from db.trades import create_trade_account, import_trade_events, list_trade_events
from imports.base import ParsedTradeEvent


def _ev(sym, key):
    return ParsedTradeEvent(
        event_type="trade", trade_date=date(2026, 6, 20), symbol=sym, side="buy",
        quantity=Decimal("1"), price=Decimal("1"), fees=Decimal("0"),
        amount=Decimal("-1"), description="", external_key=key, raw={},
    )


def test_real_only_excludes_paper(tmp_path):
    db = tmp_path / "t.db"
    real = create_trade_account("Schwab Individual", "schwab", db_path=db)["id"]
    paper = create_trade_account("Alpaca", "alpaca", db_path=db)["id"]
    import_trade_events(real, [_ev("NVDA", "r1")], source="schwab_api", db_path=db)
    import_trade_events(paper, [_ev("AAPL", "p1")], source="alpaca_api", db_path=db)

    assert {e["symbol"] for e in list_trade_events(db_path=db)} == {"NVDA", "AAPL"}
    assert {
        e["symbol"] for e in list_trade_events(real_only=True, db_path=db)
    } == {"NVDA"}
    # an explicit account id still resolves that exact account (even paper)
    assert {
        e["symbol"] for e in list_trade_events(account_id=paper, db_path=db)
    } == {"AAPL"}
