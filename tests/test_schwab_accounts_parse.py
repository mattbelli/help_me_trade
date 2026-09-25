"""Schwab position parsing — day P/L when a position was partly sold today."""

from brokers.schwab_accounts import _parse_positions


def _item(**over) -> dict:
    # Real 2026-09-24 payload: 200 TSLA overnight, 100 sold at 377.82 today.
    item = {
        "longQuantity": 100.0,
        "shortQuantity": 0.0,
        "previousSessionLongQuantity": 200.0,
        "averagePrice": 379.6872,
        "marketValue": 37713.5,
        "longOpenProfitLoss": -255.22,
        "currentDayProfitLoss": -49867.08,
        "currentDayCost": 11556.58,
        "instrument": {"assetType": "EQUITY", "symbol": "TSLA", "netChange": -2.985},
    }
    item.update(over)
    return item


def test_partial_sell_today_ignores_schwab_day_pl():
    (p,) = _parse_positions({"positions": [_item()]})
    assert p["day_pl"] == -298.5  # 100 held shares x -2.985


def test_unchanged_position_keeps_schwab_day_pl():
    (p,) = _parse_positions(
        {"positions": [_item(previousSessionLongQuantity=100.0, currentDayProfitLoss=-298.5)]}
    )
    assert p["day_pl"] == -298.5


def test_added_to_today_keeps_schwab_day_pl():
    (p,) = _parse_positions(
        {"positions": [_item(previousSessionLongQuantity=50.0, currentDayProfitLoss=-120.0)]}
    )
    assert p["day_pl"] == -120.0
