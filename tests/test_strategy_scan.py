"""Live dip-buy strategy scan — registry-driven predicate and propose_trade routing."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from brokers.alpaca import StockBar
from config import settings as settings_mod
from db.multi_account import migrate_multi_account
from db.strategies import init_strategy_tables, seed_default_strategies, update_strategy
from signals.strategy_scan import run_strategy_scan
from signals.trade_context import MIN_BARS_FOR_CONTEXT, DatedBar

PASS_SYMBOL = "AAPL"
FAIL_SYMBOL = "MSFT"
SPY_SYMBOL = "SPY"


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


def _dated_bars_to_stockbars(bars: list[DatedBar]) -> list[StockBar]:
    return [
        StockBar(
            time=f"{bar['d']}T00:00:00Z",
            open=bar["o"],
            high=bar["h"],
            low=bar["l"],
            close=bar["c"],
            volume=bar["v"],
        )
        for bar in bars
    ]


def _init_registry_db(tmp_path: Path) -> Path:
    from db.order_executions import init_execution_tables
    from db.paper_orders import init_paper_order_tables
    from db.sqlite import init_db
    from db.trades import init_trade_tables

    db = tmp_path / "strategy_scan.db"
    init_db(db)
    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    init_strategy_tables(db)
    seed_default_strategies(db_path=db)
    return db


def _patch_scan_env(
    monkeypatch,
    tmp_path: Path,
    *,
    strategy_enabled: bool = False,
    notional: float = 500,
    notional_pct: float | None = None,
    equity: float | None = None,
    watchlist: list[str] | None = None,
    held: list[str] | None = None,
    account_id: str | None = None,
) -> tuple[AsyncMock, Path]:
    db = _init_registry_db(tmp_path)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    from db.accounts import default_execution_account

    bound_account = account_id or default_execution_account(db_path=db)["id"]
    update_strategy(
        "dip_buy",
        enabled=strategy_enabled,
        notional_usd=notional,
        account_id=bound_account,
        db_path=db,
    )
    if notional_pct is not None:
        update_strategy("dip_buy", notional_pct=notional_pct, db_path=db)

    if watchlist is not None:
        monkeypatch.setattr(
            "signals.strategy_scan.resolve_universe_spec",
            lambda _spec: sorted(watchlist),
        )

    monkeypatch.setattr("signals.strategy_scan.broker_is_configured", lambda _name: True)
    monkeypatch.setattr(
        "signals.strategy_scan.connection_is_configured", lambda _conn: True
    )
    monkeypatch.setattr(
        "signals.strategy_scan.get_broker_for_account", lambda _acct: MagicMock()
    )

    snapshot: dict | None = (
        {"positions": [{"symbol": s} for s in held]} if held is not None else None
    )
    if equity is not None:
        snapshot = {
            **(snapshot or {"positions": []}),
            "account": {"portfolio_value": equity},
        }

    def _snapshot(_broker, *, account_id=None, db_path=None):
        return snapshot

    monkeypatch.setattr("signals.strategy_scan.get_latest_snapshot", _snapshot)

    propose_mock = AsyncMock(return_value={"status": "proposed"})
    monkeypatch.setattr("signals.strategy_scan.propose_trade", propose_mock)
    return propose_mock, db


def _passing_bar_payload() -> dict[str, list[StockBar]]:
    stock_bars, _ = _healthy_stock_bars(drop_pct=-6.0)
    spy_bars, _ = _spy_bars(drop_pct=-2.0)
    return {
        PASS_SYMBOL: _dated_bars_to_stockbars(stock_bars),
        SPY_SYMBOL: _dated_bars_to_stockbars(spy_bars),
    }


@pytest.mark.asyncio
async def test_disabled_returns_empty_without_fetch(monkeypatch, tmp_path: Path) -> None:
    propose_mock, db = _patch_scan_env(monkeypatch, tmp_path, strategy_enabled=False)
    fetch_mock = AsyncMock(return_value={})
    monkeypatch.setattr("signals.strategy_scan._fetch_stock_bars", fetch_mock)

    result = await run_strategy_scan(db_path=db)

    assert result == []
    fetch_mock.assert_not_called()
    propose_mock.assert_not_called()


@pytest.mark.asyncio
async def test_predicate_pass_proposes_trade(monkeypatch, tmp_path: Path) -> None:
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        notional=750,
        watchlist=[PASS_SYMBOL],
    )
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_passing_bar_payload()),
    )

    result = await run_strategy_scan(db_path=db)

    assert len(result) == 1
    propose_mock.assert_awaited_once()
    candidate = propose_mock.await_args.args[0]
    assert candidate.symbol == PASS_SYMBOL
    assert candidate.side == "buy"
    assert candidate.source == "strategy:dip_buy"
    assert candidate.notional_usd == Decimal("750")
    assert candidate.account_id is not None
    assert candidate.rule_id == "dip_buy"
    assert "Dip-Buy:" in (candidate.rationale or "")


@pytest.mark.asyncio
async def test_predicate_fail_no_propose(monkeypatch, tmp_path: Path) -> None:
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        watchlist=[PASS_SYMBOL],
    )
    stock_bars, _ = _healthy_stock_bars(drop_pct=-6.0)
    spy_bars, _ = _spy_bars(drop_pct=1.0)
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(
            return_value={
                PASS_SYMBOL: _dated_bars_to_stockbars(stock_bars),
                SPY_SYMBOL: _dated_bars_to_stockbars(spy_bars),
            }
        ),
    )

    result = await run_strategy_scan(db_path=db)

    assert result == []
    propose_mock.assert_not_called()


@pytest.mark.asyncio
async def test_insufficient_spy_bars_no_crash(monkeypatch, tmp_path: Path) -> None:
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        watchlist=[PASS_SYMBOL],
    )
    stock_bars, _ = _healthy_stock_bars(drop_pct=-6.0)
    short_spy = _dated_bars_from_closes(
        [450.0] * (MIN_BARS_FOR_CONTEXT - 1),
        date.fromisoformat(stock_bars[0]["d"]),
    )
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(
            return_value={
                PASS_SYMBOL: _dated_bars_to_stockbars(stock_bars),
                SPY_SYMBOL: _dated_bars_to_stockbars(short_spy),
            }
        ),
    )

    result = await run_strategy_scan(db_path=db)

    assert result == []
    propose_mock.assert_not_called()


@pytest.mark.asyncio
async def test_date_misalignment_skipped(monkeypatch, tmp_path: Path) -> None:
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        watchlist=[PASS_SYMBOL],
    )
    stock_bars, _ = _healthy_stock_bars(drop_pct=-6.0)
    spy_bars, _ = _spy_bars(drop_pct=-2.0)
    stale_stock = list(stock_bars)
    stale_stock[-1] = {**stale_stock[-1], "d": stale_stock[-2]["d"]}
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(
            return_value={
                PASS_SYMBOL: _dated_bars_to_stockbars(stale_stock),
                SPY_SYMBOL: _dated_bars_to_stockbars(spy_bars),
            }
        ),
    )

    result = await run_strategy_scan(db_path=db)

    assert result == []
    propose_mock.assert_not_called()


@pytest.mark.asyncio
async def test_one_bad_symbol_does_not_kill_scan(monkeypatch, tmp_path: Path) -> None:
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        watchlist=[PASS_SYMBOL, FAIL_SYMBOL],
    )
    payload = _passing_bar_payload()
    payload[FAIL_SYMBOL] = [StockBar(time="bad", open=1, high=1, low=1, close=1, volume=1)]
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=payload),
    )

    result = await run_strategy_scan(db_path=db)

    assert len(result) == 1
    propose_mock.assert_awaited_once()
    assert propose_mock.await_args.args[0].symbol == PASS_SYMBOL


@pytest.mark.asyncio
async def test_spy_never_proposed_even_in_watchlist(monkeypatch, tmp_path: Path) -> None:
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        watchlist=[SPY_SYMBOL, PASS_SYMBOL],
    )
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_passing_bar_payload()),
    )

    await run_strategy_scan(db_path=db)

    assert propose_mock.await_count == 1
    assert propose_mock.await_args.args[0].symbol == PASS_SYMBOL


@pytest.mark.asyncio
async def test_held_symbol_not_reproposed(monkeypatch, tmp_path: Path) -> None:
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        watchlist=[PASS_SYMBOL],
        held=[PASS_SYMBOL],
    )
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_passing_bar_payload()),
    )

    result = await run_strategy_scan(db_path=db)

    assert result == []
    propose_mock.assert_not_called()


@pytest.mark.asyncio
async def test_zero_notional_skips_without_fetch(monkeypatch, tmp_path: Path) -> None:
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        notional=0,
        watchlist=[PASS_SYMBOL],
    )
    fetch_mock = AsyncMock(return_value=_passing_bar_payload())
    monkeypatch.setattr("signals.strategy_scan._fetch_stock_bars", fetch_mock)

    result = await run_strategy_scan(db_path=db)

    assert result == []
    fetch_mock.assert_not_called()
    propose_mock.assert_not_called()


@pytest.mark.asyncio
async def test_non_exec_account_skipped(monkeypatch, tmp_path: Path) -> None:
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        watchlist=[PASS_SYMBOL],
        account_id="acct_missing",
    )
    fetch_mock = AsyncMock(return_value=_passing_bar_payload())
    monkeypatch.setattr("signals.strategy_scan._fetch_stock_bars", fetch_mock)

    result = await run_strategy_scan(db_path=db)

    assert result == []
    fetch_mock.assert_not_called()
    propose_mock.assert_not_called()


def _multi_passing_payload(symbols: list[str]) -> dict[str, list[StockBar]]:
    spy_bars, _ = _spy_bars(drop_pct=-2.0)
    payload: dict[str, list[StockBar]] = {
        SPY_SYMBOL: _dated_bars_to_stockbars(spy_bars)
    }
    for sym in symbols:
        stock_bars, _ = _healthy_stock_bars(drop_pct=-6.0)
        payload[sym] = _dated_bars_to_stockbars(stock_bars)
    return payload


@pytest.mark.asyncio
async def test_max_concurrent_caps_proposals(monkeypatch, tmp_path: Path) -> None:
    syms = ["AAA", "BBB", "CCC", "DDD"]
    propose_mock, db = _patch_scan_env(
        monkeypatch, tmp_path, strategy_enabled=True, watchlist=syms
    )
    update_strategy("dip_buy", max_concurrent_positions=2, db_path=db)
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_multi_passing_payload(syms)),
    )

    result = await run_strategy_scan(db_path=db)

    # 4 symbols pass the predicate, but the cap stops proposing after 2.
    assert len(result) == 2
    assert propose_mock.await_count == 2


@pytest.mark.asyncio
async def test_max_deployed_caps_proposals(monkeypatch, tmp_path: Path) -> None:
    syms = ["AAA", "BBB", "CCC", "DDD"]
    propose_mock, db = _patch_scan_env(
        monkeypatch, tmp_path, strategy_enabled=True, notional=500, watchlist=syms
    )
    # Budget fits two $500 orders (1000) but not a third (1500 > 1200).
    update_strategy("dip_buy", max_deployed_usd=1200, db_path=db)
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_multi_passing_payload(syms)),
    )

    result = await run_strategy_scan(db_path=db)

    assert len(result) == 2
    assert propose_mock.await_count == 2


@pytest.mark.asyncio
async def test_open_positions_count_toward_concurrency_cap(
    monkeypatch, tmp_path: Path
) -> None:
    syms = ["AAA", "BBB", "CCC"]
    # Two already-open positions in OTHER symbols (not in the watchlist, so they
    # don't get skipped as held candidates) — they still consume the cap.
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        watchlist=syms,
        held=["ZZZ", "YYY"],
    )
    update_strategy("dip_buy", max_concurrent_positions=3, db_path=db)
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_multi_passing_payload(syms)),
    )

    result = await run_strategy_scan(db_path=db)

    # 2 open + cap 3 ⇒ only 1 more may be proposed.
    assert len(result) == 1
    assert propose_mock.await_count == 1


@pytest.mark.asyncio
async def test_no_caps_proposes_all(monkeypatch, tmp_path: Path) -> None:
    syms = ["AAA", "BBB", "CCC", "DDD"]
    propose_mock, db = _patch_scan_env(
        monkeypatch, tmp_path, strategy_enabled=True, watchlist=syms
    )
    # caps left at NULL ⇒ no limit, every passing symbol proposes.
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_multi_passing_payload(syms)),
    )

    result = await run_strategy_scan(db_path=db)

    assert len(result) == len(syms)
    assert propose_mock.await_count == len(syms)


def test_dip_strategy_settings_defaults(tmp_path: Path, monkeypatch) -> None:
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    cfg = settings_mod.trading_config()

    assert cfg["dip_strategy_enabled"] is False
    assert cfg["dip_strategy_notional_usd"] == 500


def test_dip_strategy_settings_round_trip(tmp_path: Path, monkeypatch) -> None:
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    settings_mod.apply_settings_patch(
        {
            "trading": {
                "dip_strategy_enabled": True,
                "dip_strategy_notional_usd": 1200,
            }
        }
    )

    cfg = settings_mod.trading_config()
    assert cfg["dip_strategy_enabled"] is True
    assert cfg["dip_strategy_notional_usd"] == 1200

    settings_mod.apply_settings_patch({"trading": {"dip_strategy_notional_usd": -50}})
    cfg = settings_mod.trading_config()
    assert cfg["dip_strategy_notional_usd"] == 0.0


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


def _earnings_passing_bars() -> tuple[list[DatedBar], list[str]]:
    bars = _weekday_bars(MIN_BARS_FOR_CONTEXT + 20, date(2026, 1, 5))
    prior_idx = len(bars) - 45
    bars[prior_idx - 1]["c"] = 100.0
    bars[prior_idx + 1]["c"] = 106.0
    idx = len(bars) - 1
    last_e = bars[prior_idx]["d"]
    today = date.fromisoformat(bars[idx]["d"])
    next_e = _date_n_trading_days_after(today, 10)
    return bars, [last_e, next_e]


@pytest.mark.asyncio
async def test_earnings_strategy_proposes_when_predicate_passes(
    monkeypatch, tmp_path: Path
) -> None:
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=False,
        watchlist=[PASS_SYMBOL],
    )
    from db.accounts import default_execution_account

    bound_account = default_execution_account(db_path=db)["id"]
    update_strategy(
        "dip_buy",
        enabled=False,
        db_path=db,
    )
    update_strategy(
        "earnings_drift",
        enabled=True,
        account_id=bound_account,
        notional_usd=600,
        universe=PASS_SYMBOL,
        db_path=db,
    )

    sym_bars, earnings_dates = _earnings_passing_bars()
    last_date = date.fromisoformat(sym_bars[-1]["d"])
    spy_closes = [450.0] * (MIN_BARS_FOR_CONTEXT + 4)
    spy_bars = _dated_bars_from_closes(spy_closes, last_date - timedelta(days=len(spy_closes) - 1))
    monkeypatch.setattr(
        "signals.strategy_kinds.earnings_dates_for",
        lambda _sym, db_path=None: earnings_dates,
    )
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(
            return_value={
                PASS_SYMBOL: _dated_bars_to_stockbars(sym_bars),
                SPY_SYMBOL: _dated_bars_to_stockbars(spy_bars),
            }
        ),
    )

    result = await run_strategy_scan(db_path=db)

    assert len(result) == 1
    propose_mock.assert_awaited_once()
    candidate = propose_mock.await_args.args[0]
    assert candidate.symbol == PASS_SYMBOL
    assert candidate.source == "strategy:earnings_drift"
    assert candidate.rule_id == "earnings_drift"
    assert candidate.notional_usd == Decimal("600")
    assert "Earnings Drift:" in (candidate.rationale or "")
    assert "pre-earnings drift" in (candidate.rationale or "")


@pytest.mark.asyncio
async def test_auto_entry_dispatches_when_paper(monkeypatch, tmp_path: Path) -> None:
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        watchlist=[PASS_SYMBOL],
    )
    update_strategy("dip_buy", entry_mode="auto", db_path=db)
    auto_mock = AsyncMock(return_value={"status": "executed", "execution_id": "exec_1"})
    monkeypatch.setattr("trading.proposal_intake.auto_execute_entry", auto_mock)
    monkeypatch.setattr(
        "signals.strategy_scan.trading_config",
        lambda: {"execution_mode": "paper"},
    )
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_passing_bar_payload()),
    )

    result = await run_strategy_scan(db_path=db)

    assert len(result) == 1
    assert result[0]["status"] == "executed"
    auto_mock.assert_awaited_once()
    propose_mock.assert_not_called()


@pytest.mark.asyncio
async def test_auto_entry_reverts_to_propose_under_live(monkeypatch, tmp_path: Path) -> None:
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        watchlist=[PASS_SYMBOL],
    )
    update_strategy("dip_buy", entry_mode="auto", db_path=db)
    auto_mock = AsyncMock()
    monkeypatch.setattr("trading.proposal_intake.auto_execute_entry", auto_mock)
    monkeypatch.setattr(
        "signals.strategy_scan.trading_config",
        lambda: {"execution_mode": "live"},
    )
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_passing_bar_payload()),
    )

    result = await run_strategy_scan(db_path=db)

    assert len(result) == 1
    propose_mock.assert_awaited_once()
    auto_mock.assert_not_called()



def test_inflight_auto_exposure_counts_cross_symbol_dedups_held(tmp_path) -> None:
    """In-flight auto-entries feed the concurrency cap; held + other-strategy rows excluded."""
    from decimal import Decimal

    from db.order_executions import create_execution_record
    from signals.strategy_scan import _inflight_auto_exposure

    db = tmp_path / "inflight.db"
    for sym in ("AAPL", "MSFT"):
        create_execution_record(
            execution_id=f"ex_{sym}",
            proposal_id="auto_entry|dip_buy|2026-06-26T00:00:00+00:00",
            session_id=None,
            symbol=sym,
            side="buy",
            qty=Decimal("3"),
            order_type="market",
            limit_price=None,
            execution_mode="paper",
            status="submitted",
            broker="alpaca",
            account_id="acc1",
            db_path=db,
        )
    # a different strategy's auto-entry must NOT count toward dip_buy's cap
    create_execution_record(
        execution_id="ex_other",
        proposal_id="auto_entry|earnings_drift|2026-06-26T00:00:00+00:00",
        session_id=None,
        symbol="NVDA",
        side="buy",
        qty=Decimal("1"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="submitted",
        broker="alpaca",
        account_id="acc1",
        db_path=db,
    )

    # MSFT is already held (filled + in snapshot) → excluded to avoid double-count.
    count, value = _inflight_auto_exposure(
        "acc1",
        "dip_buy",
        held={"MSFT"},
        prices={"AAPL": 100.0},
        fallback_notional=500.0,
        within_seconds=900,
        db_path=db,
    )
    assert count == 1  # AAPL only (MSFT held, NVDA is earnings_drift)
    assert value == 300.0  # 3 * 100


@pytest.mark.asyncio
async def test_notional_pct_sizes_off_account_equity(monkeypatch, tmp_path: Path) -> None:
    """notional_pct set => order is pct of the snapshot's portfolio_value, not notional_usd."""
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        notional=750,  # must be IGNORED once a percent is configured
        notional_pct=2.0,
        equity=100_000.0,
        watchlist=[PASS_SYMBOL],
    )
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_passing_bar_payload()),
    )

    result = await run_strategy_scan(db_path=db)

    assert len(result) == 1
    candidate = propose_mock.await_args.args[0]
    assert candidate.notional_usd == Decimal("2000")  # 2% of $100k, not $750


