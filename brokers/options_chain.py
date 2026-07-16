"""Read-only OPRA option-chain builder (one underlying + expiration).

Extracted from ``api/main.py`` so both the HTTP endpoint and the perception
layer can build chains without importing the FastAPI app. Pure broker reads —
no app state, fail-soft nulls per field.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from trading.market_hours import _ET

if TYPE_CHECKING:
    from brokers.alpaca import AlpacaAdapter

logger = logging.getLogger(__name__)


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
    # expiry and drops most puts (live finding 2026-07-09).
    contracts: list[dict] = []
    try:
        raw = broker.get_option_contracts(
            sym, expiration_date_gte=today_et, limit=1000
        )
        if isinstance(raw, list):
            contracts = [c for c in raw if isinstance(c, dict)]
    except Exception:
        logger.debug(
            "options-chain: contracts fetch failed for %s", sym, exc_info=True
        )

    expirations_set: set[str] = set()
    for c in contracts:
        exp = str(c.get("expiration_date") or "").strip()[:10]
        if exp:
            expirations_set.add(exp)
    expirations = sorted(expirations_set)

    now_et = datetime.now(_ET)
    if expiry_param:
        requested = expiry_param.strip()[:10]
        chosen = requested if requested in expirations_set else _pick_nearest_expiry(
            expirations, today_et
        )
    else:
        chosen = _pick_default_expiry(expirations, now_et)

    underlying_price: float | None = None
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

    oi_map: dict[str, int] = {}
    for c in contracts:
        if str(c.get("expiration_date") or "").strip()[:10] != chosen:
            continue
        occ = str(c.get("symbol") or "").strip().upper()
        if not occ:
            continue
        oi_val = _chain_int_or_none(c.get("open_interest"))
        if oi_val is not None:
            oi_map[occ] = oi_val

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

    return {
        "symbol": sym,
        "underlying_price": underlying_price,
        "expirations": expirations,
        "expiry": chosen,
        "rows": rows,
        "as_of": as_of,
    }
