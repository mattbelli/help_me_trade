"""Per-watchlist display settings (columns/widths/density) normalization,
plus the flow-list slot-cap model (max_slots/pinned, 07-22 game plan)."""

from config.settings import (
    normalize_watchlist_settings,
    normalize_watchlists_state,
    watchlist_capacity_error,
)


def test_settings_rejects_non_dict():
    assert normalize_watchlist_settings(None) is None
    assert normalize_watchlist_settings("compact") is None
    assert normalize_watchlist_settings([]) is None
    assert normalize_watchlist_settings({}) is None


def test_settings_columns_normalized():
    out = normalize_watchlist_settings(
        {
            "columns": [
                {"key": "symbol"},
                {"key": "change_pct", "visible": False, "width": 72},
                {"key": "change_pct"},  # duplicate dropped
                {"key": ""},  # empty dropped
                "not-a-dict",
                {"key": "volume", "width": 5},  # clamped up
                {"key": "pe", "width": 9999.5},  # clamped down, float ok
            ]
        }
    )
    assert out == {
        "columns": [
            {"key": "symbol", "visible": True},
            {"key": "change_pct", "visible": False, "width": 72},
            {"key": "volume", "visible": True, "width": 40},
            {"key": "pe", "visible": True, "width": 600},
        ]
    }


def test_settings_density_enum():
    assert normalize_watchlist_settings({"density": "compact"}) == {"density": "compact"}
    assert normalize_watchlist_settings({"density": "cozy"}) is None


def test_settings_unknown_column_keys_kept():
    # Frontend owns the registry; server must not strip keys it doesn't know.
    out = normalize_watchlist_settings({"columns": [{"key": "future_col"}]})
    assert out == {"columns": [{"key": "future_col", "visible": True}]}


def test_state_carries_settings_through_normalize():
    state = normalize_watchlists_state(
        {
            "active_id": "main",
            "lists": [
                {
                    "id": "main",
                    "name": "Main",
                    "symbols": ["spy", "SPY", "aapl"],
                    "settings": {"density": "spacious", "columns": []},
                },
                {"id": "alt", "name": "Alt", "symbols": [], "settings": "junk"},
            ],
        }
    )
    assert state["lists"][0]["settings"] == {"density": "spacious"}
    assert "settings" not in state["lists"][1]
    assert state["lists"][0]["symbols"] == ["SPY", "AAPL"]


def test_max_slots_and_pinned_normalize():
    state = normalize_watchlists_state(
        {
            "active_id": "flow",
            "lists": [
                {
                    "id": "flow",
                    "name": "Flow",
                    "symbols": ["SPY", "TSLA", "NVDA"],
                    "max_slots": 30,
                    # AAPL isn't a member — its pin must be shed, not kept.
                    "pinned": ["tsla", "AAPL"],
                },
                {"id": "main", "name": "Main", "symbols": ["MU"], "max_slots": -1},
            ],
        }
    )
    flow = state["lists"][0]
    assert flow["max_slots"] == 30
    assert flow["pinned"] == ["TSLA"]
    # Non-positive cap and absent pins stay off the entry entirely.
    assert "max_slots" not in state["lists"][1]
    assert "pinned" not in state["lists"][1]


def test_capacity_counts_unpinned_only():
    # 3 members, 1 pinned → 2 unpinned fits a 2-slot cap (pins ride above it).
    fits = {
        "id": "flow",
        "name": "Flow",
        "symbols": ["SPY", "TSLA", "NVDA"],
        "max_slots": 2,
        "pinned": ["SPY"],
    }
    assert watchlist_capacity_error(fits) is None
    full = {**fits, "pinned": []}
    err = watchlist_capacity_error(full)
    assert err is not None and "3/2" in err
    # No cap on the list → never a capacity error.
    assert watchlist_capacity_error({"id": "main", "symbols": ["A", "B", "C"]}) is None


def test_flow_watch_symbols_pinned_first_and_fallback(monkeypatch):
    import signals.universe as universe

    monkeypatch.setattr(
        universe,
        "get_watchlists_state",
        lambda: {
            "active_id": "flow",
            "lists": [
                {"id": "main", "name": "Main", "symbols": ["ZZZT"]},
                {
                    "id": "flow",
                    "name": "Flow",
                    "symbols": ["AMD", "SPY", "TSLA"],
                    "pinned": ["TSLA"],
                },
            ],
        },
    )
    # Pins ride first so a downstream cap clips unpinned tail, never a pin;
    # the flow list is the SOLE source — no union with other lists.
    assert universe.flow_watch_symbols() == ["TSLA", "AMD", "SPY"]

    monkeypatch.setattr(
        universe,
        "get_watchlists_state",
        lambda: {
            "active_id": "main",
            "lists": [{"id": "main", "name": "Main", "symbols": ["MU", "AMD"]}],
        },
    )
    # No flow list → union fallback keeps the watcher alive.
    assert universe.flow_watch_symbols() == ["AMD", "MU"]
