"""Sector/industry breakdown: exposure + realized P&L grouped by sector.

Positions come from a mocked ``get_schwab_accounts_sync`` (aggregate-real path, no
network); realized P&L comes from real trade events written to a tmp trades db
(same DB helpers the performance dashboard uses). Sector lookup is mocked via
``signals.sector_breakdown.afetch_company_profile`` (module-level import) — never
hits Finnhub. Nothing here touches the real Schwab API.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from db.trades import create_trade_account, import_trade_events
from imports.base import ParsedTradeEvent
from signals.sector_breakdown import sector_breakdown


def _trade(
    trade_date: str,
    symbol: str,
    side: str,
    quantity: Decimal,
    price: Decimal,
    *,
    key: str,
) -> ParsedTradeEvent:
    amount = quantity * price if side == "sell" else -(quantity * price)
    return ParsedTradeEvent(
        event_type="trade",
        trade_date=date.fromisoformat(trade_date),
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=price,
        fees=Decimal("0"),
        amount=amount,
        description=f"{side} {symbol}",
        external_key=key,
        raw={},
    )


def _seed_real_events(tmp_path: Path) -> Path:
    """AAPL: closed +500 round trip (also currently held). XOM: closed -100 round
    trip, fully flat (no current position). Written under a real (schwab) account
    so ``real_only=True`` picks them up."""
    db_path = tmp_path / "trades.db"
    account = create_trade_account("Real", "schwab", db_path=db_path)
    import_trade_events(
        account["id"],
        [
            _trade("2026-01-01", "AAPL", "buy", Decimal("10"), Decimal("100"), key="a1"),
            _trade("2026-01-05", "AAPL", "sell", Decimal("10"), Decimal("150"), key="a2"),
            _trade("2026-01-02", "XOM", "buy", Decimal("5"), Decimal("80"), key="x1"),
            _trade("2026-01-03", "XOM", "sell", Decimal("5"), Decimal("60"), key="x2"),
        ],
        source="test",
        db_path=db_path,
    )
    return db_path


def _fake_schwab_accounts() -> dict:
    return {
        "authenticated": True,
        "accounts": [
            {
                "id": "schwab_9999",
                "positions": [
                    {"symbol": "AAPL", "market_value": "6000", "unrealized_pl": "200"},
                    {"symbol": "MSFT", "market_value": "3000", "unrealized_pl": "-50"},
                    {"symbol": "ZZZZ", "market_value": "1000", "unrealized_pl": "10"},
                ],
            }
        ],
    }


def _fake_profile(symbol: str) -> dict:
    industries = {"AAPL": "Technology", "MSFT": "Technology", "XOM": "Energy"}
    industry = industries.get(symbol)
    return {"finnhubIndustry": industry} if industry else {}


@pytest.mark.asyncio
async def test_sector_breakdown_groups_and_handles_unclassified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _seed_real_events(tmp_path)
    monkeypatch.setattr(
        "brokers.schwab_accounts.get_schwab_accounts_sync",
        lambda: _fake_schwab_accounts(),
    )
    monkeypatch.setattr(
        "signals.sector_breakdown.afetch_company_profile",
        AsyncMock(side_effect=lambda sym, **_: _fake_profile(sym)),
    )

    result = await sector_breakdown(account_id=None, period="all", db_path=db_path)

    sectors = {s["sector"]: s for s in result["sectors"]}
    assert set(sectors) == {"Technology", "Energy"}

    tech = sectors["Technology"]
    assert tech["market_value"] == "9000"
    assert tech["position_count"] == 2
    assert tech["symbols"] == ["AAPL", "MSFT"]
    assert tech["realized_pnl"] == "500"  # AAPL's closed round trip only
    assert tech["weight_pct"] == pytest.approx(0.9)

    energy = sectors["Energy"]
    assert energy["market_value"] == "0"  # XOM fully closed, no live position
    assert energy["position_count"] == 0
    assert energy["symbols"] == ["XOM"]
    assert energy["realized_pnl"] == "-100"
    assert energy["weight_pct"] == pytest.approx(0.0)

    unclassified = result["unclassified"]
    assert unclassified["symbols"] == ["ZZZZ"]
    assert unclassified["market_value"] == "1000"
    assert unclassified["realized_pnl"] == "0"

    # weight_pct across classified + unclassified sums to ~1.0.
    total_mv = Decimal("10000")  # 9000 tech + 1000 unclassified (XOM contributes 0)
    unclassified_weight = float(Decimal(unclassified["market_value"]) / total_mv)
    weight_sum = sum(s["weight_pct"] for s in result["sectors"]) + unclassified_weight
    assert weight_sum == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_sector_breakdown_empty_when_no_positions_or_trades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "empty.db"
    monkeypatch.setattr(
        "brokers.schwab_accounts.get_schwab_accounts_sync",
        lambda: {"authenticated": True, "accounts": []},
    )
    profile_mock = AsyncMock(return_value={})
    monkeypatch.setattr("signals.sector_breakdown.afetch_company_profile", profile_mock)

    result = await sector_breakdown(account_id=None, period="all", db_path=db_path)

    assert result["sectors"] == []
    assert result["unclassified"] == {
        "symbols": [],
        "market_value": "0",
        "realized_pnl": "0",
    }
    profile_mock.assert_not_called()  # empty universe → no Finnhub calls at all
