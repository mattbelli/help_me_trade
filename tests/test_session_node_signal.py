"""Tests for research.session_node_signal — synthetic, deterministic, no network."""

from __future__ import annotations

import copy
from datetime import date

import pytest

from research.intraday_bars import IntradayBar
from research.session_node_signal import (
    ReferenceNodes,
    compute_reference_nodes,
    detect_node_triggers,
)

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
    """Volume concentrated at 100; thin wings at 85 and 115."""
    return (
        _flat_session(ny_date, 85.0, 50, volume=10_000.0)
        + _flat_session(ny_date, 100.0, 10, volume=100_000.0, start_minute=80)
        + _flat_session(ny_date, 115.0, 3, volume=500.0, start_minute=90)
    )


def _synthetic_nodes() -> ReferenceNodes:
    """Well-separated levels so one bar does not tag multiple nodes."""
    return ReferenceNodes(
        session_date=DAY1,
        poc=100.0,
        vah=110.0,
        val=90.0,
        pdh=112.0,
        pdl=88.0,
    )


def test_compute_reference_nodes_poc_and_pdh_pdl() -> None:
    prior = _reference_session(DAY1)
    nodes = compute_reference_nodes(prior, n_bins=50)
    assert nodes is not None
    assert nodes.session_date == DAY1

    assert nodes.poc == pytest.approx(100.0, abs=2.0)
    assert nodes.val < nodes.poc < nodes.vah
    assert nodes.pdh == pytest.approx(max(float(b["h"]) for b in prior))
    assert nodes.pdl == pytest.approx(min(float(b["l"]) for b in prior))

    levels = dict(nodes.as_levels())
    assert set(levels) == {"VAH", "VAL", "PDH", "PDL"}
    assert "POC" not in levels
    assert levels["PDH"] == nodes.pdh


def test_compute_reference_nodes_empty_returns_none() -> None:
    assert compute_reference_nodes([]) is None


def test_direction_support_long_resistance_short() -> None:
    nodes = _synthetic_nodes()
    val = nodes.val
    vah = nodes.vah
    poc = nodes.poc

    long_touch = [
        _bar(
            _rth_bar(DAY2, 9, 30)["t"],
            o=val + 2.0,
            h=val + 2.0,
            low=val,
            c=val + 1.0,
        ),
    ]
    short_touch = [
        _bar(
            _rth_bar(DAY2, 9, 31)["t"],
            o=vah + 0.5,
            h=vah + 0.5,
            low=vah,
            c=vah + 0.3,
        ),
    ]

    triggers = detect_node_triggers(
        long_touch + short_touch, nodes, rearm_points=1.0
    )
    assert len(triggers) == 2
    assert triggers[0].node_name == "VAL"
    assert triggers[0].direction == "long"
    assert triggers[0].target_price == pytest.approx(poc)
    assert triggers[1].node_name == "VAH"
    assert triggers[1].direction == "short"
    assert triggers[1].target_price == pytest.approx(poc)


def test_rearm_no_spam_within_hysteresis() -> None:
    nodes = _synthetic_nodes()
    val = nodes.val
    ts_base = _rth_bar(DAY2, 9, 30)["t"]

    chop = [
        _bar(ts_base, o=val + 0.5, h=val + 0.5, low=val, c=val + 0.3),
        _bar(
            _rth_bar(DAY2, 9, 31)["t"],
            o=val + 0.3,
            h=val + 0.4,
            low=val,
            c=val + 0.2,
        ),
        _bar(
            _rth_bar(DAY2, 9, 32)["t"],
            o=val + 0.2,
            h=val + 0.3,
            low=val,
            c=val + 0.1,
        ),
    ]

    triggers = detect_node_triggers(chop, nodes, rearm_points=1.0)
    val_triggers = [t for t in triggers if t.node_name == "VAL"]
    assert len(val_triggers) == 1


def test_rearm_round_trip_fires_twice() -> None:
    nodes = _synthetic_nodes()
    val = nodes.val

    bars = [
        _bar(
            _rth_bar(DAY2, 9, 30)["t"],
            o=val + 2.0,
            h=val + 2.0,
            low=val,
            c=val + 1.0,
        ),
        _bar(
            _rth_bar(DAY2, 9, 31)["t"],
            o=val + 1.0,
            h=val + 3.0,
            low=val + 1.0,
            c=val + 2.5,
        ),
        _bar(
            _rth_bar(DAY2, 9, 32)["t"],
            o=val + 2.5,
            h=val + 2.5,
            low=val,
            c=val + 0.5,
        ),
    ]

    triggers = detect_node_triggers(bars, nodes, rearm_points=1.0)
    val_triggers = [t for t in triggers if t.node_name == "VAL"]
    assert len(val_triggers) == 2
    assert val_triggers[0].ts == bars[0]["t"]
    assert val_triggers[1].ts == bars[2]["t"]


