"""D-1 burst + D-2 cluster: hermetic tests over synthetic prints (no I/O)."""

from __future__ import annotations

from signals.options_flow.rolling import (
    BURST_SIDED_MIN_USD,
    CLUSTER_MIN_PRINTS,
    RollingFlow,
)

T0 = 1_800_000_000_000  # arbitrary epoch ms, minute-aligned enough


def _print(
    *,
    ts_ms: int,
    prem_usd: float,
    direction: str = "buy_side",
    right: str = "call",
    underlying: str = "NVDA",
    occ: str = "NVDA260717C00210000",
    size: int | None = None,
    basis: str | None = None,
) -> dict:
    # choose price/size so price*size*100 == prem_usd
    sz = size or 10
    p = {
        "occ_symbol": occ,
        "underlying": underlying,
        "right": right,
        "strike": 210.0,
        "expiry": "2026-07-17",
        "dte": 7,
        "ts_ms": ts_ms,
        "price": prem_usd / (sz * 100.0),
        "size": sz,
        "exchange": "C",
        "conditions": [],
        "direction": direction,
    }
    if basis is not None:
        p["direction_basis"] = basis
    return p


def test_burst_and_cluster_carry_the_prints_transport_basis() -> None:
    # 07-27 provider flip: classified prints carry the transport's own stamp
    # ("dbn_nbbo" on the databento lane). Burst/cluster events hardcoded
    # "stream_quote" — a false era claim for every sided event since the flip.
    rf = RollingFlow()
    for i in range(12):
        rf.add_print(_print(ts_ms=T0 + i * 50_000, prem_usd=100_000, basis="dbn_nbbo"))
    events = rf.check_bursts(T0 + 11 * 50_000)
    assert len(events) == 1
    assert events[0]["direction"] == "buy_side"
    assert events[0]["direction_basis"] == "dbn_nbbo"

    rf2 = RollingFlow()
    for i in range(30):
        rf2.add_print(_print(ts_ms=T0 + i * 30_000, prem_usd=20_000, basis="dbn_nbbo"))
    events = rf2.check_clusters(T0 + 30 * 30_000)
    assert len(events) == 1
    assert events[0]["direction_basis"] == "dbn_nbbo"


def test_burst_fires_on_sided_net_and_rearms() -> None:
    rf = RollingFlow()
    # $1.2M buy-side across 12 prints in 10 minutes
    for i in range(12):
        rf.add_print(_print(ts_ms=T0 + i * 50_000, prem_usd=100_000))
    events = rf.check_bursts(T0 + 11 * 50_000)
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "flow_burst"
    assert ev["direction"] == "buy_side"
    assert ev["premium_usd"] >= BURST_SIDED_MIN_USD
    assert ev["detail"]["classified_frac"] == 1.0
    assert "not a signal" in ev["caveat"]

    # same window again: re-arm blocks a duplicate
    assert rf.check_bursts(T0 + 12 * 50_000) == []
    # net grows past 1.5x -> re-fires
    for i in range(9):
        rf.add_print(_print(ts_ms=T0 + 700_000 + i * 10_000, prem_usd=100_000))
    assert len(rf.check_bursts(T0 + 800_000)) == 1


def test_burst_ignores_low_classified_below_unsigned_floor() -> None:
    rf = RollingFlow()
    # $1.5M all-undetermined: below the $3M unsigned floor -> nothing
    for i in range(15):
        rf.add_print(
            _print(ts_ms=T0 + i * 10_000, prem_usd=100_000, direction="undetermined")
        )
    assert rf.check_bursts(T0 + 200_000) == []
    # push gross past $3M unsigned -> fires undetermined
    for i in range(16):
        rf.add_print(
            _print(ts_ms=T0 + 300_000 + i * 10_000, prem_usd=100_000, direction="undetermined")
        )
    events = rf.check_bursts(T0 + 500_000)
    assert len(events) == 1
    assert events[0]["direction"] == "undetermined"
    assert "UNSIGNED" in events[0]["caveat"]


def test_burst_index_needs_higher_floor_and_no_unsigned() -> None:
    rf = RollingFlow()
    # SPY $2M sided: below the $5M index floor
    for i in range(20):
        rf.add_print(_print(ts_ms=T0 + i * 10_000, prem_usd=100_000, underlying="SPY"))
    assert rf.check_bursts(T0 + 300_000) == []
    # SPY $4M unsigned gross: index unsigned bursts are excluded in v1
    for i in range(40):
        rf.add_print(
            _print(
                ts_ms=T0 + 400_000 + i * 5_000,
                prem_usd=100_000,
                underlying="SPY",
                direction="undetermined",
            )
        )
    assert rf.check_bursts(T0 + 700_000) == []


def test_burst_window_slides_old_prints_out() -> None:
    rf = RollingFlow()
    for i in range(12):
        rf.add_print(_print(ts_ms=T0 + i * 50_000, prem_usd=100_000))
    # 40 minutes later the window is empty — no burst
    assert rf.check_bursts(T0 + 40 * 60_000) == []


def test_cluster_fires_on_small_print_aggregate_with_footprint() -> None:
    rf = RollingFlow()
    # 30 x $20k buy-side call prints, uniform 10-lot clips, regular 30s cadence
    for i in range(30):
        rf.add_print(
            _print(ts_ms=T0 + i * 30_000, prem_usd=20_000, occ="NVDA260717C00210000")
        )
    events = rf.check_clusters(T0 + 30 * 30_000)
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "flow_cluster"
    assert ev["right"] == "call"
    assert ev["n_prints"] == 30
    assert ev["premium_usd"] == 600_000.0
    assert ev["direction"] == "buy_side"
    d = ev["detail"]
    assert d["modal_size_share"] == 1.0  # every clip the same size
    assert d["interarrival_cv"] is not None and d["interarrival_cv"] < 0.1  # regular
    assert d["top_contract_share"] == 1.0  # all on one contract
    assert "cannot distinguish" in ev["caveat"]


def test_cluster_ignores_big_prints_and_needs_count() -> None:
    rf = RollingFlow()
    # 6 x $200k prints = $1.2M but each print is NOT small -> no cluster
    for i in range(6):
        rf.add_print(_print(ts_ms=T0 + i * 10_000, prem_usd=200_000))
    assert rf.check_clusters(T0 + 100_000) == []
    # 20 x $30k = $600k over floor but under CLUSTER_MIN_PRINTS
    rf2 = RollingFlow()
    for i in range(CLUSTER_MIN_PRINTS - 5):
        rf2.add_print(_print(ts_ms=T0 + i * 10_000, prem_usd=30_000))
    assert rf2.check_clusters(T0 + 300_000) == []


def test_cluster_rearm_requires_doubling() -> None:
    rf = RollingFlow()
    for i in range(30):
        rf.add_print(_print(ts_ms=T0 + i * 30_000, prem_usd=20_000))
    assert len(rf.check_clusters(T0 + 900_000)) == 1
    # a few more prints: aggregate below 2x -> silent
    for i in range(5):
        rf.add_print(_print(ts_ms=T0 + 910_000 + i * 10_000, prem_usd=20_000))
    assert rf.check_clusters(T0 + 960_000) == []


def test_hygiene_and_dust_excluded() -> None:
    rf = RollingFlow()
    # cancelled print + dust print contribute nothing
    p = _print(ts_ms=T0, prem_usd=2_000_000)
    p["conditions"] = ["C"]  # cancel condition (F-1)
    rf.add_print(p)
    rf.add_print(_print(ts_ms=T0, prem_usd=1_000))  # dust < $2k
    assert rf.check_bursts(T0 + 60_000) == []
