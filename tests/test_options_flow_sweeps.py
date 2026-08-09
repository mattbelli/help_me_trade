"""SWP-1 sweep/split clustering tests (design §5, Phase 3b) — pure, no I/O."""

from __future__ import annotations

from signals.options_flow import convergence
from signals.options_flow.sweeps import SweepBuffer, _cluster_event

TODAY = "2026-07-09"
BASE_MS = 1_752_000_000_000

_META = {
    "occ_symbol": "NVDA260821C00190000",
    "underlying": "NVDA",
    "right": "call",
    "strike": 190.0,
    "expiry": "2026-08-21",
    "dte": 43,
}


def _print(
    *,
    ts_ms: int = BASE_MS,
    price: float = 5.0,
    size: int = 100,
    exchange: str = "C",
    conditions: list[str] | None = None,
    direction: str = "undetermined",
    basis: str = "none",
    **meta_over,
) -> dict:
    p = {
        **_META,
        **meta_over,
        "ts_ms": ts_ms,
        "price": price,
        "size": size,
        "exchange": exchange,
        "conditions": conditions or [],
        "direction": direction,
        "direction_basis": basis,
    }
    return p


def _closed(buffer: SweepBuffer, *, now_offset_ms: int = 10_000) -> list[dict]:
    return buffer.close_ready(BASE_MS + now_offset_ms, TODAY)


def test_intermarket_sweep_detected_with_iso_corroboration() -> None:
    buf = SweepBuffer()
    prints = [
        _print(ts_ms=BASE_MS, exchange="C", direction="buy_side", basis="stream_quote"),
        _print(ts_ms=BASE_MS + 100, exchange="N", direction="buy_side", basis="stream_quote"),
        _print(ts_ms=BASE_MS + 300, exchange="M", conditions=["S"], direction="buy_side", basis="stream_quote"),
    ]
    buf.add(prints)
    events = _closed(buf)
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "sweep"
    assert ev["id"] == f"of_{TODAY}_NVDA_NVDA260821C00190000_swp_{BASE_MS}"
    assert ev["contracts"] == 300
    assert ev["premium_usd"] == 300 * 5.0 * 100.0
    assert ev["n_prints"] == 3
    assert ev["exchanges"] == ["C", "M", "N"]
    assert ev["direction"] == "buy_side"
    assert ev["direction_basis"] == "stream_quote"
    assert "sweep across 3 exchanges" in ev["reason"]
    assert "ISO-flagged" in ev["reason"]


def test_split_order_needs_min_prints_on_one_exchange() -> None:
    buf = SweepBuffer()
    buf.add([_print(ts_ms=BASE_MS + i * 50, exchange="C", size=60) for i in range(5)])
    events = _closed(buf)
    assert len(events) == 1
    assert "split order on one exchange" in events[0]["reason"]

    buf2 = SweepBuffer()
    buf2.add([_print(ts_ms=BASE_MS + i * 50, exchange="C", size=60) for i in range(4)])
    assert _closed(buf2) == []


def test_sweep_basis_follows_the_prints_transport_stamp() -> None:
    # 07-27 provider flip: prints on the databento lane classify from the tcbbo
    # book and carry basis "dbn_nbbo". The event must propagate the prints' own
    # stamp — matching the Alpaca literal only left every sided sweep claiming
    # basis "none" for the whole dbn era.
    buf = SweepBuffer()
    buf.add(
        [
            _print(ts_ms=BASE_MS, exchange="C", direction="buy_side", basis="dbn_nbbo"),
            _print(ts_ms=BASE_MS + 100, exchange="N", direction="buy_side", basis="dbn_nbbo"),
            _print(ts_ms=BASE_MS + 300, exchange="M", direction="buy_side", basis="dbn_nbbo"),
        ]
    )
    events = _closed(buf)
    assert len(events) == 1
    assert events[0]["direction"] == "buy_side"
    assert events[0]["direction_basis"] == "dbn_nbbo"


def test_two_exchanges_few_prints_is_nothing() -> None:
    buf = SweepBuffer()
    buf.add(
        [
            _print(ts_ms=BASE_MS, exchange="C"),
            _print(ts_ms=BASE_MS + 100, exchange="N"),
        ]
    )
    assert _closed(buf) == []


def test_window_splits_clusters() -> None:
    buf = SweepBuffer()
    # 3 exchanges but the third print falls outside the 500ms window of the anchor.
    buf.add(
        [
            _print(ts_ms=BASE_MS, exchange="C"),
            _print(ts_ms=BASE_MS + 400, exchange="N"),
            _print(ts_ms=BASE_MS + 600, exchange="M"),
        ]
    )
    assert _closed(buf) == []


def test_premium_floors_single_name_and_index() -> None:
    # Below $25k single-name floor: 3 exchanges but tiny premium.
    buf = SweepBuffer()
    buf.add(
        [
            _print(ts_ms=BASE_MS + i * 100, exchange=x, price=0.5, size=10)
            for i, x in enumerate("CNM")
        ]
    )
    assert _closed(buf) == []

    # SPY needs $250k; $150k passes for a single name but not the index set.
    spy_meta = {
        "occ_symbol": "SPY260821C00700000",
        "underlying": "SPY",
        "strike": 700.0,
    }
    buf2 = SweepBuffer()
    buf2.add(
        [
            _print(ts_ms=BASE_MS + i * 100, exchange=x, price=5.0, size=100, **spy_meta)
            for i, x in enumerate("CNM")
        ]
    )
    assert _closed(buf2) == []


