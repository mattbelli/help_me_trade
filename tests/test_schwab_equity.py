"""Flat-days-only equity reconstruction: exact on cash days, skip overnight holds."""

from decimal import Decimal

from signals.schwab_equity import _reconstruct_flat_days


def test_skips_overnight_hold_days():
    # 06-01 flat; 06-02 bought 10 NVDA (held EOD); 06-03 (today) sold, flat again.
    all_days = ["2026-06-01", "2026-06-02", "2026-06-03"]
    cash = {"2026-06-02": Decimal("-1000"), "2026-06-03": Decimal("1050")}
    pos = {
        "2026-06-02": {("1025", "NVDA"): Decimal("10")},
        "2026-06-03": {("1025", "NVDA"): Decimal("-10")},
    }
    points, skipped = _reconstruct_flat_days(all_days, cash, pos, Decimal("1050"), {})

    assert skipped == 1
    assert [p["date"] for p in points] == ["2026-06-01", "2026-06-03"]  # 06-02 held -> gap
    by = {p["date"]: p["equity"] for p in points}
    assert by["2026-06-03"] == 1050.0  # anchor = current balance (post round-trip)
    assert by["2026-06-01"] == 1000.0  # before the +$50 round-trip
    # no negative / absurd value ever emitted
    assert all(p["equity"] > 0 for p in points)


def test_all_flat_days_all_points_exact():
    all_days = ["2026-06-01", "2026-06-02", "2026-06-03"]
    cash = {"2026-06-02": Decimal("100"), "2026-06-03": Decimal("-40")}  # div in, withdrawal
    points, skipped = _reconstruct_flat_days(all_days, cash, {}, Decimal("1060"), {})

    assert skipped == 0
    by = {p["date"]: p["equity"] for p in points}
    assert by == {"2026-06-01": 1000.0, "2026-06-02": 1100.0, "2026-06-03": 1060.0}


def test_currently_held_skips_today():
    # still holding right now -> today is a gap, not a fabricated point
    points, skipped = _reconstruct_flat_days(
        ["2026-06-03"], {}, {}, Decimal("500"), {("1025", "TSLA"): Decimal("5")}
    )
    assert points == []
    assert skipped == 1
