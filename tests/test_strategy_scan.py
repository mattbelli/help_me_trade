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

    if watchlist is not None:
        monkeypatch.setattr(
            "signals.strategy_scan._scan_symbols",
            lambda _broker_name: sorted(watchlist),
        )

    monkeypatch.setattr("signals.strategy_scan.broker_is_configured", lambda _name: True)
    monkeypatch.setattr(
        "signals.strategy_scan.connection_is_configured", lambda _conn: True
    )
    monkeypatch.setattr(
        "signals.strategy_scan.get_broker_for_account", lambda _acct: MagicMock()
    )

    snapshot = (
        {"positions": [{"symbol": s} for s in held]} if held is not None else None
    )

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
    assert candidate.rule_id is None
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