def test_zero_dte_index_and_hygiene_and_spread_prints_excluded() -> None:
    buf = SweepBuffer()
    buf.add(
        [
            _print(
                ts_ms=BASE_MS + i * 100,
                exchange=x,
                size=1000,
                occ_symbol="QQQ260709C00600000",
                underlying="QQQ",
                dte=0,
            )
            for i, x in enumerate("CNM")
        ]
    )
    assert _closed(buf) == []

    buf2 = SweepBuffer()
    prints = [
        _print(ts_ms=BASE_MS, exchange="C"),
        _print(ts_ms=BASE_MS + 100, exchange="N", conditions=["C"]),  # cancel → F-1
        _print(ts_ms=BASE_MS + 200, exchange="M"),
    ]
    prints[2]["likely_spread"] = True  # F-2 tag
    buf2.add(prints)
    assert _closed(buf2) == []


def test_direction_majority_and_undetermined_below_classified_floor() -> None:
    cluster = [
        _print(ts_ms=BASE_MS, exchange="C", size=300, direction="buy_side", basis="stream_quote"),
        _print(ts_ms=BASE_MS + 100, exchange="N", size=100, direction="sell_side", basis="stream_quote"),
        _print(ts_ms=BASE_MS + 200, exchange="M", size=100),
    ]
    ev = _cluster_event(cluster, TODAY)
    assert ev is not None
    assert ev["direction"] == "buy_side"
    assert ev["direction_basis"] == "stream_quote"

    mostly_unclassified = [
        _print(ts_ms=BASE_MS, exchange="C", size=100, direction="buy_side", basis="stream_quote"),
        _print(ts_ms=BASE_MS + 100, exchange="N", size=400),
        _print(ts_ms=BASE_MS + 200, exchange="M", size=400),
    ]
    ev2 = _cluster_event(mostly_unclassified, TODAY)
    assert ev2 is not None
    assert ev2["direction"] == "undetermined"
    assert ev2["direction_basis"] == "none"
    assert "undetermined" in ev2["caveat"]


def test_cluster_held_until_grace_elapses_then_closed_once() -> None:
    buf = SweepBuffer()
    buf.add(
        [
            _print(ts_ms=BASE_MS + i * 100, exchange=x, direction="buy_side")
            for i, x in enumerate("CNM")
        ]
    )
    # Inside window+grace: nothing closes, prints retained.
    assert buf.close_ready(BASE_MS + 1_200, TODAY) == []
    events = buf.close_ready(BASE_MS + 10_000, TODAY)
    assert len(events) == 1
    # Buffer drained — a second pass emits nothing.
    assert buf.close_ready(BASE_MS + 20_000, TODAY) == []


def test_sweep_reaches_convergence_event_types() -> None:
    assert "sweep" in convergence._FLOW_EVENT_TYPES


def test_late_multileg_code_excludes_print_at_cluster_close() -> None:
    """Late pairing enrichment: a MULTILEG code that lands AFTER add() but
    before the cluster closes must drop the leg — eligibility re-runs at
    close, not only at drain."""
    buf = SweepBuffer()
    prints = [
        _print(ts_ms=BASE_MS, exchange="C", direction="buy_side", basis="stream_quote"),
        _print(ts_ms=BASE_MS + 100, exchange="N", direction="buy_side", basis="stream_quote"),
        _print(ts_ms=BASE_MS + 200, exchange="M", direction="buy_side", basis="stream_quote"),
        _print(ts_ms=BASE_MS + 300, exchange="A", direction="buy_side", basis="stream_quote"),
    ]
    buf.add(prints)
    prints[3]["conditions"] = ["f"]  # the matcher's late stamp
    events = _closed(buf)
    assert len(events) == 1
    assert events[0]["n_prints"] == 3  # the coded leg fell out
    # And a fully-late-coded cluster yields nothing at all.
    buf2 = SweepBuffer()
    pair = [
        _print(ts_ms=BASE_MS, exchange="C"),
        _print(ts_ms=BASE_MS + 100, exchange="N"),
    ]
    buf2.add(pair)
    for p in pair:
        p["conditions"] = ["g"]
    assert _closed(buf2) == []


def test_closed_cluster_prints_carry_the_event_backref() -> None:
    """Every print of an emitted sweep gets _event_id, so a code arriving
    after close can repair the stored row by exact id."""
    buf = SweepBuffer()
    prints = [
        _print(ts_ms=BASE_MS, exchange="C", direction="buy_side", basis="stream_quote"),
        _print(ts_ms=BASE_MS + 100, exchange="N", direction="buy_side", basis="stream_quote"),
        _print(ts_ms=BASE_MS + 200, exchange="M", direction="buy_side", basis="stream_quote"),
    ]
    buf.add(prints)
    events = _closed(buf)
    assert len(events) == 1
    assert all(p.get("_event_id") == events[0]["id"] for p in prints)