@pytest.mark.asyncio
async def test_notional_pct_compounds_with_equity(monkeypatch, tmp_path: Path) -> None:
    """Same 2% config sizes bigger on a bigger book — the point of percent sizing."""
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        notional_pct=2.0,
        equity=250_000.0,
        watchlist=[PASS_SYMBOL],
    )
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_passing_bar_payload()),
    )

    await run_strategy_scan(db_path=db)

    assert propose_mock.await_args.args[0].notional_usd == Decimal("5000")


@pytest.mark.asyncio
async def test_notional_pct_without_equity_fails_closed(monkeypatch, tmp_path: Path) -> None:
    """FAIL-CLOSED: percent sizing with no snapshot equity SKIPS — never falls back to $."""
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        notional=750,
        notional_pct=2.0,
        equity=None,  # snapshot carries no account/portfolio_value
        watchlist=[PASS_SYMBOL],
    )
    fetch_mock = AsyncMock(return_value=_passing_bar_payload())
    monkeypatch.setattr("signals.strategy_scan._fetch_stock_bars", fetch_mock)

    result = await run_strategy_scan(db_path=db)

    assert result == []
    propose_mock.assert_not_called()  # NOT sized at the stale $750
    fetch_mock.assert_not_called()  # skipped before spending a broker fetch


