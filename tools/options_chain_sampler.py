#!/usr/bin/env python3
"""Post-close options-chain sampler — the forward arm of the IV corpus.

The DoltHub snapshot in ``data/options_iv_history.db`` is frozen at 2026-07-17 and
cannot be extended (shallow clone, no pull). Everything after that date has to be
collected, and this is the collector: one sweep after each close, writing a per-
contract band plus a per-underlying IV row for every symbol the universe has ever
contained.

Read-only against the broker — contract metadata and market data only, never an
order path. Safe to re-run: a session's rows are replaced, not duplicated.

    python tools/options_chain_sampler.py                # today's session
    python tools/options_chain_sampler.py --session 2026-07-24
    python tools/options_chain_sampler.py --dry-run --symbols SPY,AAPL
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brokers.alpaca import AlpacaAdapter  # noqa: E402
from db.options_chain_history import (  # noqa: E402
    backfill_iv_daily_oi,
    backfill_open_interest,
    finish_run,
    init_chain_tables,
    register_universe,
    start_run,
    universe_symbols,
    write_chain_rows,
    write_iv_daily,
)
from signals.options_chain_snapshot import (  # noqa: E402
    atm_iv,
    bar_volume,
    board_aggregates,
    constant_maturity_iv,
    front_atm_iv,
    in_band,
    is_optionable_symbol,
    parse_occ,
)
from signals.universe import all_watchlist_symbols_fresh  # noqa: E402
from signals.volatility import forecast_vol  # noqa: E402
from trading.market_calendar import is_trading_day  # noqa: E402

logger = logging.getLogger("options_chain_sampler")
_ET = ZoneInfo("America/New_York")

# Index ETFs anchor the vol surface whatever the watchlist happens to hold today,
# so they are always in the universe (watchlists-are-dynamic doctrine: the sweep
# is sized by the append-only universe table, never by today's list).
CORE_ROOTS = ("SPY", "QQQ", "IWM", "DIA")

# Enough history for the EWMA seed plus its regime lookback.
HV_BAR_DAYS = 400
# The vendor pages the chain 1,000 at a time; SPY's board is ~13,600 contracts.
CHAIN_MAX_PAGES = 25
# Bar requests are chunked so one unfetchable ticker cannot empty the HV column.
BAR_BATCH = 25


def resolve_universe() -> list[str]:
    """Register today's sources into the append-only universe, then return all of it."""
    try:
        watch = all_watchlist_symbols_fresh()
    except Exception:
        logger.exception("Watchlist read failed — falling back to the stored universe")
        watch = []
    register_universe(list(CORE_ROOTS), source="core")
    tradable = [s for s in watch if is_optionable_symbol(s)]
    skipped = sorted(set(watch) - set(tradable))
    if skipped:
        # Never let these into an append-only table — see is_optionable_symbol.
        logger.info("Skipping %d non-optionable watchlist symbols: %s", len(skipped), ", ".join(skipped))
    if tradable:
        register_universe(tradable, source="watchlist")
    return [s for s in universe_symbols() if is_optionable_symbol(s)]


def fetch_daily_bars(adapter: AlpacaAdapter, symbols: list[str]) -> dict:
    """Daily bars for the HV column, chunked so one bad symbol cannot empty it.

    A single unfetchable ticker 400s the whole batch request, and an append-only
    universe steadily accumulates them as names delist. On a chunk failure the
    chunk is retried symbol-by-symbol so only the genuinely-bad ones are lost.
    """
    out: dict = {}
    for start in range(0, len(symbols), BAR_BATCH):
        chunk = symbols[start : start + BAR_BATCH]
        try:
            out.update(adapter.get_stock_bars_batch(chunk, timeframe="1Day", days=HV_BAR_DAYS) or {})
            continue
        except Exception:
            logger.warning("Daily-bar batch failed for %d symbols — retrying singly", len(chunk))
        for sym in chunk:
            try:
                out.update(adapter.get_stock_bars_batch([sym], timeframe="1Day", days=HV_BAR_DAYS) or {})
            except Exception:
                logger.warning("Daily bars unavailable for %s — no HV this run", sym)
    return out


