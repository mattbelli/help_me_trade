"""Equity tape detection (blocks + dark-pool prints) + store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from db.equity_tape import insert_event, prune, recent_events
from signals.equity_tape import (
    NO_PROFILE_MIN_NOTIONAL_USD,
    TapeDetector,
)

# Now-relative: a fixed date rots past the store's 30-day prune window and the
# prune test starts deleting the "fresh" event too (bit us 2026-08-09).
TS = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S") + ".123456789Z"


def _detector(median_shares: float | None = 80_000_000.0) -> TapeDetector:
    return TapeDetector(median_shares_loader=lambda _sym: median_shares)


def _print_kwargs(**overrides) -> dict:
    base = dict(
        symbol="SPY",
        price=620.0,
        size=20_000,  # $12.4M notional
        exchange="N",
        conditions=[],
        ts=TS,
        trade_id="52983525029461",
        prev_price=619.9,
    )
    base.update(overrides)
    return base


def test_block_detected_on_lit_exchange() -> None:
    det = _detector()
    ev = det.on_print(**_print_kwargs())
    assert ev is not None
    assert ev["event_type"] == "equity_block"
    assert ev["notional_usd"] == 620.0 * 20_000
    assert ev["direction"] == "buy_side"  # uptick vs 619.9
    assert ev["direction_basis"] == "tick"
    assert ev["id"] == f"eq_{TS[:10]}_SPY_blk_52983525029461"
    assert "not a buy/sell signal" in ev["caveat"]
    assert list(det.pending) == [ev]


def test_dark_pool_exchange_d() -> None:
    det = _detector()
    ev = det.on_print(**_print_kwargs(exchange="D", prev_price=620.1))
    assert ev is not None
    assert ev["event_type"] == "dark_pool"
    assert ev["direction"] == "sell_side"  # downtick
    assert "TRF" in ev["caveat"]
    assert ev["id"].startswith(f"eq_{TS[:10]}_SPY_dp_")


def test_small_print_ignored() -> None:
    det = _detector()
    # SPY floor = max($1M, 80M sh × $620 × 1bp = $4.96M) → $2M print is small
    assert det.on_print(**_print_kwargs(size=3_000)) is None
    assert not det.pending


def test_no_profile_uses_conservative_floor() -> None:
    det = _detector(median_shares=None)
    kwargs = _print_kwargs(symbol="XYZ", price=100.0, size=50_000)  # $5M
    assert det.on_print(**kwargs) is None
    big = _print_kwargs(
        symbol="XYZ",
        price=100.0,
        size=int(NO_PROFILE_MIN_NOTIONAL_USD / 100.0) + 1,
        prev_price=100.0,
    )
    ev = det.on_print(**big)
    assert ev is not None
    assert ev["direction"] == "undetermined"  # zero tick


def test_off_market_print_rejected() -> None:
    det = _detector()
    # >10% off last admitted = corrupt-feed guard (phantom-NVDA class)
    assert det.on_print(**_print_kwargs(price=700.0, prev_price=620.0)) is None
    # no reference yet (startup) → skip
    assert det.on_print(**_print_kwargs(prev_price=None)) is None


def test_cooldown_per_symbol_and_type() -> None:
    det = _detector()
    assert det.on_print(**_print_kwargs()) is not None
    assert det.on_print(**_print_kwargs(trade_id="2")) is None  # same type cooling
    # different type (dark pool) still fires
    assert det.on_print(**_print_kwargs(exchange="D", trade_id="3")) is not None
    # different symbol unaffected
    assert (
        det.on_print(**_print_kwargs(symbol="QQQ", trade_id="4")) is not None
    )


def test_store_upsert_filters_and_prune(tmp_path: Path) -> None:
    db = tmp_path / "tape.db"
    det = _detector()
    ev = det.on_print(**_print_kwargs())
    assert ev is not None
    insert_event(ev, db_path=db)
    insert_event(ev, db_path=db)  # idempotent upsert

    rows = recent_events(db_path=db)
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "SPY"
    assert row["conditions"] == []
    assert row["notional_usd"] == ev["notional_usd"]

    assert recent_events(symbol="QQQ", db_path=db) == []
    assert recent_events(event_type="dark_pool", db_path=db) == []
    assert recent_events(min_notional=1e9, db_path=db) == []
    assert len(recent_events(since=f"{TS[:10]}T00:00:00Z", db_path=db)) == 1

    old = dict(ev, id="eq_old", ts="2020-01-01T00:00:00Z")
    insert_event(old, db_path=db)
    assert prune(db_path=db) == 1
    assert len(recent_events(db_path=db)) == 1
