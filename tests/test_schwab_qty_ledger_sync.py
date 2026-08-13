"""Qty-change on a Schwab snapshot kicks the trade-ledger sync (calendar fill)."""

from brokers import schwab_accounts as sa
from brokers.schwab_accounts import (
    maybe_sync_ledger_on_qty_change,
    position_qty_map,
    reset_qty_sync_state,
)


def _acct(aid: str, positions: list[tuple[str, float]], *, px: float = 100.0) -> dict:
    return {
        "id": aid,
        "positions": [
            {"symbol": sym, "qty": qty, "market_value": qty * px}
            for sym, qty in positions
        ],
    }


def _clear_snapshot_cache() -> None:
    with sa._cache_lock:
        sa._cache.update(ts=0.0, data=None)


def setup_function() -> None:
    reset_qty_sync_state()
    _clear_snapshot_cache()


def test_qty_map_is_account_and_symbol_only() -> None:
    accounts = [
        _acct("schwab_1111", [("TSLA", 200.0), ("AAPL", 10.0)], px=340.0),
        _acct("schwab_2222", [("TSLA", 5.0)], px=341.0),
    ]
    assert position_qty_map(accounts) == {
        ("schwab_1111", "TSLA"): 200.0,
        ("schwab_1111", "AAPL"): 10.0,
        ("schwab_2222", "TSLA"): 5.0,
    }


def test_first_snapshot_is_baseline_no_sync() -> None:
    calls: list[int] = []
    started = maybe_sync_ledger_on_qty_change(
        [_acct("schwab_1111", [("TSLA", 200.0)])],
        run_sync=lambda: calls.append(1) or {"inserted": 0},
    )
    assert started is False
    assert calls == []


def test_price_only_move_does_not_sync() -> None:
    accounts_a = [_acct("schwab_1111", [("TSLA", 200.0)], px=340.0)]
    accounts_b = [_acct("schwab_1111", [("TSLA", 200.0)], px=350.0)]
    maybe_sync_ledger_on_qty_change(accounts_a, run_sync=lambda: {"inserted": 0})
    calls: list[int] = []
    started = maybe_sync_ledger_on_qty_change(
        accounts_b,
        run_sync=lambda: calls.append(1) or {"inserted": 0},
    )
    assert started is False
    assert calls == []


def test_partial_sell_syncs() -> None:
    maybe_sync_ledger_on_qty_change(
        [_acct("schwab_1111", [("TSLA", 200.0)])],
        run_sync=lambda: {"inserted": 0},
    )
    calls: list[int] = []
    started = maybe_sync_ledger_on_qty_change(
        [_acct("schwab_1111", [("TSLA", 150.0)])],
        run_sync=lambda: calls.append(1) or {"inserted": 2},
    )
    assert started is True
    assert calls == [1]


def test_flat_position_is_a_qty_change() -> None:
    maybe_sync_ledger_on_qty_change(
        [_acct("schwab_1111", [("TSLA", 50.0), ("AAPL", 10.0)])],
        run_sync=lambda: {"inserted": 0},
    )
    calls: list[int] = []
    maybe_sync_ledger_on_qty_change(
        [_acct("schwab_1111", [("AAPL", 10.0)])],
        run_sync=lambda: calls.append(1) or {"inserted": 1},
    )
    assert calls == [1]


def test_new_position_syncs() -> None:
    maybe_sync_ledger_on_qty_change(
        [_acct("schwab_1111", [("AAPL", 10.0)])],
        run_sync=lambda: {"inserted": 0},
    )
    calls: list[int] = []
    maybe_sync_ledger_on_qty_change(
        [_acct("schwab_1111", [("AAPL", 10.0), ("TSLA", 50.0)])],
        run_sync=lambda: calls.append(1) or {"inserted": 1},
    )
    assert calls == [1]


def test_skipped_sync_retries_next_observe() -> None:
    maybe_sync_ledger_on_qty_change(
        [_acct("schwab_1111", [("TSLA", 200.0)])],
        run_sync=lambda: {"inserted": 0},
    )
    calls: list[str] = []
    maybe_sync_ledger_on_qty_change(
        [_acct("schwab_1111", [("TSLA", 150.0)])],
        run_sync=lambda: calls.append("skip") or {"skipped": "already running"},
    )
    maybe_sync_ledger_on_qty_change(
        [_acct("schwab_1111", [("TSLA", 150.0)])],
        run_sync=lambda: calls.append("ok") or {"inserted": 2},
    )
    assert calls == ["skip", "ok"]
    # After a completed sync, the same qty is quiet.
    maybe_sync_ledger_on_qty_change(
        [_acct("schwab_1111", [("TSLA", 150.0)])],
        run_sync=lambda: calls.append("again") or {"inserted": 0},
    )
    assert calls == ["skip", "ok"]


def test_fresh_fetch_observes_qty(monkeypatch) -> None:
    seen: list[list] = []
    monkeypatch.setattr(sa, "token_exists", lambda: True)
    monkeypatch.setattr(
        sa,
        "_fetch_all",
        lambda: {"accounts": [_acct("schwab_1111", [("TSLA", 200.0)])], "totals": {}},
    )
    monkeypatch.setattr(
        sa,
        "maybe_sync_ledger_on_qty_change",
        lambda accounts: seen.append(accounts) or False,
    )
    monkeypatch.setattr("db.schwab_history.record_schwab_daily", lambda *_a, **_k: None)
    sa.get_schwab_accounts_sync(force=True)
    assert len(seen) == 1
    assert seen[0][0]["id"] == "schwab_1111"


def test_cached_fetch_does_not_reobserve(monkeypatch) -> None:
    seen: list[int] = []
    monkeypatch.setattr(sa, "token_exists", lambda: True)
    monkeypatch.setattr(
        sa,
        "_fetch_all",
        lambda: {"accounts": [_acct("schwab_1111", [("TSLA", 200.0)])], "totals": {}},
    )
    monkeypatch.setattr(
        sa,
        "maybe_sync_ledger_on_qty_change",
        lambda _accounts: seen.append(1) or False,
    )
    monkeypatch.setattr("db.schwab_history.record_schwab_daily", lambda *_a, **_k: None)
    sa.get_schwab_accounts_sync(force=True)
    sa.get_schwab_accounts_sync(force=False)
    assert seen == [1]
