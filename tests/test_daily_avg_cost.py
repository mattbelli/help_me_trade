"""daily_avg_cost_realized — partial-close day attribution (avg-cost basis)."""

from __future__ import annotations

from decimal import Decimal

from db.analytics import daily_avg_cost_realized, realized_between


def _ev(
    day: str,
    side: str,
    qty: str,
    amount: str,
    *,
    symbol: str = "TSLA",
    account: str = "acct_a",
    exec_time: str | None = None,
    row_id: str = "e1",
) -> dict:
    return {
        "id": row_id,
        "account_id": account,
        "event_type": "trade",
        "trade_date": day,
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "price": None,
        "fees": "0",
        "amount": amount,
        "exec_time": exec_time,
    }


def test_partial_close_books_on_trade_day() -> None:
    # Buy 300 @100 (day1), sell 200 @110 (day2) — position stays open.
    events = [
        _ev("2026-07-01", "buy", "300", "-30000", row_id="a"),
        _ev("2026-07-02", "sell", "200", "22000", row_id="b"),
    ]
    out = daily_avg_cost_realized(events)
    assert Decimal(out["2026-07-02"]["realized_avg"]) == Decimal("2000")
    assert not out["2026-07-02"]["avg_approx"]
    assert "2026-07-01" not in out
    # Boundary method sees $0 (never flat) — the exact gap this fixes.
    assert Decimal(
        realized_between(events, "2026-07-02", "2026-07-02")["realized_closed"]
    ) == Decimal("0")


def test_flat_to_flat_total_matches_boundary() -> None:
    events = [
        _ev("2026-07-01", "buy", "100", "-10000", row_id="a"),
        _ev("2026-07-02", "sell", "40", "4400", row_id="b"),
        _ev("2026-07-03", "sell", "60", "6900", row_id="c"),
    ]
    out = daily_avg_cost_realized(events)
    total = sum(Decimal(v["realized_avg"]) for v in out.values())
    boundary = Decimal(
        realized_between(events, None, "2026-07-03")["realized_closed"]
    )
    assert total == boundary == Decimal("1300")


def test_pre_history_sell_flagged_never_fabricated() -> None:
    # Sell 100 with no known buys → unattributed, flagged; covering buy keeps
    # the phantom cover's cost out of the basis so the next lot stays true.
    events = [
        _ev("2026-07-01", "sell", "100", "40000", row_id="a"),
        _ev("2026-07-02", "buy", "200", "-80000", row_id="b"),  # covers 100 + opens 100 @400
        _ev("2026-07-03", "sell", "100", "41000", row_id="c"),
    ]
    out = daily_avg_cost_realized(events)
    assert Decimal(out["2026-07-01"]["realized_avg"]) == Decimal("0")
    assert out["2026-07-01"]["avg_approx"]
    assert out["2026-07-02"]["avg_approx"]
    # Day 3 sells the KNOWN 100 @400 lot → exact +1000, unflagged.
    assert Decimal(out["2026-07-03"]["realized_avg"]) == Decimal("1000")
    assert not out["2026-07-03"]["avg_approx"]


def test_same_day_scratch_uses_exec_time_order() -> None:
    # 14:02 buy 100 @394.17, 14:20 sell 100 @394.03, 16:52 buy 100 @394.76.
    # Recorded id order puts the sell FIRST — exec_time must win.
    events = [
        _ev("2026-07-08", "sell", "100", "39403", exec_time="2026-07-08T14:20:17+0000", row_id="a"),
        _ev("2026-07-08", "buy", "100", "-39417", exec_time="2026-07-08T14:02:49+0000", row_id="b"),
        _ev("2026-07-08", "buy", "100", "-39476", exec_time="2026-07-08T16:52:21+0000", row_id="c"),
        _ev("2026-07-09", "sell", "50", "20377", row_id="d"),
    ]
    out = daily_avg_cost_realized(events)
    assert Decimal(out["2026-07-08"]["realized_avg"]) == Decimal("-14")
    assert not out["2026-07-08"]["avg_approx"]
    # Jul 9 sells 50 of the 394.76 lot: 20377 − 19738 = 639.
    assert Decimal(out["2026-07-09"]["realized_avg"]) == Decimal("639")


def test_date_only_rows_process_buys_before_sells() -> None:
    # No exec_time and the sell recorded first — must not fake a short.
    events = [
        _ev("2026-07-01", "sell", "100", "11000", row_id="a"),
        _ev("2026-07-01", "buy", "100", "-10000", row_id="b"),
    ]
    out = daily_avg_cost_realized(events)
    assert Decimal(out["2026-07-01"]["realized_avg"]) == Decimal("1000")
    assert not out["2026-07-01"]["avg_approx"]


def test_accounts_never_merge_bases() -> None:
    events = [
        _ev("2026-07-01", "buy", "100", "-10000", account="acct_a", row_id="a"),
        _ev("2026-07-01", "buy", "100", "-20000", account="acct_b", row_id="b"),
        _ev("2026-07-02", "sell", "100", "15000", account="acct_a", row_id="c"),
    ]
    out = daily_avg_cost_realized(events)
    # acct_a realizes vs ITS $100 basis (+5000), not a blended $150 basis.
    assert Decimal(out["2026-07-02"]["realized_avg"]) == Decimal("5000")


def test_window_filters_attribution_but_not_basis() -> None:
    events = [
        _ev("2026-06-01", "buy", "100", "-10000", row_id="a"),
        _ev("2026-07-02", "sell", "100", "15000", row_id="b"),
    ]
    out = daily_avg_cost_realized(events, from_date="2026-07-01", to_date="2026-07-31")
    # Basis from June carries into July's attribution.
    assert Decimal(out["2026-07-02"]["realized_avg"]) == Decimal("5000")
    assert "2026-06-01" not in out
