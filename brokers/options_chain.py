"""Read-only OPRA option-chain builder (one underlying + expiration).

Extracted from ``api/main.py`` so both the HTTP endpoint and the perception
layer can build chains without importing the FastAPI app. Pure broker reads —
no app state, fail-soft nulls per field.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from trading.market_hours import _ET

if TYPE_CHECKING:
    from brokers.alpaca import AlpacaAdapter

logger = logging.getLogger(__name__)

# Index option roots the market-data API serves but the trading-API contracts
# endpoint does NOT (index options aren't tradable on Alpaca): expirations must
# be generated, OI has no source, and the "underlying price" has no stock quote
# — it's inferred from put-call parity at the tightest call/put midpoint.
# NOTE the snapshots path symbol is the OCC ROOT: SPX = AM monthlies only,
# SPXW = the PM dailies/weeklies (measured 2026-07-22, chain sampler postmortem).
INDEX_OPTION_ROOTS = frozenset({"SPX", "SPXW"})

# --- per-day contracts metadata cache ---------------------------------------
# The contracts fetch is what made this endpoint too slow to poll: measured
# 2026-07-31 on the live process, SPY cost 2.15 s / 14,398 contracts — 91% of a
# 2.36 s cold chain build — while the quote snapshot underneath it took 0.07 s
# warm. Everything the contracts call supplies is a per-DAY constant: the
# expirations list, and open interest (published by OCC overnight, flat
# intraday). Caching it per (symbol, ET date) is what makes a 1-2 s poll
# affordable, which is the whole point — the quotes were always live, we were
# re-paying 2 s for yesterday's open interest to see them.
_CONTRACTS_CACHE: dict[tuple[str, str], tuple[list[str], dict[str, dict[str, int]]]] = {}
# One global lock, not one per symbol: a miss costs ~2 s, so the double-checked
# fill is there to stop a poll from stampeding the fetch its own initial load is
# already running. Serializing cold fills across symbols is fine at chain-widget
# concurrency and keeps the bookkeeping to nothing.
_CONTRACTS_LOCK = threading.Lock()
# Bound on a process that runs for weeks. Insertion-order eviction, not LRU —
# the date sweep does the real work and this is only a backstop against someone
# paging through hundreds of symbols in one session.
_CONTRACTS_CACHE_MAX = 32


def _fetch_contracts_meta(
    broker: "AlpacaAdapter", sym: str, today_et: str
) -> tuple[list[str], dict[str, dict[str, int]]]:
    """Expirations + open interest keyed by expiry, from the contracts API."""
    raw = broker.get_option_contracts(sym, expiration_date_gte=today_et, limit=1000)
    oi_by_expiry: dict[str, dict[str, int]] = {}
    expirations: set[str] = set()
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        exp = str(c.get("expiration_date") or "").strip()[:10]
        if not exp:
            continue
        expirations.add(exp)
        occ = str(c.get("symbol") or "").strip().upper()
        oi_val = _chain_int_or_none(c.get("open_interest"))
        if occ and oi_val is not None:
            oi_by_expiry.setdefault(exp, {})[occ] = oi_val
    return sorted(expirations), oi_by_expiry


def _contracts_meta(
    broker: "AlpacaAdapter", sym: str, today_et: str
) -> tuple[list[str], dict[str, dict[str, int]]]:
    key = (sym, today_et)
    hit = _CONTRACTS_CACHE.get(key)
    if hit is not None:
        return hit
    with _CONTRACTS_LOCK:
        hit = _CONTRACTS_CACHE.get(key)
        if hit is not None:  # filled while we waited for the lock
            return hit
        try:
            meta = _fetch_contracts_meta(broker, sym, today_et)
        except Exception:
            logger.debug(
                "options-chain: contracts fetch failed for %s", sym, exc_info=True
            )
            # Deliberately NOT cached: a transient vendor failure must not blind
            # the symbol's expirations picker until the ET date rolls over.
            return [], {}
        for stale in [k for k in _CONTRACTS_CACHE if k[1] != today_et]:
            del _CONTRACTS_CACHE[stale]
        while len(_CONTRACTS_CACHE) >= _CONTRACTS_CACHE_MAX:
            _CONTRACTS_CACHE.pop(next(iter(_CONTRACTS_CACHE)))
        _CONTRACTS_CACHE[key] = meta
        return meta


def _chain_float_or_none(val: object) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _chain_int_or_none(val: object) -> int | None:
    if val is None:
        return None
    try:
        return int(float(str(val).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _build_chain_side(snapshot: dict | None, oi: int | None) -> dict:
    """One call/put column for the options-chain table — fail-soft nulls."""
    side = {
        "bid": None,
        "ask": None,
        "iv": None,
        "delta": None,
        "gamma": None,
        "theta": None,
        "vega": None,
        "volume": None,
        "oi": oi,
        "last": None,
    }
    if not isinstance(snapshot, dict):
        return side

    iv = _chain_float_or_none(snapshot.get("impliedVolatility"))
    if iv is not None:
        side["iv"] = round(iv, 4)

    greeks = snapshot.get("greeks")
    if isinstance(greeks, dict):
        for key in ("delta", "gamma", "theta", "vega"):
            val = _chain_float_or_none(greeks.get(key))
            if val is not None:
                side[key] = round(val, 4)

    quote = snapshot.get("latestQuote") or snapshot.get("latest_quote")
    if isinstance(quote, dict):
        bid = _chain_float_or_none(quote.get("bp"))
        ask = _chain_float_or_none(quote.get("ap"))
        if bid is not None:
            side["bid"] = round(bid, 2)
        if ask is not None:
            side["ask"] = round(ask, 2)

    trade = snapshot.get("latestTrade") or snapshot.get("latest_trade")
    if isinstance(trade, dict):
        last = _chain_float_or_none(trade.get("p"))
        if last is not None:
            side["last"] = round(last, 2)

    daily = snapshot.get("dailyBar") or snapshot.get("daily_bar")
    if isinstance(daily, dict):
        vol = _chain_int_or_none(daily.get("v"))
        if vol is not None:
            side["volume"] = vol

    return side


def _index_expirations(sym: str, today: date) -> list[str]:
    """Generated expiration picker for index roots (no contracts API).

    Horizons measured live 2026-07-22 against /v1beta1/options/snapshots:
    SPXW listed every trading day ~3 weeks out, Friday weeklies ~7 weeks,
    third-Friday monthlies beyond; SPX root = third Fridays 8+ months out.
    A generated date that isn't actually listed fails soft (empty chain), so
    the list errs slightly generous rather than probing per date.
    """
    from datetime import timedelta

    out: set[str] = set()
    if sym == "SPXW":
        d = today
        weekdays = 0
        while weekdays < 15:
            d += timedelta(days=1)
            if d.weekday() < 5:
                out.add(d.isoformat())
                weekdays += 1
        d = today
        for _ in range(7):
            d += timedelta(days=1)
            while d.weekday() != 4:
                d += timedelta(days=1)
            out.add(d.isoformat())
        months_ahead = 8
    else:
        months_ahead = 8
    y, m = today.year, today.month
    for _ in range(months_ahead):
        third = date(y, m, 15)
        while third.weekday() != 4:
            third += timedelta(days=1)
        if third > today:
            out.add(third.isoformat())
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return sorted(out)


def _parity_spot(strike_map: dict[float, dict[str, tuple[str, dict]]]) -> float | None:
    """Underlying level from put-call parity: S ≈ K + (C − P) at the strike
    where the call/put midpoints are tightest. Short-dated index chains make
    the discounting term negligible; good enough to center the strike window
    and label the header — NOT a tradable quote."""
    best: tuple[float, float] | None = None
    for strike, sides in strike_map.items():
        call = sides.get("call")
        put = sides.get("put")
        if not call or not put:
            continue
        mids: list[float] = []
        for _occ, snap in (call, put):
            quote = snap.get("latestQuote") or {}
            bid = _chain_float_or_none(quote.get("bp"))
            ask = _chain_float_or_none(quote.get("ap"))
            if bid is None or ask is None or ask <= 0 or bid < 0 or ask < bid:
                break
            mids.append((bid + ask) / 2)
        if len(mids) != 2:
            continue
        diff = abs(mids[0] - mids[1])
        if best is None or diff < best[0]:
            best = (diff, strike + mids[0] - mids[1])
    return round(best[1], 2) if best is not None else None


def _pick_nearest_expiry(expirations: list[str], today_et: str) -> str | None:
    if not expirations:
        return None
    future = [e for e in expirations if e >= today_et]
    if future:
        return future[0]
    return expirations[-1]


def _parse_chain_timestamp(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        s = str(raw).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _snapshot_as_of_time(snapshot: dict | None) -> datetime | None:
    if not isinstance(snapshot, dict):
        return None
    quote = snapshot.get("latestQuote") or snapshot.get("latest_quote")
    if isinstance(quote, dict):
        ts = _parse_chain_timestamp(quote.get("t"))
        if ts is not None:
            return ts
    trade = snapshot.get("latestTrade") or snapshot.get("latest_trade")
    if isinstance(trade, dict):
        return _parse_chain_timestamp(trade.get("t"))
    return None


def _is_expiry_tradeable(expiry: str, now_et: datetime) -> bool:
    from datetime import time as dt_time

    try:
        exp_date = datetime.fromisoformat(expiry[:10]).date()
    except ValueError:
        return False
    today = now_et.date()
    if exp_date < today:
        return False
    if exp_date == today and now_et.time() > dt_time(16, 0):
        return False
    return True


def _pick_default_expiry(expirations: list[str], now_et: datetime) -> str | None:
    for exp in expirations:
        if _is_expiry_tradeable(exp, now_et):
            return exp
    return expirations[-1] if expirations else None


def _window_strikes_rows(
    rows: list[dict],
    underlying_price: float | None,
    strikes_param: str | int,
) -> list[dict]:
    if str(strikes_param).lower() == "all":
        return rows
    try:
        n = int(strikes_param)
    except (TypeError, ValueError):
        n = 10
    if n <= 0 or not rows:
        return rows
    if underlying_price is None:
        mid = len(rows) // 2
        half = n // 2
        start = max(0, mid - half)
        return rows[start : start + n]
    ranked = sorted(rows, key=lambda r: abs(r["strike"] - underlying_price))
    picked = ranked[:n]
    return sorted(picked, key=lambda r: r["strike"])


def _format_chain_as_of(times: list[datetime]) -> str | None:
    if not times:
        return None
    latest = max(times)
    iso = latest.astimezone(timezone.utc).isoformat()
    return iso.replace("+00:00", "Z")


def build_options_chain_sync(
    broker: "AlpacaAdapter",
    symbol: str,
    expiry_param: str | None,
    strikes_param: str | int = 10,
) -> dict:
    from imports.option_symbol import parse_occ_symbol

    sym = symbol.strip().upper()
    today_et = datetime.now(_ET).date().isoformat()

    # Contracts metadata drives the expirations picker + OI. The snapshot
    # endpoint pages alphabetically (near-dated calls first), so deriving
    # expirations from an unfiltered snapshot truncates the picker to one
    # expiry and drops most puts (live finding 2026-07-09). Index roots have
    # no contracts API (not tradable on Alpaca): generated picker, no OI.
    # Cached per (symbol, ET date) — see _CONTRACTS_CACHE.
    oi_by_expiry: dict[str, dict[str, int]] = {}
    if sym in INDEX_OPTION_ROOTS:
        expirations = _index_expirations(sym, datetime.now(_ET).date())
    else:
        expirations, oi_by_expiry = _contracts_meta(broker, sym, today_et)
    expirations_set = set(expirations)

    now_et = datetime.now(_ET)
    if expiry_param:
        requested = expiry_param.strip()[:10]
        chosen = requested if requested in expirations_set else _pick_nearest_expiry(
            expirations, today_et
        )
    else:
        chosen = _pick_default_expiry(expirations, now_et)

    underlying_price: float | None = None
    if sym not in INDEX_OPTION_ROOTS:  # indexes have no stock snapshot
        try:
            und_snap = broker.get_stock_snapshot_fields(sym)
            if und_snap and und_snap.get("last_price") is not None:
                underlying_price = round(float(und_snap["last_price"]), 2)
        except Exception:
            logger.debug(
                "options-chain: underlying snapshot failed for %s",
                sym,
                exc_info=True,
            )

    oi_map = oi_by_expiry.get(chosen or "", {})

    # One expiry is at most a few hundred contracts — filtered, paginated fetch.
    chain: dict = {}
    if chosen:
        raw_chain = broker.get_option_chain_snapshot(
            sym, expiration_date=chosen, limit=1000, max_pages=3
        )
        if isinstance(raw_chain, dict):
            chain = raw_chain

    strike_map: dict[float, dict[str, tuple[str, dict]]] = {}
    for occ, snap in chain.items():
        parsed = parse_occ_symbol(str(occ))
        if parsed is None or parsed.expiration.isoformat() != chosen:
            continue
        strike = float(parsed.strike)
        right = "call" if parsed.right == "C" else "put"
        snap_dict = snap if isinstance(snap, dict) else {}
        strike_map.setdefault(strike, {})[right] = (
            str(occ).strip().upper(),
            snap_dict,
        )

    underlying_price_source: str | None = None
    if sym in INDEX_OPTION_ROOTS and underlying_price is None:
        underlying_price = _parity_spot(strike_map)
        if underlying_price is not None:
            underlying_price_source = "options_parity"

    rows: list[dict] = []
    row_times: dict[float, list[datetime]] = {}
    if chosen:
        for strike in sorted(strike_map.keys()):
            sides = strike_map[strike]
            call_pair = sides.get("call")
            put_pair = sides.get("put")
            call_occ, call_snap = call_pair if call_pair else (None, {})
            put_occ, put_snap = put_pair if put_pair else (None, {})
            times: list[datetime] = []
            for snap in (call_snap, put_snap):
                ts = _snapshot_as_of_time(snap)
                if ts is not None:
                    times.append(ts)
            if times:
                row_times[strike] = times
            rows.append(
                {
                    "strike": strike,
                    "call": _build_chain_side(
                        call_snap,
                        oi_map.get(call_occ) if call_occ else None,
                    ),
                    "put": _build_chain_side(
                        put_snap,
                        oi_map.get(put_occ) if put_occ else None,
                    ),
                }
            )

    rows = _window_strikes_rows(rows, underlying_price, strikes_param)
    as_of_times: list[datetime] = []
    for row in rows:
        as_of_times.extend(row_times.get(row["strike"], []))
    as_of = _format_chain_as_of(as_of_times)

    result = {
        "symbol": sym,
        "underlying_price": underlying_price,
        "expirations": expirations,
        "expiry": chosen,
        "rows": rows,
        "as_of": as_of,
    }
    if underlying_price_source is not None:
        result["underlying_price_source"] = underlying_price_source
    return result
