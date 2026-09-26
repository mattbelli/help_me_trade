"""Perception layer — generic resource query."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai.perception.resources import list_resources, query
from db import base as db_base
from db.chart_features import create_price_alert, init_chart_features
from db.paper_orders import create_pending_proposal, init_paper_order_tables
from db.reports import init_reports_tables, save_report
from db.strategies import init_strategy_tables, seed_default_strategies


@pytest.fixture
def resources_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "resources.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.chart_features.DEFAULT_DB_PATH", db)
    init_chart_features(db)
    init_strategy_tables(db)
    seed_default_strategies(db_path=db)
    init_paper_order_tables(db)
    init_reports_tables(db)
    create_price_alert(symbol="AAPL", price=200.0, note="test")
    create_pending_proposal(
        proposal={"symbol": "NVDA", "side": "buy", "qty": 1, "order_type": "limit"},
        intent_fields={
            "symbol": "NVDA",
            "side": "buy",
            "qty": 1,
            "order_type": "limit",
            "limit_price": 900.0,
            "broker": "alpaca",
            "asset_class": "us_equity",
            "multiplier": "1",
        },
        session_id=None,
        ttl_seconds=3600,
        summary="Buy NVDA limit",
        db_path=db,
    )
    save_report(
        "morning_levels",
        "SPY",
        "SPY morning levels",
        "Body text for preview testing.",
        {"et_date": "2026-06-25"},
        db_path=db,
    )
    return db


def test_list_resources_catalog() -> None:
    catalog = list_resources()
    assert set(catalog) == {
        "watchlists",
        "strategies",
        "price_alerts",
        "proposals",
        "reports",
        "decision_journal",
        "my_trades",
        "schwab_accounts",
        "screener",
        "market",
        "fundamentals",
        "segments",
        "earnings_release",
        "news",
        "calibration",
        "options_flow",
        "options_chain",
        "equity_tape",
        "abnormality",
        "attention",
        "social",
        "index_pressure",
        "flow_report",
        "flow_perception",
        "flow_history",
        "overnight_flow",
        "oi_walls",
        "gex",
        "dossier",
        "episodes",
        "bars",
        "brief_board",
    }
    assert "description" in catalog["strategies"]
    assert "filters" in catalog["strategies"]


@pytest.mark.asyncio
async def test_query_unknown_resource() -> None:
    result = await query("positions")
    assert result["error"] == "unknown resource"
    assert "strategies" in result["available"]


@pytest.mark.asyncio
async def test_query_resource_aliases_resolve(resources_db: Path) -> None:
    # Weaker models (DeepSeek on mobile) guess natural names that differ from the
    # registered keys — the tool guidance itself says "market regime"/"trades"/"alerts".
    # Aliases must resolve, not return {"error":"unknown resource"} and show FAILED (the
    # market_regime-fails-on-mobile-works-on-web bug).
    for alias in ("market_regime", "regime", "trades", "alerts", "journal", "accounts"):
        result = await query(alias, db_path=resources_db)
        assert result.get("error") != "unknown resource", f"alias {alias!r} did not resolve"
    # the market-regime family maps to the real "market" resource and reads cleanly
    for alias in ("market_regime", "regime"):
        assert (await query(alias, db_path=resources_db)).get("resource") == "market"
    assert (await query("vol_scanner", db_path=resources_db)).get("resource") == "abnormality"
    assert (await query("unusual_activity", db_path=resources_db)).get("resource") == "abnormality"


@pytest.mark.asyncio
async def test_query_strategies(
    resources_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", resources_db)
    result = await query("strategies", db_path=resources_db)
    assert result["resource"] == "strategies"
    assert result["count"] >= 1
    assert "enabled" in result["rows"][0]


@pytest.mark.asyncio
async def test_query_watchlists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ai.perception.resources.get_watchlists_state",
        lambda: {
            "active_id": "main",
            "lists": [{"id": "main", "name": "Main", "symbols": ["AAPL", "NVDA"]}],
        },
    )
    result = await query("watchlists")
    assert result["count"] == 1
    assert result["rows"][0]["symbols"] == ["AAPL", "NVDA"]


@pytest.mark.asyncio
async def test_query_price_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ai.perception.resources.list_price_alerts",
        lambda **kw: [
            {
                "id": 1,
                "symbol": "AAPL",
                "price": 200.0,
                "direction": "cross",
                "note": "test",
                "repeat": 0,
                "triggered": 0,
            }
        ]
        if kw.get("symbol") == "AAPL"
        else [],
    )
    result = await query("price_alerts", {"symbol": "AAPL"})
    assert result["count"] == 1
    assert result["rows"][0]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_query_proposals(
    resources_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", resources_db)
    result = await query("proposals", db_path=resources_db)
    assert result["count"] == 1
    assert result["rows"][0]["symbol"] == "NVDA"
    assert result["rows"][0]["status"] == "pending_phone"


@pytest.mark.asyncio
async def test_query_reports(
    resources_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", resources_db)
    result = await query("reports", {"limit": 5}, db_path=resources_db)
    assert result["count"] == 1
    assert result["rows"][0]["kind"] == "morning_levels"
    assert "preview" in result["rows"][0]


@pytest.mark.asyncio
async def test_query_fail_soft_on_reader_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = {
        "description": "broken",
        "filters": {},
        "read": MagicMock(side_effect=RuntimeError("boom")),
    }
    monkeypatch.setitem(
        __import__("ai.perception.resources", fromlist=["RESOURCES"]).RESOURCES,
        "watchlists",
        entry,
    )
    result = await query("watchlists")
    assert result["resource"] == "watchlists"
    assert "boom" in result["error"]
    assert result["rows"] == []


@pytest.mark.asyncio
async def test_query_screener_compact_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_scan(preset_id, universe, get_bars, *, limit=50):
        del preset_id, universe, get_bars, limit
        return {
            "results": [
                {
                    "symbol": "NVDA",
                    "price": 900.0,
                    "change_pct": 2.5,
                    "rvol": 1.8,
                    "value": 28.0,
                }
            ]
        }

    monkeypatch.setattr("signals.screener.run_scan", fake_run_scan)
    monkeypatch.setattr(
        "api.scan_routes._screen_universe",
        lambda: ["NVDA"],
    )
    monkeypatch.setattr(
        "api.scan_routes._make_bars_getter",
        lambda: (lambda _sym: None),
    )

    result = await query("screener", {"preset": "oversold", "limit": 10})
    assert result["resource"] == "screener"
    assert result["count"] == 1
    row = result["rows"][0]
    assert row["symbol"] == "NVDA"
    assert row["price"] == 900.0
    assert row["change_pct"] == 2.5
    assert row["rvol"] == 1.8
    assert "RSI" in row["reason"]


@pytest.mark.asyncio
async def test_query_market_regime_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from datetime import date

    db = tmp_path / "market.db"
    from db.regime import upsert_regime

    today = date.today().isoformat()
    upsert_regime(
        [
            ("DGS10", today, 4.41),
            ("DGS2", today, 4.10),
            ("VIXCLS", today, 18.92),
        ],
        db_path=db,
    )
    result = await query("market", db_path=db)
    assert result["count"] == 1
    row = result["rows"][0]
    assert row["term_spread"] == pytest.approx(0.31)
    assert row["vix"] == pytest.approx(18.92)
    assert "regime_label" in row
    assert row["fresh"] is True


@pytest.mark.asyncio
async def test_query_news_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(*, limit=50, symbol=None):
        del limit
        if symbol == "AAPL":
            return [
                {
                    "headline": "Apple news",
                    "source": "Reuters",
                    "datetime": "2026-06-24T12:00:00+00:00",
                    "url": "https://example.com/aapl",
                    "symbol": "AAPL",
                }
            ]
        return []

    monkeypatch.setattr(
        "signals.news.fetch_watchlist_news",
        fake_fetch,
    )
    result = await query("news", {"symbol": "AAPL", "limit": 5})
    assert result["count"] == 1
    assert result["rows"][0]["headline"] == "Apple news"


@pytest.mark.asyncio
async def test_query_calibration_passthrough(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = {
        "n_resolved": 3,
        "min_samples": 5,
        "by_strategy": [],
        "by_bucket": [],
    }
    monkeypatch.setattr(
        "ai.journal_review.compute_calibration",
        lambda **_: payload,
    )
    result = await query("calibration", db_path=tmp_path / "cal.db")
    assert result["count"] == 1
    assert result["rows"][0]["n_resolved"] == 3


@pytest.mark.asyncio
async def test_query_options_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_chain = {
        "symbol": "SPY",
        "underlying_price": 620.5,
        "expirations": ["2026-07-10", "2026-07-17"],
        "expiry": "2026-07-10",
        "rows": [{"strike": 620.0, "call": {"bid": 1.2}, "put": {"bid": 1.1}}],
        "as_of": "2026-07-10T19:59:59Z",
    }
    calls: dict = {}

    def fake_build(broker, symbol, expiry, strikes):
        calls["args"] = (symbol, expiry, strikes)
        return fake_chain

    monkeypatch.setattr("brokers.factory.get_broker", lambda _name: object())
    monkeypatch.setattr(
        "brokers.options_chain.build_options_chain_sync", fake_build
    )

    result = await query(
        "options_chain",
        {"symbol": "spy", "expiration": "2026-07-10", "strikes": 100},
        db_path=tmp_path / "chain.db",
    )
    assert result["count"] == 1
    assert result["rows"][0]["expiry"] == "2026-07-10"
    # symbol upcased, expiry passed through, strikes clamped to the LLM cap
    assert calls["args"] == ("SPY", "2026-07-10", 30)


@pytest.mark.asyncio
async def test_query_options_chain_requires_symbol(tmp_path: Path) -> None:
    result = await query("options_chain", {}, db_path=tmp_path / "chain.db")
    assert result["count"] == 1
    assert "symbol" in str(result["rows"][0].get("error"))


@pytest.mark.asyncio
async def test_query_options_chain_aliases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("brokers.factory.get_broker", lambda _name: None)
    for alias in ("chain", "option_chain", "greeks"):
        result = await query(alias, {"symbol": "SPY"}, db_path=tmp_path / "c.db")
        assert result.get("resource") == "options_chain", alias


# --- macro substrate reaches the agents (wire-it-everywhere) --------------------


def _fake_report(today: str) -> dict:
    return {
        "dates": 1,
        "axes": {
            "credit": {
                "series": "BAMLH0A0HYM2",
                "history": {"n": 1309, "first": "2021-07-29", "last": today},
                "composition": {"low": 1, "mid": 0, "high": 0, "missing": 0},
                "per_date": {
                    today: {
                        "value": 2.84,
                        "date_used": "2026-07-28",
                        "pctile": 16.2,
                        "bucket": "low",
                    }
                },
            },
            "dollar": {  # no observation in range — must be omitted, not None-filled
                "series": "DTWEXBGS",
                "history": {"n": 0, "first": None, "last": None},
                "composition": {"low": 0, "mid": 0, "high": 0, "missing": 1},
                "per_date": {today: {"value": None, "date_used": None}},
            },
        },
    }


def test_macro_block_carries_value_bucket_and_reference_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credit spreads reach the agent WITH the vendor-truncation disclosure — a
    16th-percentile OAS off 4 years of history is not the same claim as off 30."""
    # ET, matching _macro_block — NOT date.today(). serv runs UTC, so every night
    # between 20:00 ET and midnight the box is already on tomorrow's date and a fake
    # report keyed to it lands on a date the code correctly never asks for. Caught by
    # a real 08-11 -> 08-12 rollover mid-session.
    from datetime import datetime as _datetime

    import signals.macro_substrate as macro
    from ai.perception.resources import _macro_block
    from trading.market_hours import _ET

    today = _datetime.now(_ET).date().isoformat()
    monkeypatch.setattr(macro, "regime_report", lambda dates: _fake_report(today))
    monkeypatch.setattr(macro, "macro_snapshot", lambda: {"DGS10": {"axis": "curve", "value": 4.61}})

    block = _macro_block()
    assert block is not None
    credit = block["axes"]["credit"]
    assert credit["value"] == 2.84 and credit["bucket"] == "low"
    assert credit["history_since"] == "2021-07-29" and credit["history_n"] == 1309
    # Stale/missing axes are STATED, not dropped — the same contract
    # signals/dossier.py already documents for this data. Dropping them used to
    # empty `axes`, and an empty `axes` returned None for the ENTIRE block,
    # silently taking the raw series with it; a week-long FRED gap (a government
    # shutdown does exactly that) removed the whole macro surface from the agent.
    assert block["axes"]["dollar"]["value"] is None
    assert block["axes"]["dollar"]["series"] == "DTWEXBGS"
    assert block["series"]["DGS10"]["value"] == 4.61  # full curve, not just our picked axes
    assert "calibrated signal" in block["note"]  # the disclaimer, not its exact prose