@pytest.mark.asyncio
async def test_notional_pct_zero_equity_fails_closed(monkeypatch, tmp_path: Path) -> None:
    """A zero/garbage portfolio_value is unusable equity — skip, don't size off $."""
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        notional=750,
        notional_pct=2.0,
        equity=0.0,
        watchlist=[PASS_SYMBOL],
    )
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_passing_bar_payload()),
    )

    result = await run_strategy_scan(db_path=db)

    assert result == []
    propose_mock.assert_not_called()


@pytest.mark.asyncio
async def test_notional_usd_still_used_when_pct_unset(monkeypatch, tmp_path: Path) -> None:
    """Unchanged behaviour: no percent configured => the static dollar amount, even
    when the snapshot has equity available."""
    propose_mock, db = _patch_scan_env(
        monkeypatch,
        tmp_path,
        strategy_enabled=True,
        notional=750,
        equity=100_000.0,
        watchlist=[PASS_SYMBOL],
    )
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_passing_bar_payload()),
    )

    await run_strategy_scan(db_path=db)

    assert propose_mock.await_args.args[0].notional_usd == Decimal("750")


# --- vol-targeted sizing: two switches, and the master one is shadow-safe ---


def _patch_vol_config(monkeypatch, *, enabled: bool, profile: str = "balanced") -> None:
    monkeypatch.setattr(
        "signals.strategy_scan.trading_config",
        lambda: {
            "execution_mode": "dry_run",
            "volatility_enabled": enabled,
            "vol_target_profile": profile,
        },
    )


