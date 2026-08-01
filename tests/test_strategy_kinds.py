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
    assert strategy_kind("steepyc_dip") == "dip"
    assert strategy_kind("vix_spike") == "vix_spike"
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


def _dip_ctx_with_yc(*, yc_min_spread: float | None = 0.5) -> EntryContext:
    sym_bars, idx = _healthy_stock_bars(drop_pct=-6.0)
    spy_bars, spy_idx = _spy_bars(drop_pct=-2.0)
    params: dict = {}
    if yc_min_spread is not None:
        params["yc_min_spread"] = yc_min_spread
    return EntryContext(
        sym="AAPL",
        sym_bars=sym_bars,
        idx=idx,
        spy_bars=spy_bars,
        spy_idx=spy_idx,
        params=params,
    )


def test_dip_check_yc_gate_passes_when_steep_and_fresh(monkeypatch) -> None:
    ctx = _dip_ctx_with_yc(yc_min_spread=0.5)
    monkeypatch.setattr("signals.strategy_kinds.regime_is_fresh", lambda **_: True)
    monkeypatch.setattr(
        "signals.strategy_kinds.market_regime",
        lambda **_: {"term_spread": 0.75, "as_of": "2026-06-25"},
    )
    assert STRATEGY_KINDS["dip"].check_entry(ctx) is True


def test_dip_check_yc_gate_fails_when_spread_too_low(monkeypatch) -> None:
    ctx = _dip_ctx_with_yc(yc_min_spread=0.5)
    monkeypatch.setattr("signals.strategy_kinds.regime_is_fresh", lambda **_: True)
    monkeypatch.setattr(
        "signals.strategy_kinds.market_regime",
        lambda **_: {"term_spread": 0.5, "as_of": "2026-06-25"},
    )
    assert STRATEGY_KINDS["dip"].check_entry(ctx) is False


def test_dip_check_yc_gate_fail_closed_when_stale(monkeypatch) -> None:
    ctx = _dip_ctx_with_yc(yc_min_spread=0.5)
    monkeypatch.setattr("signals.strategy_kinds.regime_is_fresh", lambda **_: False)
    monkeypatch.setattr(
        "signals.strategy_kinds.market_regime",
        lambda **_: {"term_spread": 1.0, "as_of": "2026-06-25"},
    )
    assert STRATEGY_KINDS["dip"].check_entry(ctx) is False


def test_dip_check_yc_gate_fail_closed_when_regime_missing(monkeypatch) -> None:
    ctx = _dip_ctx_with_yc(yc_min_spread=0.5)
    monkeypatch.setattr("signals.strategy_kinds.regime_is_fresh", lambda **_: True)
    monkeypatch.setattr("signals.strategy_kinds.market_regime", lambda **_: None)
    assert STRATEGY_KINDS["dip"].check_entry(ctx) is False


def test_dip_check_without_yc_param_ignores_regime(monkeypatch) -> None:
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

    def _fail_if_called(**_kwargs):
        raise AssertionError("market_regime should not be called without yc_min_spread")

    monkeypatch.setattr("signals.strategy_kinds.regime_is_fresh", _fail_if_called)
    monkeypatch.setattr("signals.strategy_kinds.market_regime", _fail_if_called)
    assert STRATEGY_KINDS["dip"].check_entry(ctx) is True


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
            "yc_min_spread": 0.5,
            "stop_pct": -3.0,
            "unknown_key": 99,
        },
    )
    assert filtered == {"oversold_3d_max": -5.0, "yc_min_spread": 0.5}
    assert "stop_pct" not in filtered
    assert "unknown_key" not in filtered
    assert set(DIP_ENTRY_PARAM_KEYS) >= set(filtered)


def _vix_obs(*, today: float, prior: list[float]) -> list[tuple[str, float]]:
    obs: list[tuple[str, float]] = [("2026-06-24", today)]
    for i, v in enumerate(prior, start=1):
        obs.append((f"2026-06-{24 - i:02d}", v))
    return obs


def _vix_ctx(sym: str = "SPY", params: dict | None = None) -> EntryContext:
    sym_bars, idx = _healthy_stock_bars(drop_pct=-6.0)
    spy_bars, spy_idx = _spy_bars(drop_pct=-2.0)
    return EntryContext(
        sym=sym,
        sym_bars=sym_bars,
        idx=idx,
        spy_bars=spy_bars,
        spy_idx=spy_idx,
        params={"symbol": "SPY"} if params is None else params,
    )


def test_vix_spike_check_fires_on_spike_from_calm(monkeypatch) -> None:
    ctx = _vix_ctx()
    monkeypatch.setattr("signals.strategy_kinds.regime_is_fresh", lambda **_: True)
    monkeypatch.setattr(
        "signals.strategy_kinds.recent_observations",
        lambda *args, **kwargs: _vix_obs(today=30.0, prior=[18.0] * 12),
    )
    assert STRATEGY_KINDS["vix_spike"].check_entry(ctx) is True