def test_macro_block_survives_every_axis_going_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The raw series outlive the axes. Previously `if not axes: return None`
    discarded 29 live series because the six axes were past their staleness
    window — the data was all still there."""
    import signals.macro_substrate as macro
    from ai.perception.resources import _macro_block

    monkeypatch.setattr(macro, "regime_report", lambda dates: {"axes": {}})
    monkeypatch.setattr(
        macro, "macro_snapshot", lambda: {"OVXCLS": {"axis": "vol_cross", "value": 56.06}}
    )
    block = _macro_block()
    assert block is not None and block["series"]["OVXCLS"]["value"] == 56.06


@pytest.mark.asyncio
async def test_market_read_includes_macro_and_survives_its_absence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """term_spread + VIX are the contract; the substrate is enrichment. A missing
    macro.db must not sink the regime read the whole app conditions on."""
    import signals.regime as regime_mod
    from db import regime as db_regime

    monkeypatch.setattr(
        regime_mod, "market_regime", lambda **kw: {"as_of": "2026-07-28", "term_spread": 0.35, "vix": 18.21}
    )
    monkeypatch.setattr(regime_mod, "regime_is_fresh", lambda **kw: True)
    monkeypatch.setattr(db_regime, "recent_observations", lambda *a, **k: [])

    monkeypatch.setattr("ai.perception.resources._macro_block", lambda: {"axes": {"credit": {"value": 2.84}}})
    row = (await query("market", db_path=tmp_path / "m.db"))["rows"][0]
    assert row["term_spread"] == 0.35 and row["macro"]["axes"]["credit"]["value"] == 2.84

    def _boom() -> dict:
        raise FileNotFoundError("no macro.db")

    monkeypatch.setattr("ai.perception.resources._macro_block", _boom)
    row = (await query("market", db_path=tmp_path / "m.db"))["rows"][0]
    assert row["term_spread"] == 0.35 and "macro" not in row


# --- the registry is the ONE list every agent surface quotes (2026-07-30) ----------


def test_every_resource_is_named_to_the_agent() -> None:
    """A resource the model is never told about is unreachable in practice: it has
    no reason to guess the key. Hand-copied lists had rotted to the same stale
    9 of 19 — fundamentals, options_flow, equity_tape, my_trades and schwab_accounts
    among the invisible. Every surface must DERIVE its list from RESOURCES, so this
    fails the moment someone hand-writes one again."""
    from ai.chat.tools import _QUERY_RESOURCES, TOOL_DEFINITIONS
    from ai.perception.context import list_capabilities

    names = set(list_resources())
    assert len(names) >= 19  # non-vacuous: an empty registry must not pass trivially

    for surface, text in (
        ("query tool description", TOOL_DEFINITIONS["query"]["description"]),
        ("perception manifest notes", list_capabilities()["notes"]),
    ):
        missing = sorted(n for n in names if n not in text)
        assert not missing, f"{surface} never names: {missing}"
        # ...verbatim, not by coincidence: short keys like `news` and `market` appear
        # in unrelated prose, so a name-by-name check alone could pass a stale list.
        assert _QUERY_RESOURCES in text, f"{surface} does not carry the derived list"


# --- bars: shape only, never a substitute for the computed numbers (2026-08-03) ----


def _bars_provider(count: int) -> MagicMock:
    from unittest.mock import AsyncMock

    from brokers.alpaca import StockBar

    bars = [
        StockBar(
            time=f"2026-07-{(i % 28) + 1:02d}T20:00:00Z",
            open=100.0 + i,
            high=110.0 + i,
            low=90.0 + i,
            close=105.0 + i,
            volume=1_000.0,
        )
        for i in range(count)
    ]
    provider = MagicMock()
    provider.aget_stock_bars = AsyncMock(return_value=bars)
    return provider


@pytest.mark.asyncio
async def test_query_bars_returns_window_and_its_extremes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _bars_provider(60)
    monkeypatch.setattr("brokers.factory.get_market_data_provider", lambda: provider)

    row = (
        await query(
            "bars", {"symbol": "tsm", "timeframe": "1Week", "limit": 10},
            db_path=tmp_path / "b.db",
        )
    )["rows"][0]

    assert row["symbol"] == "TSM" and row["timeframe"] == "1Week"
    assert row["count"] == 10 and len(row["bars"]) == 10
    # The window is the LAST 10 bars, and its extremes are computed here — the model
    # must never have to derive them by reading the candles.
    assert row["period_high"] == 169.0
    assert row["period_low"] == 140.0
    assert row["last_close"] == 164.0
    assert provider.aget_stock_bars.await_args.kwargs["timeframe"] == "1Week"


@pytest.mark.asyncio
async def test_query_bars_clamps_limit_and_requires_symbol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _bars_provider(500)
    monkeypatch.setattr("brokers.factory.get_market_data_provider", lambda: provider)

    capped = (
        await query("bars", {"symbol": "SPY", "limit": 9999}, db_path=tmp_path / "b.db")
    )["rows"][0]
    assert capped["count"] == 120, "limit must clamp — bars are context, not a dump"

    missing = (await query("bars", {}, db_path=tmp_path / "b.db"))["rows"][0]
    assert "error" in missing


@pytest.mark.asyncio
async def test_query_bars_rejects_unknown_timeframe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unsupported timeframe must name the supported set rather than silently
    falling back to daily and returning the wrong candles under the right label."""
    provider = _bars_provider(10)
    monkeypatch.setattr("brokers.factory.get_market_data_provider", lambda: provider)

    row = (
        await query(
            "bars", {"symbol": "SPY", "timeframe": "3Fortnight"}, db_path=tmp_path / "b.db"
        )
    )["rows"][0]

    assert "error" in row and "1Week" in row["supported"]
    provider.aget_stock_bars.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_bars_aliases_resolve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "brokers.factory.get_market_data_provider", lambda: _bars_provider(20)
    )
    for alias in ("candles", "ohlc", "ohlcv"):
        result = await query(alias, {"symbol": "SPY"}, db_path=tmp_path / "b.db")
        assert result["resource"] == "bars", f"{alias} must resolve to bars"


def test_bars_description_forbids_llm_derived_levels() -> None:
    """The whole point of the resource: candles answer SHAPE, never arithmetic. On
    08-03 the copilot, denied a prior-week high, reverse-engineered a level out of a
    rounded percentage. If this steer leaves the description, that returns."""
    desc = list_resources()["bars"]["description"]
    assert "Do NOT compute" in desc
    assert "PWH" in desc and "52WH" in desc
