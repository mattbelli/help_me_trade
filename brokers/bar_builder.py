"""Server-side minute-bar authority for the live chart (card #325).

Why this exists: until 2026-08-17 the chart built its forming candle in the
browser from a bare last price plus ``Date.now()``, and stitched it onto a
vendor bar history that trails real time by 60-200s (Alpaca publishes a minute
bar ~60-80s after the minute closes; our bars cache adds up to a TTL). Five
patches in three weeks all lived in that browser glue. TradingView's datafeed
contract, Alpaca's own docs ("the stream provides much better accuracy than
polling the historical endpoints") and every production feed (Binance klines,
Alpaca ``bars``/``updatedBars``) agree on the shape: candles come from ONE
authority, bucketed by the PRINT's timestamp, and the chart only replaces or
appends by bar time.

This module is that authority for the forming/recent minutes. Inputs are
"prints" — SIP trades (exchange timestamp + size), Schwab level-one futures
ticks (receipt time, size 0), overnight REST prices — and vendor bars from
Alpaca's ``bars`` (``b``) / ``updatedBars`` (``u``) channels, which replace ours
minute-for-minute so the series converges on the vendor's own numbers.

Pure state + arithmetic: no I/O, no asyncio, so it is trivially testable and
shared by reference between the hybrid hub's children exactly like the quote
table.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable

from trading.market_hours import _ET

MINUTE_MS = 60_000
# Completed minutes kept per symbol. /api/bars splices these past the vendor's
# tail, and the vendor tail is at most a few minutes behind — 120 is generous
# and bounds memory at ~120 dataclasses × subscribed symbols.
KEEP_MINUTES = 120


@dataclass
class Bar:
    time: int  # epoch ms, minute-bucket start
    open: float
    high: float
    low: float
    close: float
    volume: float
    # True once the VENDOR's bar for this minute replaced ours (b/u frame).
    final: bool = False


def bucket_ms(ts_ms: int | float, interval_ms: int = MINUTE_MS) -> int:
    return int(ts_ms) - int(ts_ms) % interval_ms


def parse_ts_ms(ts: Any) -> int | None:
    """RFC3339 (Alpaca JSON, any sub-second precision, 'Z' or offset) → epoch ms."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # Alpaca msgpack timestamps arrive as ns via to_json_msg only as ISO;
        # a raw number here means "already ms".
        return int(ts)
    s = str(ts).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # datetime.fromisoformat wants ≤6 fractional digits (Alpaca sends 9).
    if "." in s:
        head, tail = s.split(".", 1)
        frac = ""
        rest = ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                frac += ch
            else:
                rest = tail[i:]
                break
        s = f"{head}.{frac[:6].ljust(6, '0')}{rest}"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def bar_session(time_ms: int) -> str:
    """Session label for a bar's minute (ET clock): pre / regular / post /
    overnight. The chart hides non-regular bars when ETH is off. Half-days
    are deliberately ignored here — a 13:00 close makes 13:00-16:00 'regular'
    bars that carry no prints, i.e. nothing to hide."""
    t = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).astimezone(_ET)
    minutes = t.hour * 60 + t.minute
    if t.weekday() <= 4:
        if 4 * 60 <= minutes < 9 * 60 + 30:
            return "pre"
        if 9 * 60 + 30 <= minutes < 16 * 60:
            return "regular"
        if 16 * 60 <= minutes < 20 * 60:
            return "post"
    return "overnight"


def bar_payload(symbol: str, bar: Bar, *, timeframe: str = "1Min") -> dict[str, Any]:
    """The `bar` frame / quote-payload field shape the browser consumes."""
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "time": bar.time,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "final": bar.final,
        "session": bar_session(bar.time),
    }


