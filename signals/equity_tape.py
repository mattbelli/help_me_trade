"""Large-print detection on the SIP equity tape (blocks + dark-pool prints).

Facts about the tape, never a buy/sell signal. The SIP carries every US equity
print including off-exchange fills reported to FINRA's TRF (exchange code
``D``) — the raw data behind paid "dark pool flow" products. Detection is a
pure hot-path tap fed by the market-stream hub's ``_on_trade``; events queue in
``pending`` for the api drain loop to persist.

Honesty constraints:
- TRF reporting may lag the fill by up to ~10s and never identifies the buyer
  or seller — side is inferred from a tick test (weak evidence), flagged as such.
- Prints carrying non-last-sale conditions (average-price ``W``, derivatively
  priced ``4``…) are often the genuine institutional crosses, so they ARE
  detected here even though they never update the consolidated last price.
"""

from __future__ import annotations

import collections
import time
from typing import Any, Callable

# A print is "large" above 1bp of the name's median daily dollar volume, floored
# at $1M (thin names) — SPY ≈ $5M, a $2B/day single name → the $1M floor binds.
BLOCK_MIN_NOTIONAL_USD = 1_000_000.0
ADV_NOTIONAL_FRACTION = 0.0001
# No volume profile yet (symbol never lazily built) → conservative static floor.
NO_PROFILE_MIN_NOTIONAL_USD = 10_000_000.0
# One event per (symbol, type) per window — the drain/notify layers add their
# own daily dedup on top; this just stops a working order from spamming rows.
TAPE_COOLDOWN_SECONDS = 60.0
# FINRA ADF/TRF prints (off-exchange: dark pools + internalizers).
DARK_POOL_EXCHANGE = "D"
# Corrupt-feed guard: a "large print" >10% off the last admitted price is far
# more likely vendor junk (the phantom-NVDA class) than a real cross — crosses
# price at/near market. No admitted reference yet → skip (startup only).
TAPE_MAX_OFF_MARKET_PCT = 0.10

_PROFILE_TTL_SECONDS = 3600.0


def _default_median_shares(symbol: str) -> float | None:
    from signals.volume_profile import load_profile

    profile = load_profile(symbol)
    if profile is None:
        return None
    try:
        return float(profile.median_daily_total)
    except (TypeError, ValueError, AttributeError):
        return None


class TapeDetector:
    """Stateful per-process detector; ``on_print`` is cheap and never raises."""

    def __init__(
        self,
        median_shares_loader: Callable[[str], float | None] | None = None,
    ) -> None:
        self.pending: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=256
        )
        self._loader = median_shares_loader or _default_median_shares
        self._median_cache: dict[str, tuple[float, float | None]] = {}
        self._cooldown: dict[tuple[str, str], float] = {}

    def _median_shares(self, symbol: str) -> float | None:
        now = time.monotonic()
        hit = self._median_cache.get(symbol)
        if hit is not None and now - hit[0] < _PROFILE_TTL_SECONDS:
            return hit[1]
        try:
            value = self._loader(symbol)
        except Exception:
            value = None
        self._median_cache[symbol] = (now, value)
        return value

    def notional_floor(self, symbol: str, price: float) -> float:
        median = self._median_shares(symbol)
        if median is None or median <= 0:
            return NO_PROFILE_MIN_NOTIONAL_USD
        return max(BLOCK_MIN_NOTIONAL_USD, median * price * ADV_NOTIONAL_FRACTION)

    def on_print(
        self,
        *,
        symbol: str,
        price: float,
        size: Any,
        exchange: Any,
        conditions: Any,
        ts: Any,
        trade_id: Any,
        prev_price: float | None,
    ) -> dict[str, Any] | None:
        try:
            size_i = int(size)
        except (TypeError, ValueError):
            return None
        if price <= 0 or size_i <= 0:
            return None
        if prev_price is None or prev_price <= 0:
            return None
        if abs(price - prev_price) > prev_price * TAPE_MAX_OFF_MARKET_PCT:
            return None

        notional = price * size_i
        if notional < self.notional_floor(symbol, price):
            return None

        ex = str(exchange or "").strip().upper()
        kind = "dark_pool" if ex == DARK_POOL_EXCHANGE else "equity_block"

        now = time.monotonic()
        key = (symbol, kind)
        last = self._cooldown.get(key)
        if last is not None and now - last < TAPE_COOLDOWN_SECONDS:
            return None
        self._cooldown[key] = now

        if price > prev_price:
            direction = "buy_side"
        elif price < prev_price:
            direction = "sell_side"
        else:
            direction = "undetermined"

        conds = [str(c) for c in conditions] if conditions else []
        ts_s = str(ts or "").strip()
        date = ts_s[:10] if len(ts_s) >= 10 else ""
        tid = str(trade_id) if trade_id is not None else ts_s
        notional_m = notional / 1_000_000
        venue = "off-exchange (TRF)" if kind == "dark_pool" else f"exchange {ex}"
        reason = (
            f"${notional_m:,.1f}M print · {size_i:,} sh @ ${price:,.2f} · {venue}"
        )
        caveat = (
            "Off-exchange print reported to FINRA TRF — may lag the fill by up "
            "to ~10s; side inferred from tick test (weak). A fact about the "
            "tape, not a buy/sell signal."
            if kind == "dark_pool"
            else "Side inferred from tick test (weak). A fact about the tape, "
            "not a buy/sell signal."
        )

        event = {
            "id": f"eq_{date}_{symbol}_{'dp' if kind == 'dark_pool' else 'blk'}_{tid}",
            "ts": ts_s,
            "symbol": symbol,
            "event_type": kind,
            "exchange": ex,
            "price": round(price, 4),
            "size": size_i,
            "notional_usd": round(notional, 2),
            "direction": direction,
            "direction_basis": "tick",
            "conditions": conds,
            "reason": reason,
            "caveat": caveat,
        }
        self.pending.append(event)
        return event
