"""Schwab Transactions-API sync: parser + incremental cursor (read-only path)."""

from datetime import date
from decimal import Decimal

from db.trades import create_trade_account, import_trade_events, latest_trade_date
from imports.base import ParsedTradeEvent
from trading.sync import _schwab_sync_start, _schwab_transaction_to_event


def _equity_trade(*, symbol, shares, price, net, fee=0.0, activity_id="1"):
    return {
        "type": "TRADE",
        "activityId": activity_id,
        "tradeDate": "2026-07-01T15:24:54+0000",
        "netAmount": net,
        "transferItems": [
            {
                "instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"},
                "amount": fee,
                "feeType": "SEC_FEE",
            },
            {
                "instrument": {
                    "assetType": "EQUITY",
                    "symbol": symbol,
                    "uniformSymbol": symbol,
                },
                "amount": shares,
                "price": price,
            },
        ],
    }


# --- parser ---------------------------------------------------------------

def test_parse_equity_sell():
    ev = _schwab_transaction_to_event(
        _equity_trade(symbol="NVDA", shares=-500.0, price=196.56, net=98277.88, fee=2.12),
        account_id="acct_x",
    )
    assert ev is not None
    assert ev.symbol == "NVDA"
    assert ev.side == "sell"
    assert ev.quantity == Decimal("500.0")
    assert ev.price == Decimal("196.56")
    assert ev.amount == Decimal("98277.88")  # net cash, fee-inclusive
    assert ev.fees == Decimal("2.12")
    assert ev.trade_date == date(2026, 7, 1)
    assert ev.broker_txn_id == "1"


def test_parse_equity_buy():
    ev = _schwab_transaction_to_event(
        _equity_trade(symbol="NVDA", shares=250.0, price=194.01, net=-48502.5),
        account_id="acct_x",
    )
    assert ev.side == "buy"
    assert ev.quantity == Decimal("250.0")
    assert ev.amount == Decimal("-48502.5")


def test_parse_currency_only_is_none():
    tx = {
        "type": "TRADE",
        "activityId": "2",
        "tradeDate": "2026-07-01",
        "netAmount": -5.0,
        "transferItems": [
            {"instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"}, "amount": -5.0}
        ],
    }
    assert _schwab_transaction_to_event(tx, account_id="a") is None


def test_parse_non_trade_is_none():
    tx = {
        "type": "DIVIDEND_OR_INTEREST",
        "activityId": "3",
        "transferItems": [
            {"instrument": {"assetType": "EQUITY", "symbol": "AAPL"}, "amount": 0}
        ],
    }
    assert _schwab_transaction_to_event(tx, account_id="a") is None


def test_parse_picks_primary_security_leg():
    # cross-symbol spread: primary = the symbol group with larger total abs(amount)
    tx = {
        "type": "TRADE",
        "activityId": "4",
        "tradeDate": "2026-07-01",
        "netAmount": 100.0,
        "transferItems": [
            {"instrument": {"assetType": "OPTION", "symbol": "A"}, "amount": 1.0, "price": 1.0},
            {"instrument": {"assetType": "OPTION", "symbol": "B"}, "amount": -3.0, "price": 2.0},
        ],
    }
    ev = _schwab_transaction_to_event(tx, account_id="a")
    assert ev.symbol == "B"
    assert ev.side == "sell"
    assert ev.quantity == Decimal("3.0")
    assert ev.price == Decimal("2.0")


def test_parse_same_symbol_multileg_sums_legs():
    """Schwab splits one sell-through-flat fill into CLOSING + OPENING legs."""
    tx = {
        "type": "TRADE",
        "activityId": "106464088442",
        "tradeDate": "2026-07-02T15:30:00+0000",
        "netAmount": 79798.33,
        "transferItems": [
            {
                "instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"},
                "amount": 1.64,
                "feeType": "SEC_FEE",
            },
            {
                "instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"},
                "amount": 0.03,
                "feeType": "TAF_FEE",
            },
            {
                "instrument": {
                    "assetType": "EQUITY",
                    "symbol": "TSLA",
                    "uniformSymbol": "TSLA",
                },
                "amount": -126.0,
                "price": 399.0,
                "positionEffect": "OPENING",
            },
            {
                "instrument": {
                    "assetType": "EQUITY",
                    "symbol": "TSLA",
                    "uniformSymbol": "TSLA",
                },
                "amount": -74.0,
                "price": 399.0,
                "positionEffect": "CLOSING",
            },
        ],
    }
    ev = _schwab_transaction_to_event(tx, account_id="acct_x")
    assert ev is not None
    assert ev.symbol == "TSLA"
    assert ev.side == "sell"
    assert ev.quantity == Decimal("200.0")
    assert ev.price == Decimal("399.0")
    assert ev.amount == Decimal("79798.33")
    assert ev.fees == Decimal("1.67")
    assert ev.trade_date == date(2026, 7, 2)


