"""Position change on a live Schwab snapshot → accelerated ledger refresh.

Read-only path throughout: the broker network is mocked and no test touches the
real ledger (the only sync entry point, ``trading.sync.sync_schwab``, is
replaced by a recorder).
"""

from __future__ import annotations

import threading
import time

import pytest

from trading import position_trigger as trigger


def _pos(symbol: str, qty: float, **extra) -> dict:
    """A position row shaped like ``brokers.schwab_accounts._parse_positions``."""
    row = {
        "symbol": symbol,
        "asset_type": "EQUITY",
        "qty": qty,
        "market_value": 100.0,
        "avg_price": 1.0,
        "current_price": 1.0,
        "unrealized_pl": 0.0,
        "day_pl": 0.0,
    }
    row.update(extra)
    return row


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _drain_worker() -> None:
    with trigger._lock:
        trigger._pending.clear()
    _wait_for(lambda: trigger._worker is None or not trigger._worker.is_alive())


@pytest.fixture
def calls(monkeypatch):
    """Fresh trigger state + fast timers; records every accelerated sync call."""
    _drain_worker()
    trigger._baseline.clear()
    trigger._next_allowed.clear()
    monkeypatch.setattr(trigger, "_DEBOUNCE_S", 0.01)
    monkeypatch.setattr(trigger, "_COOLDOWN_S", 0.0)

    recorded: list[list[str]] = []

    def _fake_sync(*, lookback_days: int = 365, external_ids=None) -> dict:
        recorded.append(sorted(external_ids or []))
        return {"broker": "schwab", "inserted": 0}

    monkeypatch.setattr("trading.sync.sync_schwab", _fake_sync)
    yield recorded
    _drain_worker()


# --- what counts as a change -------------------------------------------------

def test_first_snapshot_is_baseline_only(calls):
    assert trigger.observe_positions({"h1": [_pos("AAPL", 100)]}) == []
    assert not _wait_for(lambda: calls, timeout=0.2)


def test_mark_price_and_day_pl_changes_do_not_fire(calls):
    trigger.observe_positions({"h1": [_pos("AAPL", 100)]})
    queued = trigger.observe_positions(
        {
            "h1": [
                _pos(
                    "AAPL",
                    100,
                    market_value=99999.0,
                    current_price=812.5,
                    unrealized_pl=4210.0,
                    day_pl=-317.25,
                )
            ]
        }
    )
    assert queued == []
    assert not _wait_for(lambda: calls, timeout=0.2)


def test_quantity_change_fires_once(calls):
    trigger.observe_positions({"h1": [_pos("AAPL", 100)]})
    assert trigger.observe_positions({"h1": [_pos("AAPL", 50)]}) == ["h1"]
    assert _wait_for(lambda: len(calls) == 1)
    assert calls == [["h1"]]
    # steady state: the same holdings observed again must not sweep again
    assert trigger.observe_positions({"h1": [_pos("AAPL", 50)]}) == []
    assert not _wait_for(lambda: len(calls) > 1, timeout=0.2)


def test_added_position_fires(calls):
    trigger.observe_positions({"h1": [_pos("AAPL", 100)]})
    assert trigger.observe_positions(
        {"h1": [_pos("AAPL", 100), _pos("MSFT", 5)]}
    ) == ["h1"]
    assert _wait_for(lambda: calls == [["h1"]])


def test_removed_position_fires(calls):
    trigger.observe_positions({"h1": [_pos("AAPL", 100), _pos("MSFT", 5)]})
    assert trigger.observe_positions({"h1": [_pos("AAPL", 100)]}) == ["h1"]
    assert _wait_for(lambda: calls == [["h1"]])


def test_long_to_short_flip_fires(calls):
    trigger.observe_positions({"h1": [_pos("AAPL", 100)]})
    assert trigger.observe_positions({"h1": [_pos("AAPL", -100)]}) == ["h1"]
    assert _wait_for(lambda: calls == [["h1"]])


def test_symbol_case_and_quantity_formatting_are_not_changes(calls):
    trigger.observe_positions({"h1": [_pos("AAPL", 100)]})
    assert trigger.observe_positions({"h1": [_pos(" aapl ", 100.0)]}) == []
    # Schwab can split one holding across rows — the same net qty is no change.
    assert trigger.observe_positions(
        {"h1": [_pos("AAPL", 60), _pos("AAPL", 40.0)]}
    ) == []
    assert not _wait_for(lambda: calls, timeout=0.2)


def test_only_the_changed_account_is_refreshed(calls):
    trigger.observe_positions({"h1": [_pos("AAPL", 100)], "h2": [_pos("MSFT", 10)]})
    queued = trigger.observe_positions(
        {"h1": [_pos("AAPL", 100)], "h2": [_pos("MSFT", 25)]}
    )
    assert queued == ["h2"]
    assert _wait_for(lambda: len(calls) == 1)
    assert calls == [["h2"]]


# --- debounce / cooldown / single-flight -------------------------------------

def test_burst_coalesces_into_one_refresh(calls, monkeypatch):
    monkeypatch.setattr(trigger, "_DEBOUNCE_S", 0.3)
    trigger.observe_positions({"h1": [_pos("AAPL", 100)]})
    for qty in (50, 25, 10):
        trigger.observe_positions({"h1": [_pos("AAPL", qty)]})
    assert _wait_for(lambda: len(calls) == 1)
    assert calls == [["h1"]]


