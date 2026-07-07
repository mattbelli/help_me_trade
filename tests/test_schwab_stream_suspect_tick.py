"""Suspect-print confirmation guard for the Schwab stream write points.

A lone bad print ~2× the true price (e.g. a ~$315 stock "printing" ~$750) used to
sail through the old single-tick 3× guard and spike a chart candle / poison the
watchlist / the RiskEngine live quote. The guard is now a ±50% band around the LAST
ADMITTED price (seeded from the prior close) with one-tick corroboration: a suspect
tick is HELD unless the immediately preceding raw tick confirms the same level
(within 5%). Every admit re-centers the band, so a confirmed gap flows freely and a
phantom-admitted level self-heals within ~2 real ticks. The REST apply_extended poll
gets the same band with ONE-POLL corroboration. These tests pin the session (never
the wall clock) and touch no network — they assert QuoteState / guard state directly.
"""

from __future__ import annotations

import logging

import pytest

from brokers.market_stream import QuoteState
from brokers.schwab_market_stream import SchwabMarketStreamHub


@pytest.fixture(autouse=True)
def _regular_session(monkeypatch):
    """Pin the session gate to RTH so the equity path is deterministic; ext tests
    re-patch `market_session` explicitly."""
    monkeypatch.setattr(
        "brokers.schwab_market_stream.market_session", lambda: "regular"
    )


async def _noop_async(*args, **kwargs):
    return None


def _good_tick(hub: SchwabMarketStreamHub, symbol: str, price: float, close: float):
    hub._on_equity(
        {"content": [{"key": symbol, "LAST_PRICE": price, "CLOSE_PRICE": close}]}
    )


# ---------------------------------------------------------------------------
# Equity regular path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lone_2x_spike_is_held_and_logged(caplog) -> None:
    hub = SchwabMarketStreamHub()
    _good_tick(hub, "TSLA", 315.0, 315.0)
    assert hub._quotes["TSLA"].price == pytest.approx(315.0)
    assert hub._pending_tick["TSLA"] == pytest.approx(315.0)

    # A lone ~2.4× print between two normal ticks: no preceding corroboration.
    with caplog.at_level(logging.WARNING):
        hub._on_equity({"content": [{"key": "TSLA", "LAST_PRICE": 750.0}]})

    state = hub._quotes["TSLA"]
    assert state.price == pytest.approx(315.0)  # unchanged — spike dropped
    assert hub._pending_tick["TSLA"] == pytest.approx(750.0)  # pending still updated
    assert any(
        "suspect" in r.message.lower() and "TSLA" in r.message for r in caplog.records
    )

    # Reference (last admitted = 315) uncorrupted → next in-band tick admits.
    hub._on_equity({"content": [{"key": "TSLA", "LAST_PRICE": 318.0}]})
    assert hub._quotes["TSLA"].price == pytest.approx(318.0)


@pytest.mark.asyncio
async def test_spike_then_corroborating_tick_admits(monkeypatch) -> None:
    """A real fast move / halt-reopen: the second tick confirms the first, so it is
    admitted after one held tick."""
    hub = SchwabMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)
    _good_tick(hub, "AAPL", 200.0, 200.0)

    # First out-of-band print (450 vs 200) — held.
    hub._on_equity({"content": [{"key": "AAPL", "LAST_PRICE": 450.0}]})
    assert hub._quotes["AAPL"].price == pytest.approx(200.0)

    # Immediately corroborated within 5% (455 vs pending 450) — admitted.
    hub._on_equity({"content": [{"key": "AAPL", "LAST_PRICE": 455.0}]})
    assert hub._quotes["AAPL"].price == pytest.approx(455.0)


@pytest.mark.asyncio
async def test_persistent_outside_band_flows(monkeypatch) -> None:
    """A stock legitimately trading well above its prior close all session: after one
    held tick, consecutive corroborating ticks flow continuously."""
    hub = SchwabMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)
    hub._quotes["GME"] = QuoteState(prev_close=100.0)

    hub._on_equity({"content": [{"key": "GME", "LAST_PRICE": 250.0}]})  # held (first)
    hub._on_equity({"content": [{"key": "GME", "LAST_PRICE": 251.0}]})  # corroborated
    hub._on_equity({"content": [{"key": "GME", "LAST_PRICE": 252.0}]})  # in-band now
    assert hub._quotes["GME"].price == pytest.approx(252.0)


