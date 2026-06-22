"""Per-strategy universe resolution (decoupled from the active watchlist)."""

from __future__ import annotations

import signals.strategy_universe as su


def test_sp500_builtin_loads():
    syms = su.resolve_universe_spec("sp500")
    assert len(syms) >= 480  # ~503 constituents
    assert "AAPL" in syms and "MSFT" in syms
    assert syms == sorted(set(syms))  # sorted + de-duped


def test_empty_and_none_default_to_sp500():
    assert su.resolve_universe_spec("") == su.resolve_universe_spec("sp500")
    assert su.resolve_universe_spec(None) == su.resolve_universe_spec("sp500")


def test_unknown_spec_falls_back_to_default():
    assert su.resolve_universe_spec("does_not_exist") == su.resolve_universe_spec("sp500")


def test_pinned_watchlist_resolves_by_id(monkeypatch):
    state = {
        "active_id": "main",
        "lists": [
            {"id": "main", "name": "Main", "symbols": ["AAA", "BBB"]},
            {"id": "energy", "name": "Energy", "symbols": ["XLE", "cvx"]},
        ],
    }
    monkeypatch.setattr(su, "get_watchlists_state", lambda: state)
    assert su.resolve_universe_spec("watchlist:energy") == ["CVX", "XLE"]


def test_pinned_watchlist_unknown_id_is_empty(monkeypatch):
    monkeypatch.setattr(
        su, "get_watchlists_state", lambda: {"active_id": "main", "lists": []}
    )
    assert su.resolve_universe_spec("watchlist:ghost") == []


def test_active_watchlist_spec(monkeypatch):
    monkeypatch.setattr(
        su, "get_active_watchlist", lambda: {"id": "main", "symbols": ["spy", "QQQ"]}
    )
    assert su.resolve_universe_spec("active_watchlist") == ["QQQ", "SPY"]


def test_available_universes_lists_builtin_and_watchlists(monkeypatch):
    state = {
        "active_id": "main",
        "lists": [{"id": "main", "name": "Main", "symbols": []}],
    }
    monkeypatch.setattr(su, "get_watchlists_state", lambda: state)
    opts = su.available_universes()
    ids = {o["id"] for o in opts}
    assert "sp500" in ids
    assert "watchlist:main" in ids
    assert any(o["kind"] == "builtin" for o in opts)
    assert any(o["kind"] == "watchlist" for o in opts)