def test_cooldown_defers_the_next_refresh_without_dropping_it(calls, monkeypatch):
    monkeypatch.setattr(trigger, "_COOLDOWN_S", 60.0)
    trigger.observe_positions({"h1": [_pos("AAPL", 100)]})
    trigger.observe_positions({"h1": [_pos("AAPL", 50)]})
    assert _wait_for(lambda: len(calls) == 1)

    trigger.observe_positions({"h1": [_pos("AAPL", 10)]})
    assert not _wait_for(lambda: len(calls) > 1, timeout=0.3)
    assert "h1" in trigger._pending  # deferred to after the cooldown, not lost


def test_same_observed_state_does_not_queue_a_second_refresh(calls, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def _blocking_sync(*, lookback_days: int = 365, external_ids=None) -> dict:
        calls.append(sorted(external_ids or []))
        started.set()
        release.wait(10)
        return {"broker": "schwab"}

    monkeypatch.setattr("trading.sync.sync_schwab", _blocking_sync)
    trigger.observe_positions({"h1": [_pos("AAPL", 100)]})
    trigger.observe_positions({"h1": [_pos("AAPL", 50)]})
    assert started.wait(3)
    # the sweep is still running — re-observing the same holdings adds nothing
    assert trigger.observe_positions({"h1": [_pos("AAPL", 50)]}) == []
    assert not trigger._pending
    release.set()
    assert not _wait_for(lambda: len(calls) > 1, timeout=0.3)


# --- only a fresh upstream success observes ----------------------------------

@pytest.fixture
def snapshot(monkeypatch, calls):
    """``get_schwab_accounts_sync`` with an empty cache and a mocked upstream."""
    from brokers import schwab_accounts as acct

    monkeypatch.setattr(acct, "token_exists", lambda: True)
    monkeypatch.setattr(acct, "_cache", {"ts": 0.0, "data": None})
    monkeypatch.setattr("db.schwab_history.record_schwab_daily", lambda rows: None)
    return acct


def _fetch(qty: float):
    return lambda: ({"accounts": [], "totals": {}}, {"h1": [_pos("AAPL", qty)]})


def test_fresh_snapshot_change_fires(snapshot, monkeypatch, calls):
    monkeypatch.setattr(snapshot, "_fetch_all", _fetch(100))
    snapshot.get_schwab_accounts_sync()
    monkeypatch.setattr(snapshot, "_fetch_all", _fetch(40))
    snapshot.get_schwab_accounts_sync(force=True)
    assert _wait_for(lambda: calls == [["h1"]])


def test_cache_hit_never_observes(snapshot, monkeypatch, calls):
    fetched: list[int] = []

    def _fake_fetch():
        fetched.append(len(fetched))
        qty = 100 if len(fetched) == 1 else 40
        return {"accounts": [], "totals": {}}, {"h1": [_pos("AAPL", qty)]}

    monkeypatch.setattr(snapshot, "_fetch_all", _fake_fetch)
    snapshot.get_schwab_accounts_sync()  # baseline
    snapshot.get_schwab_accounts_sync()  # inside the TTL → cache, no upstream call
    assert len(fetched) == 1
    assert not _wait_for(lambda: calls, timeout=0.2)


def test_failed_fetch_serves_stale_and_never_observes(snapshot, monkeypatch, calls):
    monkeypatch.setattr(snapshot, "_fetch_all", _fetch(100))
    snapshot.get_schwab_accounts_sync()  # baseline

    def _boom():
        raise RuntimeError("Akamai said no")

    monkeypatch.setattr(snapshot, "_fetch_all", _boom)
    data = snapshot.get_schwab_accounts_sync(force=True)
    assert data["stale"] is True
    assert not _wait_for(lambda: calls, timeout=0.2)


def test_unauthenticated_never_observes(snapshot, monkeypatch, calls):
    monkeypatch.setattr(snapshot, "token_exists", lambda: False)
    monkeypatch.setattr(snapshot, "_fetch_all", _fetch(100))
    assert snapshot.get_schwab_accounts_sync()["authenticated"] is False
    assert not _wait_for(lambda: calls, timeout=0.2)


def test_snapshot_returns_while_the_refresh_is_still_running(
    snapshot, monkeypatch, calls
):
    started = threading.Event()
    release = threading.Event()

    def _blocking_sync(*, lookback_days: int = 365, external_ids=None) -> dict:
        calls.append(sorted(external_ids or []))
        started.set()
        release.wait(10)
        return {"broker": "schwab"}

    monkeypatch.setattr("trading.sync.sync_schwab", _blocking_sync)
    monkeypatch.setattr(snapshot, "_fetch_all", _fetch(100))
    snapshot.get_schwab_accounts_sync()  # baseline

    monkeypatch.setattr(snapshot, "_fetch_all", _fetch(40))
    t0 = time.monotonic()
    data = snapshot.get_schwab_accounts_sync(force=True)
    elapsed = time.monotonic() - t0
    assert data["authenticated"] is True
    assert elapsed < 1.0  # the ledger sweep is not on the caller's path

    assert started.wait(3)
    assert not release.is_set()  # worker is inside the sweep right now
    t1 = time.monotonic()
    snapshot.get_schwab_accounts_sync()
    assert time.monotonic() - t1 < 1.0
    release.set()