@pytest.mark.asyncio
async def test_vol_master_switch_off_leaves_notional_untouched(
    monkeypatch, tmp_path: Path
) -> None:
    propose_mock, db = _patch_scan_env(
        monkeypatch, tmp_path, strategy_enabled=True, notional=750,
        watchlist=[PASS_SYMBOL],
    )
    update_strategy("dip_buy", vol_target=True, db_path=db)  # opted in, master OFF
    _patch_vol_config(monkeypatch, enabled=False)
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_passing_bar_payload()),
    )

    await run_strategy_scan(db_path=db)

    assert propose_mock.await_args.args[0].notional_usd == Decimal("750")


@pytest.mark.asyncio
async def test_vol_master_switch_alone_is_shadow_only(
    monkeypatch, tmp_path: Path
) -> None:
    """The master switch turns on the READ, never the sizing — that is what makes
    it safe to flip on in production before any strategy has opted in."""
    propose_mock, db = _patch_scan_env(
        monkeypatch, tmp_path, strategy_enabled=True, notional=750,
        watchlist=[PASS_SYMBOL],
    )
    _patch_vol_config(monkeypatch, enabled=True)  # strategy vol_target stays 0
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_passing_bar_payload()),
    )

    await run_strategy_scan(db_path=db)

    assert propose_mock.await_args.args[0].notional_usd == Decimal("750")


