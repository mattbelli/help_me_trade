"""Tests for the RVOL-spike and Schwab-health background monitor loops.

Focuses on the pure dedup logic (_should_rvol_alert / _mark_rvol_alerted) and
the loop iteration helpers, without exercising network or APNs plumbing.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

_ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# RVOL dedup helpers  (_should_rvol_alert / _mark_rvol_alerted)
# ---------------------------------------------------------------------------


def test_should_rvol_alert_new_symbol():
    """A symbol never seen before should always trigger an alert."""
    from api.main import _rvol_alert_last_date, _should_rvol_alert

    # Ensure clean state for this symbol.
    _rvol_alert_last_date.pop("NEWTEST", None)

    today = date(2026, 6, 24)
    assert _should_rvol_alert("NEWTEST", today) is True


def test_should_rvol_alert_same_day_blocks():
    """After marking alerted, the same symbol should NOT alert again the same day."""
    from api.main import _mark_rvol_alerted, _should_rvol_alert

    today = date(2026, 6, 24)
    sym = "DEDUP_SAME_DAY"

    _mark_rvol_alerted(sym, today)
    assert _should_rvol_alert(sym, today) is False


def test_should_rvol_alert_next_day_allows():
    """The next ET day the symbol should be allowed to alert again."""
    from api.main import _mark_rvol_alerted, _should_rvol_alert

    yesterday = date(2026, 6, 23)
    today = date(2026, 6, 24)
    sym = "DEDUP_NEXT_DAY"

    _mark_rvol_alerted(sym, yesterday)
    # A different day → should alert.
    assert _should_rvol_alert(sym, today) is True


def test_mark_rvol_alerted_updates_state():
    """_mark_rvol_alerted should update the module-level dict."""
    from api.main import _mark_rvol_alerted, _rvol_alert_last_date

    today = date(2026, 6, 24)
    sym = "MARK_TEST"

    _mark_rvol_alerted(sym, today)
    assert _rvol_alert_last_date[sym] == today


def test_multiple_symbols_dedup_independently():
    """Marking one symbol does not affect another symbol's dedup state."""
    from api.main import _mark_rvol_alerted, _rvol_alert_last_date, _should_rvol_alert

    today = date(2026, 6, 24)
    _rvol_alert_last_date.pop("SYM_A", None)
    _rvol_alert_last_date.pop("SYM_B", None)

    _mark_rvol_alerted("SYM_A", today)

    # SYM_A alerted — should block.
    assert _should_rvol_alert("SYM_A", today) is False
    # SYM_B untouched — should allow.
    assert _should_rvol_alert("SYM_B", today) is True


@pytest.mark.asyncio
async def test_rvol_alert_once_calls_notify_with_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """RVOL spike path emits through notify() with kind=rvol and push enabled."""
    from api.main import _rvol_alert_last_date, _rvol_alert_once

    _rvol_alert_last_date.pop("AAPL", None)

    notify_calls: list[dict] = []

    async def _fake_notify(**kwargs):
        notify_calls.append(kwargs)
        return {"id": "rvol-test"}

    class _FakeProvider:
        async def aget_quotes(self, symbols):
            return {
                "AAPL": {
                    "total_volume": 300_000.0,
                    "regular_last": 150.25,
                }
            }

    from brokers.schwab_market_data import SchwabMarketData

    provider = object.__new__(SchwabMarketData)
    provider.aget_quotes = _FakeProvider().aget_quotes  # type: ignore[method-assign]

    monkeypatch.setattr("api.main._RVOL_ALERTS_ENABLED", True)
    monkeypatch.setattr("api.main._market_session", lambda: "regular")
    monkeypatch.setattr("api.main._rvol_bucket_now", lambda: 38)
    monkeypatch.setattr("api.main._all_watchlist_symbols", lambda: ["AAPL"])
    monkeypatch.setattr("api.main.get_market_data_provider", lambda: provider)
    monkeypatch.setattr(
        "api.main.load_profile",
        lambda sym: {
            "avg_volume_by_bucket": [10_000.0] * 78,
        },
    )
    monkeypatch.setattr(
        "api.main._compute_rvol_from_total_volume",
        lambda profile, total_volume, bucket: 4.0,
    )
    monkeypatch.setattr(
        "api.main.datetime",
        type(
            "FixedDatetime",
            (),
            {
                "now": staticmethod(
                    lambda tz=None: datetime(2026, 6, 24, 11, 0, tzinfo=_ET)
                )
            },
        ),
    )
    monkeypatch.setattr("signals.notify.notify", _fake_notify)

    await _rvol_alert_once()

    assert len(notify_calls) == 1
    call = notify_calls[0]
    assert call["kind"] == "rvol"
    assert call.get("push", True) is True
    assert call["data"]["symbol"] == "AAPL"
    assert call["data"]["rvol"] == pytest.approx(4.0)
    assert call["data"]["price"] == pytest.approx(150.25)


# ---------------------------------------------------------------------------
# Environment flag defaults
# ---------------------------------------------------------------------------


def test_rvol_alert_threshold_default():
    """RVOL_ALERT_THRESHOLD must default to 3.0 (high enough to suppress noise)."""
    from api.main import RVOL_ALERT_THRESHOLD

    assert RVOL_ALERT_THRESHOLD == 3.0


def test_rvol_alert_interval_default():
    """RVOL_ALERT_INTERVAL_SECONDS must default to 300 (5 min polling)."""
    import os

    if "RVOL_ALERT_INTERVAL_SECONDS" not in os.environ:
        from api.main import RVOL_ALERT_INTERVAL_SECONDS

        assert RVOL_ALERT_INTERVAL_SECONDS == 300


def test_schwab_health_interval_default():
    """SCHWAB_HEALTH_INTERVAL_SECONDS must default to 21600 (6 h)."""
    import os

    if "SCHWAB_HEALTH_INTERVAL_SECONDS" not in os.environ:
        from api.main import SCHWAB_HEALTH_INTERVAL_SECONDS

        assert SCHWAB_HEALTH_INTERVAL_SECONDS == 6 * 3600