def test_parse_single_leg_buy_unchanged():
    ev = _schwab_transaction_to_event(
        _equity_trade(symbol="AAPL", shares=10.0, price=150.0, net=-1500.0),
        account_id="acct_x",
    )
    assert ev.side == "buy"
    assert ev.quantity == Decimal("10.0")
    assert ev.price == Decimal("150.0")


# --- cursor (#1 regression: no same-day fill loss) ------------------------

def test_cursor_first_api_sync_after_json():
    assert _schwab_sync_start(date(2026, 6, 15), None, date(2026, 7, 1), 365) == date(2026, 6, 16)


def test_cursor_rolling_refetch_does_not_drop_same_day():
    # API already saw a fill dated today; a later same-day fill must still be
    # re-fetchable → start <= end (the naive last+1 cursor gave today+1 → skip).
    today = date(2026, 7, 1)
    start = _schwab_sync_start(date(2026, 6, 15), today, today, 365)
    assert start <= today
    assert start == date(2026, 6, 24)  # today - 7d, above the JSON floor


def test_cursor_never_crosses_json_boundary():
    start = _schwab_sync_start(date(2026, 6, 28), date(2026, 6, 30), date(2026, 7, 1), 365)
    assert start == date(2026, 6, 29)  # json_max + 1, not the rolling 06-23


def test_cursor_first_ever_backfill():
    assert _schwab_sync_start(None, None, date(2026, 7, 1), 365) == date(2025, 7, 1)


# --- latest_trade_date source filter --------------------------------------

def _ev(d, key):
    return ParsedTradeEvent(
        event_type="trade", trade_date=d, symbol="AAPL", side="buy",
        quantity=Decimal("1"), price=Decimal("1"), fees=Decimal("0"),
        amount=Decimal("-1"), description="", external_key=key, raw={},
    )


def test_latest_trade_date_source_filter(tmp_path):
    db = tmp_path / "t.db"
    acct = create_trade_account("Test", "schwab", db_path=db)["id"]
    import_trade_events(acct, [_ev(date(2026, 6, 15), "j1")],
                        source="schwab_json_import", db_path=db)
    import_trade_events(acct, [_ev(date(2026, 6, 20), "a1")],
                        source="schwab_api", db_path=db)

    assert latest_trade_date(acct, source="schwab_json_import", db_path=db) == date(2026, 6, 15)
    assert latest_trade_date(acct, source="schwab_api", db_path=db) == date(2026, 6, 20)
    assert latest_trade_date(acct, db_path=db) == date(2026, 6, 20)


# --- accelerated refresh scope (position-change trigger) -------------------

def _mapped_accounts():
    return [
        {"id": "acct_a", "label": "Individual", "broker_external_id": "hash_a"},
        {"id": "acct_b", "label": "Roth IRA", "broker_external_id": "hash_b"},
    ]


def _stub_schwab_sync(monkeypatch):
    """Mock every network/ledger touch point; return the list of synced ids."""
    from trading import sync as sync_mod

    synced: list[str] = []
    monkeypatch.setattr(sync_mod, "token_exists", lambda: True)
    monkeypatch.setattr(sync_mod, "list_accounts", lambda **kw: _mapped_accounts())
    monkeypatch.setattr(sync_mod, "create_client", lambda: object())

    def _fake_account_sync(client, acct, *, end, lookback_days):
        synced.append(acct["id"])
        return {"account_id": acct["id"], "fetched": 0, "inserted": 0, "duplicates": 0}

    monkeypatch.setattr(sync_mod, "_sync_schwab_account", _fake_account_sync)
    return sync_mod, synced


def test_sync_schwab_without_external_ids_covers_every_account(monkeypatch):
    sync_mod, synced = _stub_schwab_sync(monkeypatch)
    sync_mod.sync_schwab()
    assert synced == ["acct_a", "acct_b"]


def test_sync_schwab_external_ids_limits_to_the_changed_account(monkeypatch):
    sync_mod, synced = _stub_schwab_sync(monkeypatch)
    result = sync_mod.sync_schwab(external_ids=["hash_b"])
    assert synced == ["acct_b"]
    assert [r["account_id"] for r in result["accounts"]] == ["acct_b"]


def test_sync_schwab_unmapped_external_id_syncs_nothing(monkeypatch):
    sync_mod, synced = _stub_schwab_sync(monkeypatch)
    assert sync_mod.sync_schwab(external_ids=["hash_unknown"]) == {
        "broker": "schwab",
        "skipped": "no mapped accounts",
    }
    assert synced == []


def test_sync_schwab_is_single_flight(monkeypatch):
    sync_mod, synced = _stub_schwab_sync(monkeypatch)
    assert sync_mod._schwab_sync_lock.acquire(blocking=False)
    try:
        assert sync_mod.sync_schwab(external_ids=["hash_a"]) == {
            "broker": "schwab",
            "skipped": "already running",
        }
    finally:
        sync_mod._schwab_sync_lock.release()
    assert synced == []
