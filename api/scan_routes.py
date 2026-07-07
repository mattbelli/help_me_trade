"""Universe screener API — list scan presets, run a preset scan (read-only).

A scan screens a BOUNDED universe (watchlist ∪ open positions) against a
preset's technical criteria and returns ranked matches. It reuses the app's
already-cached daily bars — the same ``_BARS_CACHE`` the chart endpoint and its
prewarm loop keep warm — so it never bursts Schwab (see ``schwab-data-ops``).
It places no orders and creates no alerts; it's a live read-only screen.

Registered by ``api/main`` via ``app.include_router`` (behind SecurityMiddleware).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from signals.indicators import Bar
from signals.screener import PRESETS, get_preset, preset_summary, run_scan
from trading.market_hours import _ET

router = APIRouter(prefix="/api/scan", tags=["scan"])

# Hard ceiling on how many symbols a single scan may touch. The watchlist
# universe is already bounded to watchlist ∪ open positions; this is a
# belt-and-suspenders cap so the LIVE screener can never fan out into a broker
# burst. It does NOT apply to the ``sp500`` universe, which reads a local store
# (no broker) and is meant to span ~500 names.
_MAX_UNIVERSE = 120
_SCAN_BARS_TIMEFRAME = "1Day"
_SCAN_BARS_DAYS = 365  # ~250 trading days — covers 200-DMA and the 52-week range

# The universes a scan may screen. ``watchlist`` = live/cached bars (bounded);
# ``sp500`` = the persisted momentum daily-bars store (read-only, no broker).
_UNIVERSES: tuple[str, ...] = ("watchlist", "sp500")
_SP500_VOLUME_NOTE = (
    "Volume scans use the Watchlist universe — the S&P 500 store keeps only "
    "open/close, so it has no volume to screen."
)


def _screen_universe() -> list[str]:
    """The bounded set a scan screens: watchlist symbols ∪ open-position symbols.

    Both come from data the app already holds — the persisted watchlists and the
    latest polled snapshot in SQLite — so building the universe hits no broker.
    Futures (``/`` prefix) are dropped: the screener's technicals are equity
    daily-bar screens.
    """
    # Lazy imports keep the router decoupled from api.main (avoids a circular
    # import at module load — api.main registers this router).
    from api.main import _all_watchlist_symbols
    from db.sqlite import get_latest_snapshot

    symbols: set[str] = set(_all_watchlist_symbols())
    snapshot = get_latest_snapshot()
    if snapshot:
        for position in snapshot.get("positions") or []:
            sym = position.get("symbol")
            if isinstance(sym, str) and sym.strip():
                symbols.add(sym.strip().upper())

    return sorted(s for s in symbols if not s.startswith("/"))[:_MAX_UNIVERSE]


def _make_bars_getter():
    """Return an async ``get_bars`` bound to the app's cached daily-bars path.

    Reuses ``api.main._get_bars_cached`` — cache-read first, fetching only on a
    miss — the very function the chart endpoint and prewarm loop use. Watchlist
    symbols are already kept warm by the prewarm loop, so scans are served from
    cache; at most a handful of cold open-position symbols trigger a paced,
    per-symbol fetch (never a concurrent burst).
    """
    from api.main import _get_bars_cached  # lazy: avoid circular import

    async def _get(symbol: str) -> list[Bar] | None:
        # RTH-only, pinned explicitly: the scan's daily inputs (200-DMA, 52-week
        # range) must stay regular-session-consistent, never ext-inclusive.
        response = await _get_bars_cached(
            symbol, _SCAN_BARS_TIMEFRAME, _SCAN_BARS_DAYS, extended=False
        )
        if not response:
            return None
        return [
            {
                "o": bar["open"],
                "h": bar["high"],
                "l": bar["low"],
                "c": bar["close"],
                "v": bar["volume"],
            }
            for bar in response.get("bars", [])
        ]

    return _get


def _sp500_universe() -> list[str]:
    """The S&P 500 symbol list (from the shared strategy universe spec).

    These are exactly the names a daily loop refreshes into the
    ``momentum_daily_bars`` store, so an ``sp500`` scan reads that store and never
    touches the broker.
    """
    from signals.strategy_universe import resolve_universe_spec  # lazy import

    return resolve_universe_spec("sp500")


def _make_store_bars_getter(symbols: list[str]):
    """Return a ``get_bars`` backed by the persisted ``momentum_daily_bars`` store.

    Reads every symbol's daily bars in a SINGLE SQLite query up front (read-only),
    then serves each from memory — there is NO broker/Schwab call on this path, so
    a ~500-name scan can't burst-request Schwab. The store keeps only open/close,
    so high/low are APPROXIMATED by the close and volume is absent (``None``);
    volume-based presets therefore match nothing here (see ``_metrics``' guard).
    """
    from db.momentum_bars import load_daily_bars  # lazy import

    loaded = load_daily_bars(symbols)  # {symbol: (dates, closes, opens)}

    async def _get(symbol: str) -> list[Bar] | None:
        entry = loaded.get(symbol.strip().upper())
        if not entry:
            return None
        _dates, close_list, open_list = entry
        return [
            {"o": open_, "h": close_, "l": close_, "c": close_, "v": None}
            for open_, close_ in zip(open_list, close_list, strict=True)
        ]

    return _get


@router.get("/presets")
def api_scan_presets() -> dict[str, Any]:
    """List the available scan presets (id, label, description, metric label)."""
    return {"presets": [preset_summary(p) for p in PRESETS]}


@router.get("/run")
async def api_scan_run(preset: str, limit: int = 50, universe: str = "watchlist") -> Any:
    """Run a preset scan over a chosen universe → ranked matches.

    ``universe="watchlist"`` (default) screens watchlist ∪ open positions from the
    app's cached daily-bars path (live, bounded). ``universe="sp500"`` screens the
    S&P 500 straight from the persisted ``momentum_daily_bars`` store — READ-ONLY,
    no broker call — so it can span ~500 names without a Schwab burst.
    """
    if get_preset(preset) is None:
        return JSONResponse({"error": f"Unknown scan preset {preset!r}"}, status_code=400)
    if limit < 1 or limit > 200:
        return JSONResponse({"error": "limit must be 1–200"}, status_code=400)
    if universe not in _UNIVERSES:
        return JSONResponse({"error": f"Unknown universe {universe!r}"}, status_code=400)

    as_of = datetime.now(_ET).isoformat(timespec="seconds")

    if universe == "sp500":
        symbols = _sp500_universe()
        get_bars = _make_store_bars_getter(symbols)
    else:
        symbols = _screen_universe()
        get_bars = _make_bars_getter()

    result = await run_scan(preset, symbols, get_bars, limit=limit)
    result["universe"] = universe
    result["as_of"] = as_of
    # The store has no volume, so the volume preset is honestly empty here — say so.
    if universe == "sp500" and preset == "unusual_volume":
        result["note"] = _SP500_VOLUME_NOTE
    return result
