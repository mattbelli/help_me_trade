"""Live session-node strategy wiring tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from brokers.alpaca import StockBar
from db.ai_tables import create_rule, get_rule, update_rule
from db.managed_positions import init_managed_position_tables, record_open_position
from db.session_node import init_session_node_tables
from db.strategies import init_strategy_tables, seed_default_strategies, update_strategy
from research.intraday_bars import IntradayBar
from signals.session_node_strategy import RULE_ID, run_session_node_scan
from trading.proposals import create_phone_proposal

DAY1 = date(2024, 6, 3)
DAY2 = date(2024, 6, 4)


def _bar(
    ts: str,
    *,
    o: float | None = None,
    h: float | None = None,
    low: float | None = None,
    c: float | None = None,
    v: float = 1000.0,
) -> IntradayBar:
    close = c if c is not None else 100.0
    return {
        "t": ts,
        "o": o if o is not None else close,
        "h": h if h is not None else close,
        "l": low if low is not None else close,
        "c": close,
        "v": v,
    }


def _rth_bar(
    ny_date: date,
    ny_hour: int,
    ny_minute: int,
    *,
    price: float = 100.0,
    v: float = 1000.0,
) -> IntradayBar:
    utc_hour = ny_hour + 4
    ts = f"{ny_date.isoformat()}T{utc_hour:02d}:{ny_minute:02d}:00+00:00"
    return _bar(ts, o=price, h=price, low=price, c=price, v=v)


def _flat_session(
    ny_date: date,
    price: float,
    count: int,
    *,
    volume: float = 1000.0,
    start_minute: int = 30,
) -> list[IntradayBar]:
    bars: list[IntradayBar] = []
    for i in range(count):
        minute = start_minute + i
        hour = 9 + minute // 60
        minute = minute % 60
        bars.append(_rth_bar(ny_date, hour, minute, price=price, v=volume))
    return bars


def _reference_session(ny_date: date) -> list[IntradayBar]:
    return (
        _flat_session(ny_date, 85.0, 50, volume=10_000.0)
        + _flat_session(ny_date, 100.0, 10, volume=100_000.0, start_minute=80)
        + _flat_session(ny_date, 115.0, 3, volume=500.0, start_minute=90)
    )


def _intraday_to_stockbars(bars: list[IntradayBar]) -> list[StockBar]:
    out: list[StockBar] = []
    for bar in bars:
        ts = bar["t"].replace("+00:00", "Z")
        out.append(
            StockBar(
                time=ts,
                open=bar["o"],
                high=bar["h"],
                low=bar["l"],
                close=bar["c"],
                volume=bar["v"],
            )
        )
    return out


def _long_touch_today(val: float) -> list[IntradayBar]:
    ts = _rth_bar(DAY2, 9, 30)["t"]
    return [
        _bar(
            ts,
            o=val + 2.0,
            h=val + 2.0,
            low=val,
            c=val + 1.0,
        )
    ]


def _short_touch_today(vah: float) -> list[IntradayBar]:
    ts = _rth_bar(DAY2, 9, 31)["t"]
    return [
        _bar(
            ts,
            o=vah + 0.5,
            h=vah + 0.5,
            low=vah,
            c=vah + 0.3,
        )
    ]


def _nodes_from_prior(prior: list[IntradayBar]):
    from research.session_node_signal import compute_reference_nodes

    nodes = compute_reference_nodes(prior)
    assert nodes is not None
    return nodes


def _init_db(tmp_path: Path) -> Path:
    from db.multi_account import migrate_multi_account
    from db.paper_orders import init_paper_order_tables
    from db.sqlite import init_db

    db = tmp_path / "session_node.db"
    init_db(db)
    migrate_multi_account(db)
    init_paper_order_tables(db)
    init_session_node_tables(db)
    init_managed_position_tables(db)
    init_strategy_tables(db)
    seed_default_strategies(db_path=db)
    return db


def _patch_scan_env(
    monkeypatch,
    tmp_path: Path,
    *,
    db: Path,
    spy_bars: list[StockBar],
    strategy_enabled: bool = True,
    notional_usd: float | None = None,
) -> AsyncMock:
    from config import settings as settings_mod
    from db import base as db_base

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)

    if strategy_enabled:
        update_strategy(RULE_ID, enabled=True, db_path=db)
    else:
        update_strategy(RULE_ID, enabled=False, db_path=db)
    if notional_usd is not None:
        update_strategy(RULE_ID, notional_usd=notional_usd, db_path=db)

    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)
    monkeypatch.setattr(
        "signals.session_node_strategy.trading_config",
        lambda: {
            "agent_proposals_enabled": True,
            "max_pending_agent_proposals": 3,
        },
    )
    monkeypatch.setattr("signals.session_node_strategy.is_kill_switch_active", lambda: False)
    async def _fake_price(_sym):
        return 500.0

    monkeypatch.setattr("signals.session_node_strategy._live_market_price", _fake_price)

    from db.accounts import default_execution_account

    default_execution_account(db_path=db)
    monkeypatch.setattr(
        "signals.session_node_strategy.RiskEngine.validate",
        AsyncMock(return_value=MagicMock(passed=True)),
    )
    monkeypatch.setattr(
        "signals.session_node_strategy.get_latest_snapshot",
        lambda *_a, **_k: {
            "account": {"buying_power": 100_000.0},
            "positions": [],
        },
    )

    provider = MagicMock()
    provider.aget_stock_bars_batch = AsyncMock(return_value={"SPY": spy_bars})
    monkeypatch.setattr(
        "signals.session_node_strategy.get_market_data_provider",
        lambda: provider,
    )

    propose_mock = AsyncMock(return_value={"id": "paper_sn1"})
    monkeypatch.setattr(
        "signals.session_node_strategy.create_phone_proposal",
        propose_mock,
    )
    monkeypatch.setattr(
        "signals.session_node_strategy.LOG_PATH",
        tmp_path / "session_node_log.csv",
    )
    return propose_mock


@pytest.mark.asyncio
async def test_disabled_strategy_proposes_nothing(monkeypatch, tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    prior = _reference_session(DAY1)
    nodes = _nodes_from_prior(prior)
    today = _long_touch_today(nodes.val)
    spy_bars = _intraday_to_stockbars(prior + today)
    propose_mock = _patch_scan_env(
        monkeypatch, tmp_path, db=db, spy_bars=spy_bars, strategy_enabled=False
    )

    result = await run_session_node_scan(db_path=db)
    assert result == []
    propose_mock.assert_not_called()


@pytest.mark.asyncio
async def test_seeded_strategy_disabled_by_default(tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    from db.strategies import get_strategy

    row = get_strategy(RULE_ID, db_path=db)
    assert row is not None
    assert row["id"] == RULE_ID
    assert row["enabled"] is False


@pytest.mark.asyncio
async def test_enabled_long_trigger_proposes_once(monkeypatch, tmp_path: Path) -> None:
    from research.session_node_signal import NodeTrigger

    db = _init_db(tmp_path)
    prior = _reference_session(DAY1)
    nodes = _nodes_from_prior(prior)
    today = _long_touch_today(nodes.val)
    spy_bars = _intraday_to_stockbars(prior + today)
    propose_mock = _patch_scan_env(
        monkeypatch, tmp_path, db=db, spy_bars=spy_bars, strategy_enabled=True
    )
    trigger = NodeTrigger(
        ts=today[0]["t"],
        node_name="VAL",
        node_price=nodes.val,
        direction="long",
        target_price=nodes.poc,
        touch_price=nodes.val,
        context="test long",
    )
    monkeypatch.setattr(
        "signals.session_node_strategy.detect_node_triggers",
        lambda _bars, _nodes, **kwargs: [trigger],
    )

    first = await run_session_node_scan(db_path=db)
    second = await run_session_node_scan(db_path=db)

    assert len([x for x in first if x.get("kind") == "propose"]) == 1
    assert [x for x in second if x.get("kind") == "propose"] == []
    assert propose_mock.await_count == 1
    _args, kwargs = propose_mock.await_args
    assert kwargs["source"] == "session_node"
    assert kwargs["rule_id"] == RULE_ID
    assert kwargs["score"] is False


@pytest.mark.asyncio
async def test_short_trigger_skipped_not_proposed(monkeypatch, tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    prior = _reference_session(DAY1)
    nodes = _nodes_from_prior(prior)
    today = _short_touch_today(nodes.vah)
    spy_bars = _intraday_to_stockbars(prior + today)
    propose_mock = _patch_scan_env(
        monkeypatch, tmp_path, db=db, spy_bars=spy_bars, strategy_enabled=True
    )

    result = await run_session_node_scan(db_path=db)
    assert any(x.get("kind") == "skip_short" for x in result)
    propose_mock.assert_not_called()


@pytest.mark.asyncio
async def test_create_phone_proposal_score_false_stores_none(
    monkeypatch, tmp_path: Path
) -> None:
    from db import base as db_base
    from db import paper_orders as paper_orders_mod
    from db import sqlite as sqlite_mod
    from db.multi_account import migrate_multi_account
    from db.paper_orders import init_paper_order_tables
    from db.sqlite import init_db

    db = tmp_path / "score.db"
    init_db(db)
    migrate_multi_account(db)
    init_paper_order_tables(db)
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db)

    from db.accounts import default_execution_account

    account_id = default_execution_account(db_path=db)["id"]
    monkeypatch.setattr(
        "trading.proposals.notify",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr("signals.push.send_proposal_push", AsyncMock())

    score_called = False

    def _fail_score(*_a, **_k):
        nonlocal score_called
        score_called = True
        raise AssertionError("score_symbol must not run when score=False")

    monkeypatch.setattr("signals.decision_scorer.score_symbol", _fail_score)

    row = await create_phone_proposal(
        {"symbol": "SPY", "side": "buy", "qty": 1, "order_type": "market", "account_id": account_id},
        source="session_node",
        rule_id=RULE_ID,
        score=False,
        db_path=db,
    )
    assert row["score"] is None
    assert score_called is False


@pytest.mark.asyncio
async def test_exit_prompt_dedup(monkeypatch, tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    prior = _reference_session(DAY1)
    poc_bar = _rth_bar(DAY2, 10, 0, price=100.0)
    spy_bars = _intraday_to_stockbars(prior + [poc_bar])
    _patch_scan_env(monkeypatch, tmp_path, db=db, spy_bars=spy_bars, strategy_enabled=True)

    from db.accounts import default_execution_account

    account_id = default_execution_account(db_path=db)["id"]
    entry_ts = _rth_bar(DAY2, 9, 35)["t"]
    record_open_position(
        account_id=account_id,
        symbol="SPY",
        strategy_id=RULE_ID,
        qty="1",
        exit_mode="propose",
        params={
            "entry_ts": entry_ts,
            "entry_price": 90.0,
            "direction": "long",
            "entry_node_name": "VAL",
            "entry_node_price": 90.0,
            "nodes": {
                "session_date": DAY1.isoformat(),
                "poc": 100.0,
                "vah": 110.0,
                "val": 90.0,
                "pdh": 112.0,
                "pdl": 88.0,
            },
        },
        db_path=db,
    )

    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("signals.push.send_alert_push", push_mock)

    await run_session_node_scan(db_path=db)
    await run_session_node_scan(db_path=db)

    target_calls = [
        c
        for c in push_mock.await_args_list
        if c.kwargs.get("data", {}).get("kind") == "target_t1"
    ]
    assert len(target_calls) == 1


@pytest.mark.asyncio
async def test_notional_from_strategy_row(monkeypatch, tmp_path: Path) -> None:
    from research.session_node_signal import NodeTrigger

    db = _init_db(tmp_path)
    prior = _reference_session(DAY1)
    nodes = _nodes_from_prior(prior)
    today = _long_touch_today(nodes.val)
    spy_bars = _intraday_to_stockbars(prior + today)
    propose_mock = _patch_scan_env(
        monkeypatch,
        tmp_path,
        db=db,
        spy_bars=spy_bars,
        strategy_enabled=True,
        notional_usd=1200.0,
    )
    trigger = NodeTrigger(
        ts=today[0]["t"],
        node_name="VAL",
        node_price=nodes.val,
        direction="long",
        target_price=nodes.poc,
        touch_price=nodes.val,
        context="test long",
    )
    monkeypatch.setattr(
        "signals.session_node_strategy.detect_node_triggers",
        lambda _bars, _nodes, **kwargs: [trigger],
    )

    await run_session_node_scan(db_path=db)

    propose_mock.assert_awaited_once()
    args, _kwargs = propose_mock.await_args
    # price mocked to 500 → floor(1200/500) = 2 shares
    assert args[0]["qty"] == 2


@pytest.mark.asyncio
async def test_stale_rule_enabled_has_no_effect(monkeypatch, tmp_path: Path) -> None:
    from db.ai_tables import init_ai_tables

    db = _init_db(tmp_path)
    init_ai_tables(db)
    create_rule(
        rule_id=RULE_ID,
        name="Session-Node Reversion (SPY)",
        conditions={"type": "session_node", "symbol": "SPY"},
        action={
            "type": "trade",
            "symbol": "SPY",
            "side": "buy",
            "notional_usd": 500,
            "order_type": "market",
        },
        db_path=db,
    )
    update_rule(RULE_ID, enabled=True, db_path=db)
    update_strategy(RULE_ID, enabled=False, db_path=db)

    prior = _reference_session(DAY1)
    nodes = _nodes_from_prior(prior)
    today = _long_touch_today(nodes.val)
    spy_bars = _intraday_to_stockbars(prior + today)
    propose_mock = _patch_scan_env(
        monkeypatch, tmp_path, db=db, spy_bars=spy_bars, strategy_enabled=False
    )

    result = await run_session_node_scan(db_path=db)
    assert result == []
    propose_mock.assert_not_called()

    rule = get_rule(RULE_ID, db_path=db)
    assert rule is not None
    assert rule.get("enabled") is True


@pytest.mark.asyncio
async def test_strategy_enabled_proposes_despite_disabled_rule(
    monkeypatch, tmp_path: Path
) -> None:
    from db.ai_tables import init_ai_tables
    from research.session_node_signal import NodeTrigger

    db = _init_db(tmp_path)
    init_ai_tables(db)
    create_rule(
        rule_id=RULE_ID,
        name="Session-Node Reversion (SPY)",
        conditions={"type": "session_node", "symbol": "SPY"},
        action={
            "type": "trade",
            "symbol": "SPY",
            "side": "buy",
            "notional_usd": 500,
            "order_type": "market",
        },
        db_path=db,
    )

    prior = _reference_session(DAY1)
    nodes = _nodes_from_prior(prior)
    today = _long_touch_today(nodes.val)
    spy_bars = _intraday_to_stockbars(prior + today)
    propose_mock = _patch_scan_env(
        monkeypatch, tmp_path, db=db, spy_bars=spy_bars, strategy_enabled=True
    )
    trigger = NodeTrigger(
        ts=today[0]["t"],
        node_name="VAL",
        node_price=nodes.val,
        direction="long",
        target_price=nodes.poc,
        touch_price=nodes.val,
        context="test long",
    )
    monkeypatch.setattr(
        "signals.session_node_strategy.detect_node_triggers",
        lambda _bars, _nodes, **kwargs: [trigger],
    )

    result = await run_session_node_scan(db_path=db)
    assert len([x for x in result if x.get("kind") == "propose"]) == 1
    propose_mock.assert_awaited_once()
