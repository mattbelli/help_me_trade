"""Portfolio analytics derived from snapshots."""

from __future__ import annotations

from portfolio.analytics import compute_portfolio_analytics


def test_compute_weights_and_concentration_flag() -> None:
    snapshot = {
        "broker": "alpaca",
        "captured_at": "2026-06-16T12:00:00+00:00",
        "account": {
            "portfolio_value": 100_000.0,
            "cash": 10_000.0,
            "buying_power": 20_000.0,
        },
        "positions": [
            {
                "symbol": "NVDA",
                "qty": 100,
                "market_value": 30_000.0,
                "unrealized_pl": 2_000.0,
                "current_price": 300.0,
            },
            {
                "symbol": "AAPL",
                "qty": 200,
                "market_value": 60_000.0,
                "unrealized_pl": -500.0,
                "current_price": 300.0,
            },
        ],
    }

    analytics = compute_portfolio_analytics(snapshot)

    assert analytics["total_equity"] == 100_000.0
    assert analytics["cash_pct"] == 10.0
    assert analytics["largest_position_pct"] == 60.0
    assert analytics["top_holdings"][0]["symbol"] == "AAPL"
    assert analytics["winners"] == ["NVDA"]
    assert analytics["losers"] == ["AAPL"]
    assert any("Concentrated" in flag for flag in analytics["flags"])


def test_exposure_drift_vs_prior_snapshot() -> None:
    prior = {
        "account": {"portfolio_value": 100_000.0},
        "positions": [
            {"symbol": "NVDA", "market_value": 10_000.0},
            {"symbol": "AAPL", "market_value": 80_000.0},
        ],
    }
    current = {
        "broker": "alpaca",
        "captured_at": "2026-06-16T12:20:00+00:00",
        "account": {"portfolio_value": 100_000.0, "cash": 10_000.0},
        "positions": [
            {"symbol": "NVDA", "market_value": 20_000.0, "unrealized_pl": 100.0},
            {"symbol": "AAPL", "market_value": 70_000.0, "unrealized_pl": -50.0},
        ],
    }

    analytics = compute_portfolio_analytics(current, prior)

    assert analytics["exposure_drift_pct"]["NVDA"] == 10.0
    assert analytics["equity_change_since_prior"] == 0.0


def test_exposure_drift_vs_week_ago() -> None:
    week_ago = {
        "account": {"portfolio_value": 100_000.0},
        "positions": [
            {"symbol": "NVDA", "market_value": 10_000.0},
        ],
    }
    current = {
        "broker": "alpaca",
        "captured_at": "2026-06-16T12:00:00+00:00",
        "account": {"portfolio_value": 100_000.0, "cash": 10_000.0},
        "positions": [
            {"symbol": "NVDA", "market_value": 20_000.0, "unrealized_pl": 100.0},
        ],
    }
    analytics = compute_portfolio_analytics(current, week_ago_snapshot=week_ago)
    assert analytics["exposure_drift_vs_week_ago_pct"]["NVDA"] == 10.0
    assert any("7d ago" in flag for flag in analytics["flags"])


def test_snapshot_to_context_includes_analytics() -> None:
    from ai.insights import snapshot_to_context

    snapshot = {
        "broker": "alpaca",
        "captured_at": "2026-06-16T12:00:00+00:00",
        "account": {"portfolio_value": 50_000.0, "cash": 5_000.0},
        "positions": [
            {"symbol": "SPY", "market_value": 45_000.0, "unrealized_pl": 250.0},
        ],
        "open_orders": [],
    }

    payload = snapshot_to_context(snapshot)
    assert '"analytics"' in payload
    assert '"top_holdings"' in payload