def sample_symbol(
    adapter: AlpacaAdapter, sym: str, session: str, session_date: date, bars: list
) -> tuple[list[dict], dict, list[tuple[str, str, int]], tuple[int, int], str | None]:
    """Sample one underlying.

    Returns ``(banded_rows, iv_row, oi_observations, board_oi_totals, error)``.
    ``oi_observations`` and ``board_oi_totals`` describe the PRIOR session — the
    vendor publishes open interest a session in arrears.
    """
    snap = adapter.get_stock_snapshot_fields(sym) or {}
    spot = snap.get("last_price")
    if not spot or spot <= 0:
        return [], {}, [], (0, 0), "no spot"

    snaps = adapter.get_option_chain_snapshot(sym, limit=1000, max_pages=CHAIN_MAX_PAGES)
    if not snaps:
        return [], {}, [], (0, 0), "empty chain"

    board: list[dict] = []
    banded: list[dict] = []
    iv_by_expiry: dict[date, list[tuple[float, float]]] = {}

    for occ, s in snaps.items():
        c = parse_occ(occ)
        if c is None:
            continue
        volume, bar_date = bar_volume(s.get("dailyBar"), session)
        quote = s.get("latestQuote") or {}
        trade = s.get("latestTrade") or {}
        greeks = s.get("greeks") or {}
        iv = s.get("impliedVolatility")
        dte = (c.expiration - session_date).days

        # Whole-board row: aggregates must not be band-truncated.
        board.append(
            {"opt_type": c.opt_type, "volume": volume, "bar_date": bar_date,
             "session": session, "open_interest": 0}
        )
        if iv and iv > 0:
            iv_by_expiry.setdefault(c.expiration, []).append((c.strike, float(iv)))

        if not in_band(c.strike, spot, dte):
            continue
        banded.append(
            {
                "session": session, "occ": c.occ, "underlying": sym,
                "expiration": c.expiration.isoformat(), "strike": c.strike,
                "opt_type": c.opt_type, "style": None, "spot": spot,
                "bid": quote.get("bp"), "ask": quote.get("ap"),
                "bid_size": quote.get("bs"), "ask_size": quote.get("as"),
                "last_price": trade.get("p"), "volume": volume, "bar_date": bar_date,
                "open_interest": None, "oi_date": None, "iv": iv,
                "delta": greeks.get("delta"), "gamma": greeks.get("gamma"),
                "theta": greeks.get("theta"), "vega": greeks.get("vega"),
                "rho": greeks.get("rho"),
            }
        )

    agg = board_aggregates(board)
    front_iv, front_dte = front_atm_iv(iv_by_expiry, spot, session_date)
    cm_points: list[tuple[int, float]] = []
    for expiry, points in iv_by_expiry.items():
        value = atm_iv(points, spot)
        if value is not None:
            cm_points.append(((expiry - session_date).days, value))
    iv30 = constant_maturity_iv(cm_points)

    hv = None
    fc = forecast_vol(sym, bars, today=session_date) if bars else None
    if fc is not None:
        hv = fc.annualized

    iv_row = {
        "session": session, "underlying": sym, "spot": spot,
        "atm_iv_30d": iv30, "atm_iv_front": front_iv, "front_dte": front_dte,
        "hv_ewma": hv,
        "vrp": (iv30 - hv) if (iv30 is not None and hv is not None) else None,
        "call_volume": agg.call_volume, "put_volume": agg.put_volume,
        "call_oi": 0, "put_oi": 0,
        "contracts_observed": agg.contracts_observed,
        "contracts_stored": len(banded), "bars_current": agg.bars_current,
    }

    # Open interest, for whichever session the vendor stamped it with.
    oi_obs: list[tuple[str, str, int]] = []
    call_oi = put_oi = 0
    contracts = adapter.get_option_contracts(sym, expiration_date_gte=session, limit=10000)
    for item in contracts:
        occ = str(item.get("symbol") or "").strip().upper()
        oi_date = item.get("open_interest_date")
        raw_oi = item.get("open_interest")
        if not occ or not oi_date or raw_oi in (None, ""):
            continue
        try:
            oi = int(raw_oi)
        except (TypeError, ValueError):
            continue
        parsed = parse_occ(occ)
        if parsed is None:
            continue
        oi_obs.append((occ, str(oi_date), oi))
        if parsed.opt_type == "call":
            call_oi += oi
        else:
            put_oi += oi

    return banded, iv_row, oi_obs, (call_oi, put_oi), None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", help="ET session date (default: today)")
    ap.add_argument("--symbols", help="comma-separated override of the universe")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    ap.add_argument("--force", action="store_true", help="run even on a non-trading day")
    ap.add_argument(
        "--allow-backdate",
        action="store_true",
        help="permit a past --session (testing only; the capture is NOT that session)",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    today = datetime.now(_ET).date()
    session = args.session or today.isoformat()
    session_date = date.fromisoformat(session)
    if not is_trading_day(session_date) and not args.force:
        logger.info("%s is not a trading day — nothing to sample", session)
        return 0
    # Snapshots are a LIVE read with no history endpoint, so a past --session does
    # not sample that session — it labels today's quotes with an old date, and the
    # daily bars attached can even postdate the label. Writing that into the corpus
    # would be indistinguishable from real history later, so it takes an explicit flag.
    if session_date != today and not args.allow_backdate:
        logger.error(
            "Refusing --session %s: snapshots are live-only, so this would store "
            "today's chain under a past date. Pass --allow-backdate if you are "
            "testing and understand the rows are not real history.",
            session,
        )
        return 2
    if session_date != today:
        logger.warning(
            "BACKDATED RUN: rows stored for %s are today's live chain, not that "
            "session's history — do not treat them as observations.",
            session,
        )

    if args.symbols:
        symbols = sorted({s.strip().upper() for s in args.symbols.split(",") if s.strip()})
    elif args.dry_run:
        # A dry run must not create or write the corpus DB, so it previews today's
        # sources rather than reading (and extending) the append-only universe.
        symbols = sorted(set(CORE_ROOTS) | set(all_watchlist_symbols_fresh()))
    else:
        init_chain_tables()
        symbols = resolve_universe()
    if not symbols:
        logger.warning("Universe is empty — nothing to sample")
        return 0

    adapter = AlpacaAdapter.from_settings()
    if not args.dry_run:
        start_run(session)

    logger.info("Sampling %d symbols for session %s", len(symbols), session)
    raw_bars = fetch_daily_bars(adapter, symbols)

    from signals.strategy_scan import _stock_bars_to_dated

    total_contracts = total_oi = ok = 0
    failures: list[str] = []
    oi_by_date: dict[str, list[tuple[str, str, int]]] = {}
    oi_totals_by_date: dict[str, dict[str, tuple[int, int]]] = {}

    for sym in symbols:
        try:
            bars = _stock_bars_to_dated(list(raw_bars.get(sym) or []))
        except Exception:
            bars = []
        try:
            rows, iv_row, oi_obs, board_oi, err = sample_symbol(
                adapter, sym, session, session_date, bars
            )
        except Exception:
            logger.exception("Sampling failed for %s", sym)
            failures.append(sym)
            continue
        if err:
            logger.warning("%s: %s", sym, err)
            failures.append(sym)
            continue

        ok += 1
        total_contracts += len(rows)
        logger.info(
            "%-6s spot=%-9.2f stored=%-5d board=%-6d iv30=%s hv=%s vol(c/p)=%d/%d bars_current=%d",
            sym, iv_row["spot"], len(rows), iv_row["contracts_observed"],
            f"{iv_row['atm_iv_30d']:.4f}" if iv_row["atm_iv_30d"] else "-",
            f"{iv_row['hv_ewma']:.4f}" if iv_row["hv_ewma"] else "-",
            iv_row["call_volume"], iv_row["put_volume"], iv_row["bars_current"],
        )
        # Group the OI observations by the session they describe (normally D-1).
        for occ, oi_date, oi in oi_obs:
            oi_by_date.setdefault(oi_date, []).append((occ, oi_date, oi))
        if oi_obs:
            stamp = oi_obs[0][1]
            oi_totals_by_date.setdefault(stamp, {})[sym] = board_oi

        if not args.dry_run:
            write_chain_rows(rows)
            write_iv_daily([iv_row])

    if not args.dry_run:
        for oi_date, obs in oi_by_date.items():
            if oi_date == session:
                continue  # same-session OI needs no backfill
            total_oi += backfill_open_interest(obs)
            backfill_iv_daily_oi(oi_date, oi_totals_by_date.get(oi_date, {}))
        finish_run(
            session, attempted=len(symbols), ok=ok, contracts=total_contracts,
            oi_backfilled=total_oi, failures=failures,
        )

    logger.info(
        "Session %s: %d/%d symbols, %d contract rows, %d OI backfilled%s",
        session, ok, len(symbols), total_contracts, total_oi,
        " (dry run — nothing written)" if args.dry_run else "",
    )
    if failures:
        logger.warning("Failed: %s", ", ".join(sorted(failures)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