class MinuteBarBuilder:
    """Per-symbol forming minute + a short ring of recent completed minutes."""

    def __init__(self, keep: int = KEEP_MINUTES) -> None:
        self._keep = keep
        self._forming: dict[str, Bar] = {}
        self._done: dict[str, collections.deque[Bar]] = {}

    # -- inputs ---------------------------------------------------------------

    def on_print(
        self, symbol: str, price: float, size: float, ts_ms: int
    ) -> tuple[Bar | None, Bar | None]:
        """Fold one print in. Returns ``(forming, completed)``: the forming bar
        after this print (None when the print was for an already-completed
        minute), and the minute this print closed out (None unless it rolled).
        A late print for a kept completed minute updates that minute's h/l/c/v
        and is returned as ``completed`` so callers can re-broadcast it."""
        if not (price > 0):
            return None, None
        bucket = bucket_ms(ts_ms)
        cur = self._forming.get(symbol)
        if cur is None:
            cur = Bar(bucket, price, price, price, price, size)
            self._forming[symbol] = cur
            return cur, None
        if bucket > cur.time:
            self._push_done(symbol, cur)
            nxt = Bar(bucket, price, price, price, price, size)
            self._forming[symbol] = nxt
            return nxt, cur
        if bucket < cur.time:
            done = self._done.get(symbol)
            if done:
                for b in done:
                    if b.time == bucket:
                        if b.final:
                            return None, None  # vendor already sealed it
                        b.high = max(b.high, price)
                        b.low = min(b.low, price)
                        b.close = price
                        b.volume += size
                        return None, b
            return None, None
        if cur.final:
            # Vendor bar for the still-current minute already landed; a late
            # print for it belongs to updatedBars, not to us.
            return None, None
        cur.high = max(cur.high, price)
        cur.low = min(cur.low, price)
        cur.close = price
        cur.volume += size
        return cur, None

    def on_vendor_bar(self, symbol: str, bar: Bar) -> Bar:
        """Adopt the vendor's minute (Alpaca ``b``/``u``): replaces our copy of
        that minute wherever it sits, or slots it into the ring. Returns the
        stored bar (final=True)."""
        vb = replace(bar, final=True)
        cur = self._forming.get(symbol)
        if cur is not None and cur.time == vb.time:
            self._forming[symbol] = vb
            return vb
        if cur is None or vb.time > cur.time:
            # Vendor is ahead of our prints (fresh subscription, or a symbol
            # with no admitted trades yet): the vendor minute becomes the tail.
            if cur is not None:
                self._push_done(symbol, cur)
            self._forming[symbol] = vb
            return vb
        done = self._done.setdefault(symbol, collections.deque(maxlen=self._keep))
        for i, b in enumerate(done):
            if b.time == vb.time:
                done[i] = vb
                return vb
        # Not held: insert in order (rare — a vendor minute older than our ring
        # start). deque has no sorted insert; rebuild once.
        items = sorted([*done, vb], key=lambda b: b.time)
        done.clear()
        done.extend(items[-self._keep :])
        return vb

    # -- reads ----------------------------------------------------------------

    def recent(self, symbol: str, since_ms: int = 0) -> list[Bar]:
        """Completed minutes + the forming one, ascending, ``time >= since_ms``."""
        out: list[Bar] = [b for b in self._done.get(symbol, ()) if b.time >= since_ms]
        cur = self._forming.get(symbol)
        if cur is not None and cur.time >= since_ms:
            out.append(cur)
        return out

    def forming(self, symbol: str) -> Bar | None:
        return self._forming.get(symbol)

    def forget(self, symbol: str) -> None:
        self._forming.pop(symbol, None)
        self._done.pop(symbol, None)

    # -- internals ------------------------------------------------------------

    def _push_done(self, symbol: str, bar: Bar) -> None:
        done = self._done.get(symbol)
        if done is None:
            done = collections.deque(maxlen=self._keep)
            self._done[symbol] = done
        done.append(bar)


def aggregate(bars: Iterable[Bar], interval_ms: int) -> list[Bar]:
    """Roll minute bars up into ``interval_ms`` buckets (UTC-floor alignment —
    matches the vendor: 1Hour on :00, 4Hour on 00/04/08/12/16/20Z, and 5/15Min
    on the obvious boundaries). ``final`` is true only when every minute is."""
    out: list[Bar] = []
    for b in bars:
        t = bucket_ms(b.time, interval_ms)
        if out and out[-1].time == t:
            agg = out[-1]
            agg.high = max(agg.high, b.high)
            agg.low = min(agg.low, b.low)
            agg.close = b.close
            agg.volume += b.volume
            agg.final = agg.final and b.final
        else:
            out.append(replace(b, time=t))
    return out


def splice_vendor_tail(
    vendor: list[dict[str, Any]], ours: list[Bar], interval_ms: int
) -> list[dict[str, Any]]:
    """Extend a vendor bar-dict series (``time`` ISO, ascending) with our
    minute-built buckets past its tail. Vendor buckets are kept verbatim; a
    bucket equal to the vendor's LAST bar is merged (the vendor may return the
    still-forming bucket, partial as of its snapshot): open stays the vendor's,
    high/low widen, close is ours (we are live, the snapshot is not), volume is
    max of the two — the honest bound when the two windows overlap unknowably.
    Buckets past the vendor tail are appended as ours. Returns a NEW list."""
    if not ours:
        return vendor
    if not vendor:
        return [_bar_dict(b) for b in aggregate(ours, interval_ms)]
    last = vendor[-1]
    last_ms = parse_ts_ms(last.get("time"))
    if last_ms is None:
        return vendor
    last_bucket = bucket_ms(last_ms, interval_ms)
    agg = aggregate((b for b in ours if b.time >= last_bucket), interval_ms)
    if not agg:
        return vendor
    out = list(vendor)
    for b in agg:
        if b.time < last_bucket:
            continue
        if b.time == last_bucket:
            merged = dict(last)
            merged["high"] = max(float(last["high"]), b.high)
            merged["low"] = min(float(last["low"]), b.low)
            merged["close"] = b.close
            merged["volume"] = max(float(last.get("volume") or 0.0), b.volume)
            out[-1] = merged
            continue
        out.append(_bar_dict(b))
    return out


def _bar_dict(b: Bar) -> dict[str, Any]:
    return {
        "time": datetime.fromtimestamp(b.time / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "open": b.open,
        "high": b.high,
        "low": b.low,
        "close": b.close,
        "volume": b.volume,
    }
