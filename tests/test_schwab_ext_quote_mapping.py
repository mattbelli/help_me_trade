"""Regression: the ext_last candidate race in Schwab quote mapping.

Pre-market, Schwab keeps bumping the consolidated `quote` block's tradeTime
while its lastPrice stays frozen at the prior regular close. That stale echo
was winning the freshest-timestamp race against LIVE extended trades, so the
REST poll ping-ponged state.ext_price against the stream's real ext ticks —
every disagreement fired a phantom price-alert cross (NVDA 195.55 vs ~191,
2026-07-07 pre-market spam).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from brokers.schwab_market_data import SchwabMarketData


def _make_md(payload: dict) -> SchwabMarketData:
    md = SchwabMarketData.__new__(SchwabMarketData)
    client = MagicMock()
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    client.get_quotes.return_value = response
    md._client = client
    return md


def _nvda_payload(
    *,
    quote_last: float,
    quote_time: float,
    ext_last: float | None,
    ext_time: float | None,
    ext_live: bool,
    reg_last: float,
) -> dict:
    extended: dict = {}
    if ext_last is not None:
        extended = {
            "lastPrice": ext_last,
            "tradeTime": ext_time,
            "bidPrice": ext_last - 0.05 if ext_live else 0,
            "askPrice": ext_last + 0.05 if ext_live else 0,
            "totalVolume": 120000 if ext_live else 0,
        }
    return {
        "NVDA": {
            "regular": {"regularMarketLastPrice": reg_last},
            "quote": {
                "lastPrice": quote_last,
                "tradeTime": quote_time,
                "closePrice": 195.59,
                "totalVolume": 0,
            },
            "extended": extended,
        }
    }


def test_stale_close_echo_never_outraces_live_ext_trade() -> None:
    """The exact spam shape: quote block echoes the regular close (195.55) with a
    FRESHER tradeTime; a live extended trade sits at ~191. The live ext trade must
    win — the echo carries no ext information."""
    md = _make_md(
        _nvda_payload(
            quote_last=195.55,
            quote_time=2_000.0,  # fresher timestamp on the stale echo
            ext_last=191.02,
            ext_time=1_000.0,
            ext_live=True,
            reg_last=195.55,
        )
    )
    out = md._fetch_quotes_sync(["NVDA"])
    assert out["NVDA"]["ext_last"] == 191.02


def test_fresher_quote_block_still_wins_when_not_an_echo() -> None:
    """Weekday post-market the quote block is genuinely live (its lastPrice is a
    real post-market trade != the regular close) — freshest-by-time still wins."""
    md = _make_md(
        _nvda_payload(
            quote_last=192.40,  # a real ext trade, not the close
            quote_time=2_000.0,
            ext_last=191.02,
            ext_time=1_000.0,
            ext_live=True,
            reg_last=195.55,
        )
    )
    out = md._fetch_quotes_sync(["NVDA"])
    assert out["NVDA"]["ext_last"] == 192.40


def test_echo_is_fine_when_no_live_ext_market_exists() -> None:
    """Overnight with no ext trades at all: echoing the close is stable (no
    competing value to ping-pong against) and stays the fallback."""
    md = _make_md(
        _nvda_payload(
            quote_last=195.55,
            quote_time=2_000.0,
            ext_last=190.0,
            ext_time=1_500.0,
            ext_live=False,  # stale extended block: zero bid/ask/volume
            reg_last=195.55,
        )
    )
    out = md._fetch_quotes_sync(["NVDA"])
    assert out["NVDA"]["ext_last"] == 195.55
