"""Persistent (repeat) price alerts + cross direction.

One-shot alerts fire once then leave the active list. Persistent alerts re-arm
after each crossing and stay active until deleted, debounced by a cooldown.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api import alert_checker
from db.chart_features import (
    create_price_alert,
    delete_price_alert,
    list_price_alerts,
    mark_alert_triggered,
)


def _clear_alerts() -> None:
    for a in list_price_alerts(active_only=False):
        delete_price_alert(int(a["id"]))


@pytest.fixture(autouse=True)
def _isolate_alerts():
    _clear_alerts()
    yield
    _clear_alerts()


def test_one_shot_alert_deactivates_after_trigger() -> None:
    a = create_price_alert(symbol="AAPL", price=200.0, direction="above")
    assert a["repeat"] == 0
    assert any(x["id"] == a["id"] for x in list_price_alerts(active_only=True))

    mark_alert_triggered(int(a["id"]), repeat=False)
    active_ids = {x["id"] for x in list_price_alerts(active_only=True)}
    assert a["id"] not in active_ids  # gone from active list


def test_repeat_alert_stays_active_after_trigger() -> None:
    a = create_price_alert(symbol="AAPL", price=200.0, direction="above", repeat=True)
    assert a["repeat"] == 1

    mark_alert_triggered(int(a["id"]), repeat=True)
    active = list_price_alerts(active_only=True)
    row = next(x for x in active if x["id"] == a["id"])
    assert row["triggered"] == 0  # still armed
    assert row["last_triggered_at"]  # but stamped


def test_check_alerts_repeat_refires_one_shot_does_not() -> None:
    one = create_price_alert(symbol="AAPL", price=200.0, direction="above")
    rep = create_price_alert(symbol="MSFT", price=400.0, direction="above", repeat=True)

    # First crossing fires both.
    fired = alert_checker.check_alerts(
        {"AAPL": 200.5, "MSFT": 400.5},
        prev_quotes={"AAPL": 199.5, "MSFT": 399.5},
    )
    fired_ids = {f["id"] for f in fired}
    assert one["id"] in fired_ids and rep["id"] in fired_ids

    # Force the repeat alert past its cooldown so the next cross can re-fire.
    _age_last_triggered(int(rep["id"]), seconds=120)

    # Second crossing: one-shot is gone, repeat fires again.
    fired2 = alert_checker.check_alerts(
        {"AAPL": 201.0, "MSFT": 401.0},
        prev_quotes={"AAPL": 199.0, "MSFT": 399.0},
    )
    fired2_ids = {f["id"] for f in fired2}
    assert one["id"] not in fired2_ids
    assert rep["id"] in fired2_ids


def test_repeat_alert_cooldown_suppresses_rapid_refire() -> None:
    rep = create_price_alert(symbol="NVDA", price=100.0, direction="cross", repeat=True)

    fired = alert_checker.check_alerts(
        {"NVDA": 100.5}, prev_quotes={"NVDA": 99.5}
    )
    assert any(f["id"] == rep["id"] for f in fired)

    # Immediate second crossing within the cooldown window → suppressed.
    fired2 = alert_checker.check_alerts(
        {"NVDA": 99.5}, prev_quotes={"NVDA": 100.5}
    )
    assert not any(f["id"] == rep["id"] for f in fired2)


def test_repeat_alert_oscillation_at_level_does_not_refire() -> None:
    """After firing, a price dithering ~0.1% around the level must NOT re-fire.

    The band = max(target*0.003, 0.01) = 0.3 at target 100; ±0.1% swings stay
    inside it, so the alert never re-arms even with the cooldown elapsed.
    """
    rep = create_price_alert(symbol="NVDA", price=100.0, direction="cross", repeat=True)

    fired = alert_checker.check_alerts({"NVDA": 100.1}, prev_quotes={"NVDA": 99.9})
    assert any(f["id"] == rep["id"] for f in fired)

    # Even past the cooldown, oscillating within the band must not re-arm/re-fire.
    for prev, cur in [(100.1, 99.9), (99.9, 100.1), (100.1, 99.95), (99.95, 100.1)]:
        _age_last_triggered(int(rep["id"]), seconds=120)
        again = alert_checker.check_alerts({"NVDA": cur}, prev_quotes={"NVDA": prev})
        assert not any(f["id"] == rep["id"] for f in again), (prev, cur)


def test_repeat_alert_refires_after_retreat_past_band_and_recross() -> None:
    """Price retreats a full band away, then crosses back → fires again."""
    rep = create_price_alert(symbol="NVDA", price=100.0, direction="above", repeat=True)

    fired = alert_checker.check_alerts({"NVDA": 100.5}, prev_quotes={"NVDA": 99.5})
    assert any(f["id"] == rep["id"] for f in fired)

    # Retreat well past the band (down to 99.0) — re-arms but doesn't fire
    # (downward move, direction="above").
    retreat = alert_checker.check_alerts({"NVDA": 99.0}, prev_quotes={"NVDA": 100.5})
    assert not any(f["id"] == rep["id"] for f in retreat)

    # Re-cross up, cooldown elapsed → fires again.
    _age_last_triggered(int(rep["id"]), seconds=120)
    refire = alert_checker.check_alerts({"NVDA": 100.5}, prev_quotes={"NVDA": 99.0})
    assert any(f["id"] == rep["id"] for f in refire)


def test_repeat_alert_retreat_still_honors_cooldown() -> None:
    """Retreat past the band re-arms, but the 60s cooldown still gates a re-fire."""
    rep = create_price_alert(symbol="NVDA", price=100.0, direction="above", repeat=True)

    alert_checker.check_alerts({"NVDA": 100.5}, prev_quotes={"NVDA": 99.5})
    # Re-arm via retreat, then immediately re-cross WITHOUT aging → cooldown blocks.
    alert_checker.check_alerts({"NVDA": 99.0}, prev_quotes={"NVDA": 100.5})
    refire = alert_checker.check_alerts({"NVDA": 100.5}, prev_quotes={"NVDA": 99.0})
    assert not any(f["id"] == rep["id"] for f in refire)


def test_one_shot_concurrent_evaluations_fire_exactly_once() -> None:
    """The read-then-mark gap: two evaluations both see triggered=0 and try to
    claim the same one-shot. Only one conditional UPDATE flips the row, so only
    one caller may notify."""
    a = create_price_alert(symbol="AAPL", price=200.0, direction="above")

    first = mark_alert_triggered(int(a["id"]), repeat=False)
    second = mark_alert_triggered(int(a["id"]), repeat=False)

    assert first is True   # claimed the fire → notifies
    assert second is False  # lost the race → must not notify
    assert sum(1 for x in (first, second) if x) == 1


def _age_last_triggered(alert_id: int, *, seconds: int) -> None:
    """Backdate an alert's last_triggered_at so the cooldown has elapsed."""
    from db.base import connect

    past = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
    with connect() as conn:
        conn.execute(
            "UPDATE price_alerts SET last_triggered_at = ? WHERE id = ?",
            (past, alert_id),
        )
        conn.commit()
