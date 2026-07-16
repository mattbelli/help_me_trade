"""Per-watchlist display settings (columns/widths/density) normalization."""

from config.settings import normalize_watchlist_settings, normalize_watchlists_state


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