@pytest.mark.asyncio
async def test_gapped_runner_recenters_and_flows(monkeypatch) -> None:
    """MAJOR-2 pin: +60% gap at the open → one held tick, corroborating tick admits,
    the band RE-CENTERS on the gapped level, and subsequent ±8% swings flow freely
    (with a prev_close-only reference they would all have been held → frozen price)."""
    hub = SchwabMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)
    hub._quotes["GAP"] = QuoteState(prev_close=100.0)

    hub._on_equity({"content": [{"key": "GAP", "LAST_PRICE": 160.0}]})  # held
    assert hub._quotes["GAP"].price == pytest.approx(0.0)
    hub._on_equity({"content": [{"key": "GAP", "LAST_PRICE": 162.0}]})  # confirmed
    assert hub._quotes["GAP"].price == pytest.approx(162.0)

    # ±8% swings around the gapped level — every tick in-band vs last-admitted.
    for px in (175.0, 161.0, 174.0):
        hub._on_equity({"content": [{"key": "GAP", "LAST_PRICE": px}]})
        assert hub._quotes["GAP"].price == pytest.approx(px)


@pytest.mark.asyncio
async def test_phantom_pair_recenters_on_reality_within_two_ticks(monkeypatch) -> None:
    """Accepted residual + self-heal pin: two same-level bogus prints phantom-admit
    (not worse than the old guard), but the very next two real ticks corroborate each
    other and re-center the reference on reality within ~2 ticks."""
    hub = SchwabMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)
    _good_tick(hub, "PHM", 100.0, 100.0)

    hub._on_equity({"content": [{"key": "PHM", "LAST_PRICE": 210.0}]})  # bogus, held
    hub._on_equity({"content": [{"key": "PHM", "LAST_PRICE": 212.0}]})  # phantom admit
    assert hub._quotes["PHM"].price == pytest.approx(212.0)

    # Real tick 1: out-of-band vs the phantom 212, no corroboration → held.
    hub._on_equity({"content": [{"key": "PHM", "LAST_PRICE": 101.0}]})
    assert hub._quotes["PHM"].price == pytest.approx(212.0)
    # Real tick 2: corroborates real tick 1 within 5% → admitted, ref re-centered.
    hub._on_equity({"content": [{"key": "PHM", "LAST_PRICE": 101.5}]})
    assert hub._quotes["PHM"].price == pytest.approx(101.5)


@pytest.mark.asyncio
async def test_no_prev_close_admits_everything(monkeypatch) -> None:
    """No baseline yet → band check skipped → existing admit-everything behavior."""
    hub = SchwabMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)
    # No CLOSE_PRICE → prev_close stays 0.0.
    hub._on_equity({"content": [{"key": "NEW", "LAST_PRICE": 750.0}]})
    assert hub._quotes["NEW"].price == pytest.approx(750.0)


@pytest.mark.asyncio
async def test_held_warning_rate_limited_and_error_at_frozen_streak(
    monkeypatch, caplog
) -> None:
    """MINOR-5 pin: held-tick WARNINGs are rate-limited to one per symbol per 60s and
    the 20th consecutive hold escalates to ERROR (the 'price frozen' alarm)."""
    hub = SchwabMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)
    _good_tick(hub, "FRZ", 100.0, 100.0)

    with caplog.at_level(logging.WARNING, logger="brokers.schwab_market_stream"):
        # 20 consecutive holds: alternate levels so no pair ever corroborates
        # (each print differs >5% from the preceding one).
        for i in range(20):
            px = 300.0 if i % 2 == 0 else 400.0
            hub._on_equity({"content": [{"key": "FRZ", "LAST_PRICE": px}]})

    assert hub._quotes["FRZ"].price == pytest.approx(100.0)  # frozen on last good
    ours = [r for r in caplog.records if "FRZ" in r.message]
    warnings = [r for r in ours if r.levelno == logging.WARNING]
    errors = [r for r in ours if r.levelno == logging.ERROR]
    # Hold #1 logs immediately; #2–#19 are suppressed inside the 60s window.
    assert len(warnings) == 1
    # Hold #20 escalates to ERROR exactly once.
    assert len(errors) == 1
    assert "FROZEN" in errors[0].message


# ---------------------------------------------------------------------------
# Futures mirror
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_futures_spike_held_then_confirm_admits(monkeypatch) -> None:
    hub = SchwabMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)
    hub._on_futures(
        {"content": [{"key": "/ES", "LAST_PRICE": 5500.0, "CLOSE_PRICE": 5500.0}]}
    )
    assert hub._quotes["/ES"].price == pytest.approx(5500.0)

    # Lone spike (~2.2×) — held.
    hub._on_futures({"content": [{"key": "/ES", "LAST_PRICE": 12000.0}]})
    assert hub._quotes["/ES"].price == pytest.approx(5500.0)

    # Corroborated within 5% — admitted (band re-centers on the new level).
    hub._on_futures({"content": [{"key": "/ES", "LAST_PRICE": 12100.0}]})
    assert hub._quotes["/ES"].price == pytest.approx(12100.0)


