"""Overnight ext-quote staleness guard.

Regression: 2026-07-14 20:14 ET the watchlist + chart showed NTSK at 12.78
(-10.44%) while the stock had closed at 14.27, +11% on the day. The overnight
venue had no NTSK trades that night, and its snapshot served the last trade it
ever saw — a 100-share print from the PRIOR overnight session, 20.6h stale and
superseded by an entire regular session.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from brokers.alpaca import AlpacaAdapter

ET = ZoneInfo("America/New_York")

# The real payload shape, with NTSK's real numbers.
_GHOST_TRADE_AT = "2026-07-14T03:35:49.123456789Z"  # 23:35 ET on 07-13
_LIVE_TRADE_AT = "2026-07-14T23:52:40.898938015Z"  # 19:52 ET on 07-14
_LAST_CLOSE = datetime(2026, 7, 14, 16, tzinfo=ET)  # today's 16:00 ET close


def _snap(price: float, at: str | None) -> dict:
    trade: dict = {"p": price}
    if at is not None:
        trade["t"] = at
    return {
        "latestTrade": trade,
        "latestQuote": {"bp": price - 0.02, "ap": price + 0.02},
        "dailyBar": {"c": 14.27, "t": "2026-07-14T04:00:00Z"},
        "prevDailyBar": {"c": 12.80, "t": "2026-07-13T04:00:00Z"},
    }


def test_stale_overnight_print_is_rejected() -> None:
    """The ghost: last night's 12.78 must not be served as tonight's price."""
    out = AlpacaAdapter._parse_ext_quote_snapshot(
        _snap(12.78, _GHOST_TRADE_AT), not_before=_LAST_CLOSE
    )
    assert out is None


def test_fresh_overnight_print_is_kept() -> None:
    out = AlpacaAdapter._parse_ext_quote_snapshot(
        _snap(14.4448, _LIVE_TRADE_AT), not_before=_LAST_CLOSE
    )
    assert out is not None
    assert out["ext_last"] == 14.4448
    # vs today's regular close (14.27), not yesterday's (12.80).
    assert out["ext_change_pct"] == 1.22


def test_untimed_trade_is_rejected_when_guarded() -> None:
    """No timestamp = no freshness proof; a stale bid/ask must not stand in."""
    assert (
        AlpacaAdapter._parse_ext_quote_snapshot(
            _snap(12.78, None), not_before=_LAST_CLOSE
        )
        is None
    )


def test_unparseable_timestamp_is_rejected_when_guarded() -> None:
    assert (
        AlpacaAdapter._parse_ext_quote_snapshot(
            _snap(12.78, "not-a-timestamp"), not_before=_LAST_CLOSE
        )
        is None
    )


def test_guard_is_opt_in_daytime_path_unchanged() -> None:
    """Without not_before (pre/post/regular), behaviour is exactly as before."""
    out = AlpacaAdapter._parse_ext_quote_snapshot(_snap(12.78, _GHOST_TRADE_AT))
    assert out is not None
    assert out["ext_last"] == 12.78