@pytest.mark.asyncio
async def test_vol_applies_only_with_both_switches_on(
    monkeypatch, tmp_path: Path
) -> None:
    propose_mock, db = _patch_scan_env(
        monkeypatch, tmp_path, strategy_enabled=True, notional=750,
        watchlist=[PASS_SYMBOL],
    )
    update_strategy("dip_buy", vol_target=True, db_path=db)
    _patch_vol_config(monkeypatch, enabled=True)
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_passing_bar_payload()),
    )

    await run_strategy_scan(db_path=db)

    sized = propose_mock.await_args.args[0].notional_usd
    assert sized != Decimal("750"), "vol_target on + master on must resize"
    # Whatever the fixture's volatility, the clamp bounds the move both ways.
    assert Decimal("750") * Decimal("0.25") <= sized <= Decimal("750") * Decimal("1.5")


@pytest.mark.asyncio
async def test_vol_sizing_survives_an_unusable_forecast(
    monkeypatch, tmp_path: Path
) -> None:
    """No forecast => identity multiplier => the strategy's normal size, not zero."""
    propose_mock, db = _patch_scan_env(
        monkeypatch, tmp_path, strategy_enabled=True, notional=750,
        watchlist=[PASS_SYMBOL],
    )
    update_strategy("dip_buy", vol_target=True, db_path=db)
    _patch_vol_config(monkeypatch, enabled=True)
    monkeypatch.setattr("signals.strategy_scan.forecast_vol", lambda *a, **k: None)
    monkeypatch.setattr(
        "signals.strategy_scan._fetch_stock_bars",
        AsyncMock(return_value=_passing_bar_payload()),
    )

    await run_strategy_scan(db_path=db)

    assert propose_mock.await_args.args[0].notional_usd == Decimal("750")