# ---------------------------------------------------------------------------
# Extended-hours paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ext_suspect_tick_held_in_on_equity(monkeypatch) -> None:
    """Outside RTH the shared guard also gates the ext write: a suspect ext print is
    held (ext fields untouched)."""
    monkeypatch.setattr(
        "brokers.schwab_market_stream.market_session", lambda: "post"
    )
    hub = SchwabMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)
    hub._quotes["SPY"] = QuoteState(price=730.0, prev_close=728.0)

    hub._on_equity({"content": [{"key": "SPY", "LAST_PRICE": 1500.0}]})
    state = hub._quotes["SPY"]
    assert state.ext_price is None  # suspect ext print not written
    assert state.price == pytest.approx(730.0)  # regular price still frozen
    assert hub._pending_tick["SPY"] == pytest.approx(1500.0)


@pytest.mark.asyncio
async def test_apply_extended_holds_suspect_price(caplog) -> None:
    """The 30s REST ext poll: a suspect ext price with no corroborating prior poll is
    held (WARNING), keeping a bad print out of state.ext_price; an in-band price
    writes normally."""
    hub = SchwabMarketStreamHub()
    hub._quotes["NVDA"] = QuoteState(price=170.0, prev_close=170.0)

    with caplog.at_level(logging.WARNING):
        await hub.apply_extended(
            "NVDA", ext_price=400.0, ext_change_pct=135.0, market_session="post"
        )
    state = hub._quotes["NVDA"]
    assert state.ext_price is None  # held
    assert any(
        "suspect" in r.message.lower() and "NVDA" in r.message for r in caplog.records
    )

    # An in-band ext price is written normally.
    await hub.apply_extended(
        "NVDA",
        ext_price=172.5,
        ext_change_pct=1.47,
        market_session="post",
        broadcast=False,
    )
    assert hub._quotes["NVDA"].ext_price == pytest.approx(172.5)


@pytest.mark.asyncio
async def test_apply_extended_legit_gap_admits_on_second_poll(monkeypatch) -> None:
    """MAJOR-1 pin: a real +60% after-hours gap is held on poll 1 and admitted on
    poll 2 when the next poll corroborates within 5% — worst case one poll cycle of
    delay. The reference then re-centers on the admitted ext price."""
    hub = SchwabMarketStreamHub()
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)
    hub._quotes["EARN"] = QuoteState(price=100.0, prev_close=100.0)

    # Poll 1: +60% vs prev_close, no prior pending → held.
    await hub.apply_extended(
        "EARN", ext_price=160.0, ext_change_pct=60.0, market_session="post",
        broadcast=False,
    )
    assert hub._quotes["EARN"].ext_price is None

    # Poll 2: corroborates poll 1 within 5% → admitted.
    await hub.apply_extended(
        "EARN", ext_price=161.0, ext_change_pct=61.0, market_session="post",
        broadcast=False,
    )
    assert hub._quotes["EARN"].ext_price == pytest.approx(161.0)

    # Poll 3: in-band vs the admitted ext price (the re-centered ref) → flows.
    await hub.apply_extended(
        "EARN", ext_price=155.0, ext_change_pct=55.0, market_session="post",
        broadcast=False,
    )
    assert hub._quotes["EARN"].ext_price == pytest.approx(155.0)


@pytest.mark.asyncio
async def test_abs_ceiling_blocks_corroborated_walk(monkeypatch) -> None:
    """Backstop pin: corroboration can never admit a print at >10x prev_close —
    a corrupt feed pair at an absurd level stays held even though the two prints
    corroborate each other within 5%, so the rolling reference cannot walk there."""
    hub = SchwabMarketStreamHub()
    monkeypatch.setattr(hub, "_broadcast_quote", _noop_async)
    monkeypatch.setattr(hub, "_check_price_alerts", _noop_async)
    hub._quotes["WALK"] = QuoteState(prev_close=100.0)

    hub._on_equity({"content": [{"key": "WALK", "LAST_PRICE": 1500.0}]})  # absurd, held
    hub._on_equity({"content": [{"key": "WALK", "LAST_PRICE": 1510.0}]})  # corroborates — still held
    assert hub._quotes["WALK"].price == pytest.approx(0.0)

    # Real prints keep flowing normally afterwards.
    hub._on_equity({"content": [{"key": "WALK", "LAST_PRICE": 101.0}]})
    assert hub._quotes["WALK"].price == pytest.approx(101.0)