def test_multiple_nodes_val_then_vah() -> None:
    nodes = _synthetic_nodes()
    val = nodes.val
    vah = nodes.vah
    poc = nodes.poc

    bars = [
        _bar(
            _rth_bar(DAY2, 9, 30)["t"],
            o=val + 1.0,
            h=val + 1.0,
            low=val,
            c=val + 0.5,
        ),
        _bar(
            _rth_bar(DAY2, 10, 0)["t"],
            o=vah - 1.0,
            h=vah + 1.0,
            low=vah - 1.0,
            c=vah,
        ),
    ]

    triggers = detect_node_triggers(bars, nodes, rearm_points=1.0)
    assert len(triggers) == 2
    assert triggers[0].node_name == "VAL"
    assert triggers[0].direction == "long"
    assert triggers[0].target_price == pytest.approx(poc)
    assert triggers[1].node_name == "VAH"
    assert triggers[1].direction == "short"
    assert triggers[1].target_price == pytest.approx(poc)


def test_poc_is_target_not_entry_node() -> None:
    nodes = _synthetic_nodes()
    levels = dict(nodes.as_levels())
    assert set(levels) == {"VAH", "VAL", "PDH", "PDL"}
    assert "POC" not in levels

    val = nodes.val
    vah = nodes.vah
    bars = [
        _bar(
            _rth_bar(DAY2, 9, 30)["t"],
            o=val + 2.0,
            h=val + 2.0,
            low=val,
            c=val + 1.0,
        ),
        _bar(
            _rth_bar(DAY2, 9, 31)["t"],
            o=vah + 0.5,
            h=vah + 0.5,
            low=vah,
            c=vah + 0.3,
        ),
    ]

    triggers = detect_node_triggers(bars, nodes, rearm_points=1.0)
    assert len(triggers) == 2
    for trigger in triggers:
        assert trigger.target_price == pytest.approx(nodes.poc)
        assert trigger.node_price != trigger.target_price
        assert trigger.node_name != "POC"


def test_per_node_cap() -> None:
    nodes = _synthetic_nodes()
    val = nodes.val
    max_per_node = 2

    bars: list[IntradayBar] = []
    for i in range(6):
        ts = _rth_bar(DAY2, 9, 30 + i * 2)["t"]
        if i % 2 == 0:
            bars.append(
                _bar(ts, o=val + 2.0, h=val + 2.0, low=val, c=val + 1.0)
            )
        else:
            bars.append(
                _bar(ts, o=val + 1.0, h=val + 3.0, low=val + 1.0, c=val + 2.5)
            )

    triggers = detect_node_triggers(
        bars,
        nodes,
        rearm_points=1.0,
        max_per_node=max_per_node,
        max_triggers_per_day=100,
    )
    val_triggers = [t for t in triggers if t.node_name == "VAL"]
    assert len(val_triggers) == max_per_node


def test_daily_cap_keeps_earliest() -> None:
    nodes = _synthetic_nodes()
    val = nodes.val
    vah = nodes.vah
    pdh = nodes.pdh
    pdl = nodes.pdl
    max_triggers_per_day = 3

    bars = [
        _bar(
            _rth_bar(DAY2, 9, 30)["t"],
            o=val + 2.0,
            h=val + 2.0,
            low=val,
            c=val + 1.0,
        ),
        _bar(
            _rth_bar(DAY2, 9, 31)["t"],
            o=vah + 0.5,
            h=vah + 0.5,
            low=vah,
            c=vah + 0.3,
        ),
        _bar(
            _rth_bar(DAY2, 9, 32)["t"],
            o=pdh + 0.5,
            h=pdh + 0.5,
            low=pdh,
            c=pdh + 0.3,
        ),
        _bar(
            _rth_bar(DAY2, 9, 33)["t"],
            o=pdl + 2.0,
            h=pdl + 2.0,
            low=pdl,
            c=pdl + 1.0,
        ),
        _bar(
            _rth_bar(DAY2, 9, 34)["t"],
            o=val + 2.0,
            h=val + 2.0,
            low=val,
            c=val + 1.0,
        ),
    ]

    triggers = detect_node_triggers(
        bars,
        nodes,
        rearm_points=1.0,
        max_per_node=10,
        max_triggers_per_day=max_triggers_per_day,
    )
    assert len(triggers) == max_triggers_per_day
    assert triggers[0].node_name == "VAL"
    assert triggers[1].node_name == "VAH"
    assert triggers[2].node_name == "PDH"


def test_no_look_ahead() -> None:
    nodes = _synthetic_nodes()
    val = nodes.val

    bars = [
        _bar(
            _rth_bar(DAY2, 9, 30)["t"],
            o=val + 2.0,
            h=val + 2.0,
            low=val,
            c=val + 1.0,
        ),
        _bar(
            _rth_bar(DAY2, 9, 31)["t"],
            o=val + 5.0,
            h=val + 10.0,
            low=val + 4.0,
            c=val + 8.0,
        ),
    ]

    baseline = detect_node_triggers(bars, nodes, rearm_points=1.0)
    mutated = copy.deepcopy(bars)
    mutated[-1]["h"] = 999.0
    mutated[-1]["c"] = 999.0

    again = detect_node_triggers(mutated, nodes, rearm_points=1.0)
    assert again[0] == baseline[0]