def test_vix_spike_check_no_fire_on_untargeted_symbol(monkeypatch) -> None:
    """A VIX spike says nothing about WHICH symbol to buy. Without a symbol gate the
    predicate passed for every name in the universe and the scan auto-bought whichever
    sorted first (universe=sp500 ⇒ $10k of 'A')."""
    ctx = _vix_ctx(sym="A")  # targets SPY; scanning Agilent
    monkeypatch.setattr("signals.strategy_kinds.regime_is_fresh", lambda **_: True)
    monkeypatch.setattr(
        "signals.strategy_kinds.recent_observations",
        lambda *args, **kwargs: _vix_obs(today=30.0, prior=[18.0] * 12),
    )
    assert STRATEGY_KINDS["vix_spike"].check_entry(ctx) is False


def test_vix_spike_check_fail_closed_when_symbol_unset(monkeypatch) -> None:
    """No params["symbol"] ⇒ inert, never 'match anything'."""
    ctx = _vix_ctx(sym="SPY", params={})
    monkeypatch.setattr("signals.strategy_kinds.regime_is_fresh", lambda **_: True)
    monkeypatch.setattr(
        "signals.strategy_kinds.recent_observations",
        lambda *args, **kwargs: _vix_obs(today=30.0, prior=[18.0] * 12),
    )
    assert STRATEGY_KINDS["vix_spike"].check_entry(ctx) is False


def test_vix_spike_symbol_param_survives_entry_filter() -> None:
    """filter_entry_params strips keys not in entry_param_keys — if "symbol" were
    unregistered the gate above would silently fail closed forever."""
    filtered = filter_entry_params(
        STRATEGY_KINDS["vix_spike"], {"symbol": "SPY", "vix_spike_min": 28.0, "bogus": 1}
    )
    assert filtered == {"symbol": "SPY", "vix_spike_min": 28.0}


def test_vix_spike_check_no_fire_when_today_not_above_threshold(monkeypatch) -> None:
    ctx = _vix_ctx()
    monkeypatch.setattr("signals.strategy_kinds.regime_is_fresh", lambda **_: True)
    monkeypatch.setattr(
        "signals.strategy_kinds.recent_observations",
        lambda *args, **kwargs: _vix_obs(today=28.0, prior=[18.0] * 12),
    )
    assert STRATEGY_KINDS["vix_spike"].check_entry(ctx) is False


def test_vix_spike_check_no_fire_when_too_few_calm_days(monkeypatch) -> None:
    ctx = _vix_ctx()
    prior = [18.0] * 7 + [25.0] * 5
    monkeypatch.setattr("signals.strategy_kinds.regime_is_fresh", lambda **_: True)
    monkeypatch.setattr(
        "signals.strategy_kinds.recent_observations",
        lambda *args, **kwargs: _vix_obs(today=30.0, prior=prior),
    )
    assert STRATEGY_KINDS["vix_spike"].check_entry(ctx) is False


def test_vix_spike_check_fail_closed_when_stale(monkeypatch) -> None:
    ctx = _vix_ctx()
    monkeypatch.setattr("signals.strategy_kinds.regime_is_fresh", lambda **_: False)
    monkeypatch.setattr(
        "signals.strategy_kinds.recent_observations",
        lambda *args, **kwargs: _vix_obs(today=30.0, prior=[18.0] * 12),
    )
    assert STRATEGY_KINDS["vix_spike"].check_entry(ctx) is False


def test_vix_spike_check_fail_closed_when_history_short(monkeypatch) -> None:
    ctx = _vix_ctx()
    monkeypatch.setattr("signals.strategy_kinds.regime_is_fresh", lambda **_: True)
    monkeypatch.setattr(
        "signals.strategy_kinds.recent_observations",
        lambda *args, **kwargs: _vix_obs(today=30.0, prior=[18.0] * 10),
    )
    assert STRATEGY_KINDS["vix_spike"].check_entry(ctx) is False


def test_vix_spike_check_boundary_exactly_eight_calm(monkeypatch) -> None:
    ctx = _vix_ctx()
    prior = [18.0] * 8 + [25.0] * 4
    monkeypatch.setattr("signals.strategy_kinds.regime_is_fresh", lambda **_: True)
    monkeypatch.setattr(
        "signals.strategy_kinds.recent_observations",
        lambda *args, **kwargs: _vix_obs(today=30.0, prior=prior),
    )
    assert STRATEGY_KINDS["vix_spike"].check_entry(ctx) is True
