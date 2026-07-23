"""Equal-weight watchlist index math (signals/watchlist_index.py)."""

import pytest

from signals.watchlist_index import equal_weight_index


def test_two_symbols_daily_rebalanced_average():
    points, per_symbol = equal_weight_index(
        {
            # A: +10% then +10%; B: flat then -10%.
            "A": [("2026-07-01", 100.0), ("2026-07-02", 110.0), ("2026-07-03", 121.0)],
            "B": [("2026-07-01", 50.0), ("2026-07-02", 50.0), ("2026-07-03", 45.0)],
        }
    )
    assert [d for d, _ in points] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    # Day 1 = base 0. Day 2 mean(+10%, 0%) = +5%. Day 3 mean(+10%, -10%) = 0% → stays +5%.
    assert points[0][1] == pytest.approx(0.0)
    assert points[1][1] == pytest.approx(5.0)
    assert points[2][1] == pytest.approx(5.0)
    assert per_symbol["A"] == pytest.approx(21.0)
    assert per_symbol["B"] == pytest.approx(-10.0)


def test_single_symbol_tracks_its_own_return():
    points, per_symbol = equal_weight_index(
        {"A": [("2026-07-01", 200.0), ("2026-07-02", 190.0), ("2026-07-03", 209.0)]}
    )
    assert points[-1][1] == pytest.approx(4.5)
    assert per_symbol["A"] == pytest.approx(4.5)


def test_missing_day_return_lands_on_reappearance():
    points, per_symbol = equal_weight_index(
        {
            "A": [("2026-07-01", 100.0), ("2026-07-02", 100.0), ("2026-07-03", 100.0)],
            # B skips 07-02; its full 2-day +20% enters 07-03's average.
            "B": [("2026-07-01", 10.0), ("2026-07-03", 12.0)],
        }
    )
    assert points[1][1] == pytest.approx(0.0)  # only A trades on 07-02
    assert points[2][1] == pytest.approx(10.0)  # mean(0%, +20%)
    assert per_symbol["B"] == pytest.approx(20.0)


def test_ragged_start_joins_index_late():
    points, _ = equal_weight_index(
        {
            "A": [("2026-07-01", 100.0), ("2026-07-02", 101.0), ("2026-07-03", 102.01)],
            # B's first close is 07-02 — it seeds there and first contributes 07-03.
            "B": [("2026-07-02", 20.0), ("2026-07-03", 22.0)],
        }
    )
    assert points[1][1] == pytest.approx(1.0)
    # 07-03: mean(+1%, +10%) = +5.5% on top of +1%.
    assert points[2][1] == pytest.approx((1.01 * 1.055 - 1.0) * 100.0)


def test_non_positive_closes_dropped_and_empty_input():
    points, per_symbol = equal_weight_index({"A": [("2026-07-01", 0.0), ("2026-07-02", -5.0)]})
    assert points == []
    assert per_symbol == {}
    assert equal_weight_index({}) == ([], {})


def test_symbol_with_one_close_never_moves_the_index():
    points, per_symbol = equal_weight_index(
        {
            "A": [("2026-07-01", 100.0), ("2026-07-02", 110.0)],
            "B": [("2026-07-01", 50.0)],
        }
    )
    assert points[-1][1] == pytest.approx(10.0)
    assert "B" not in per_symbol
