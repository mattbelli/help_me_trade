"""Schwab daily equity/P&L snapshot persistence (accurate record, no FIFO)."""

from db.schwab_history import list_schwab_daily, record_schwab_daily


def _acct(**kw):
    base = {
        "id": "schwab_1025", "last4": "1025", "label": "Schwab Individual",
        "liquidation_value": 71838.72, "cash": 71838.72,
        "long_market_value": 0.0, "day_pl": 866.13,
    }
    base.update(kw)
    return base


def test_record_and_list(tmp_path):
    db = tmp_path / "h.db"
    n = record_schwab_daily(
        [_acct(), _acct(id="schwab_3344", label="Schwab Joint", day_pl=0.0)],
        snapshot_date="2026-07-01", db_path=db,
    )
    assert n == 2
    by = {r["label"]: r for r in list_schwab_daily(db_path=db)}
    assert by["Schwab Individual"]["day_pl"] == "866.13"
    assert by["Schwab Individual"]["liquidation_value"] == "71838.72"


def test_upsert_one_row_per_account_per_day(tmp_path):
    db = tmp_path / "h.db"
    record_schwab_daily([_acct(day_pl=100.0)], snapshot_date="2026-07-01", db_path=db)
    record_schwab_daily([_acct(day_pl=866.13)], snapshot_date="2026-07-01", db_path=db)
    rows = list_schwab_daily(account_id="schwab_1025", db_path=db)
    assert len(rows) == 1  # upsert, not a second row
    assert rows[0]["day_pl"] == "866.13"  # latest capture wins


def test_distinct_days_accumulate(tmp_path):
    db = tmp_path / "h.db"
    record_schwab_daily([_acct(day_pl=866.13)], snapshot_date="2026-07-01", db_path=db)
    record_schwab_daily([_acct(day_pl=-120.0)], snapshot_date="2026-07-02", db_path=db)
    rows = list_schwab_daily(account_id="schwab_1025", db_path=db)
    assert [r["snapshot_date"] for r in rows] == ["2026-07-02", "2026-07-01"]  # newest first


def test_account_filter(tmp_path):
    db = tmp_path / "h.db"
    record_schwab_daily(
        [_acct(), _acct(id="schwab_3344", label="Schwab Joint")],
        snapshot_date="2026-07-01", db_path=db,
    )
    rows = list_schwab_daily(account_id="schwab_1025", db_path=db)
    assert {r["account_id"] for r in rows} == {"schwab_1025"}


def test_prior_futures_equity_is_last_earlier_day(tmp_path):
    from db.schwab_history import prior_futures_equity

    db = tmp_path / "h.db"
    record_schwab_daily([_acct(futures_equity=500.0)], snapshot_date="2026-07-01", db_path=db)
    record_schwab_daily([_acct(futures_equity=13302.26)], snapshot_date="2026-07-02", db_path=db)
    assert prior_futures_equity("schwab_1025", before_date="2026-07-02", db_path=db) == 500.0
    assert prior_futures_equity("schwab_1025", before_date="2026-07-01", db_path=db) is None
