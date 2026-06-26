"""Strategy-kind registry — entry dispatch adapters."""

from __future__ import annotations

from datetime import date, timedelta

from signals.strategy_kinds import (
    DIP_ENTRY_PARAM_KEYS,
    EARNINGS_ENTRY_PARAM_KEYS,
    STRATEGY_KINDS,
    EntryContext,
    filter_entry_params,
    is_portfolio_kind,
    kind_spec,
    strategy_kind,
)
from signals.trade_context import MIN_BARS_FOR_CONTEXT, DatedBar


def _dated_bars_from_closes(
    close_series: list[float],
    start_date: date,
    *,
    volume: float = 1000.0,
) -> list[DatedBar]:
    bars: list[DatedBar] = []
    d = start_date
    for i, c in enumerate(close_series):
        prev_c = close_series[i - 1] if i > 0 else c
        bars.append(
            {
                "d": d.isoformat(),
                "o": prev_c if i > 0 else c,
                "h": c + 1.0,
                "l": c - 1.0,
                "c": c,
                "v": volume,
            }
        )
        d += timedelta(days=1)
    return bars


def _uptrend_closes(n: int, *, start: float = 100.0, step: float = 0.35) -> list[float]:
    return [start + i * step for i in range(n)]


def _append_three_day_dip(closes: list[float], drop_pct: float) -> list[float]:
    peak = closes[-1]
    factor = 1.0 + drop_pct / 100.0
    return closes + [peak, peak * (factor ** (1 / 3)), peak * (factor ** (2 / 3)), peak * factor]


def _healthy_stock_bars(*, drop_pct: float = -6.0) -> tuple[list[DatedBar], int]:
    base_len = MIN_BARS_FOR_CONTEXT + 4
    closes = _uptrend_closes(base_len - 4)
    closes = _append_three_day_dip(closes, drop_pct)
    bars = _dated_bars_from_closes(closes, date(2024, 1, 1))
    return bars, len(bars) - 1


def _spy_bars(*, drop_pct: float = -2.0) -> tuple[list[DatedBar], int]:
    base_len = MIN_BARS_FOR_CONTEXT + 4
    closes = [450.0] * (base_len - 4)
    closes = _append_three_day_dip(closes, drop_pct)
    bars = _dated_bars_from_closes(closes, date(2024, 1, 1))
    return bars, len(bars) - 1


def _weekday_bars(n: int, start: date, base: float = 100.0) -> list[DatedBar]:
    bars: list[DatedBar] = []
    d = start
    while len(bars) < n:
        if d.weekday() < 5:
            bars.append(
                {"d": d.isoformat(), "o": base, "h": base, "l": base, "c": base, "v": 1000.0}
            )
        d += timedelta(days=1)
    return bars


def _date_n_trading_days_after(d: date, n: int) -> str:
    cur = d
    count = 0
    while count < n:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            count += 1
    return cur.isoformat()


def _earnings_setup(
    *,
    reaction_close: float = 106.0,
    entry_td_out: int = 10,
    prior_idx: int = 35,
) -> tuple[list[DatedBar], int, list[str]]:
    bars = _weekday_bars(80, date(2026, 1, 5))
    bars[prior_idx - 1]["c"] = 100.0
    bars[prior_idx + 1]["c"] = reaction_close
    idx = len(bars) - 1
    last_e = bars[prior_idx]["d"]
    today = date.fromisoformat(bars[idx]["d"])
    next_e = _date_n_trading_days_after(today, entry_td_out)
    return bars, idx, [last_e, next_e]


def test_strategy_kind_inference() -> None:
    assert strategy_kind("dip_buy") == "dip"
    assert strategy_kind("dip_refined") == "dip"
    assert strategy_kind("earnings_drift") == "earnings"
    assert strategy_kind("momentum_sp500") == "momentum"
    assert strategy_kind("momentum_residual") == "momentum"
    assert strategy_kind("manual_x") is None


def test_is_portfolio_kind() -> None:
    assert is_portfolio_kind("momentum_sp500") is True
    assert is_portfolio_kind("momentum_residual") is True
    assert is_portfolio_kind("dip_buy") is False
    assert is_portfolio_kind("earnings_drift") is False


def test_kind_spec_entry_param_keys() -> None:
    spec = kind_spec("earnings_drift")
    assert spec is not None
    assert spec.entry_param_keys == EARNINGS_ENTRY_PARAM_KEYS


def test_dip_check_entry_passes() -> None:
    sym_bars, idx = _healthy_stock_bars(drop_pct=-6.0)
    spy_bars, spy_idx = _spy_bars(drop_pct=-2.0)
    ctx = EntryContext(
        sym="AAPL",
        sym_bars=sym_bars,
        idx=idx,
        spy_bars=spy_bars,
        spy_idx=spy_idx,
        params={},
    )
    assert STRATEGY_KINDS["dip"].check_entry(ctx) is True


def test_dip_check_entry_fails_when_market_up() -> None:
    sym_bars, idx = _healthy_stock_bars(drop_pct=-6.0)
    spy_bars, spy_idx = _spy_bars(drop_pct=1.0)
    ctx = EntryContext(
        sym="AAPL",
        sym_bars=sym_bars,
        idx=idx,
        spy_bars=spy_bars,
        spy_idx=spy_idx,
        params={},
    )
    assert STRATEGY_KINDS["dip"].check_entry(ctx) is False


def test_earnings_check_entry_passes(monkeypatch) -> None:
    sym_bars, idx, earnings = _earnings_setup(reaction_close=106.0, entry_td_out=10)
    spy_bars, spy_idx = _spy_bars(drop_pct=-2.0)
    monkeypatch.setattr(
        "signals.strategy_kinds.earnings_dates_for",
        lambda _sym, db_path=None: earnings,
    )
    ctx = EntryContext(
        sym="AAPL",
        sym_bars=sym_bars,
        idx=idx,
        spy_bars=spy_bars,
        spy_idx=spy_idx,
        params={},
    )
    assert STRATEGY_KINDS["earnings"].check_entry(ctx) is True


def test_earnings_check_entry_no_dates(monkeypatch) -> None:
    sym_bars, idx, _earnings = _earnings_setup(reaction_close=106.0, entry_td_out=10)
    spy_bars, spy_idx = _spy_bars(drop_pct=-2.0)
    monkeypatch.setattr(
        "signals.strategy_kinds.earnings_dates_for",
        lambda _sym, db_path=None: [],
    )
    ctx = EntryContext(
        sym="AAPL",
        sym_bars=sym_bars,
        idx=idx,
        spy_bars=spy_bars,
        spy_idx=spy_idx,
        params={},
    )
    assert STRATEGY_KINDS["earnings"].check_entry(ctx) is False


def test_filter_entry_params_drops_unknown_keys() -> None:
    spec = STRATEGY_KINDS["dip"]
    filtered = filter_entry_params(
        spec,
        {
            "oversold_3d_max": -5.0,
            "stop_pct": -3.0,
            "unknown_key": 99,
        },
    )
    assert filtered == {"oversold_3d_max": -5.0}
    assert "stop_pct" not in filtered
    assert "unknown_key" not in filtered
    assert set(DIP_ENTRY_PARAM_KEYS) >= set(filtered)
