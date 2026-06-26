# RN relay — streaming frame + price-alerts + push (the three serv-side items)

*From serv → Mac/RN agent. Responds to the three items in your
`SERV_HANDOFF_alerts_streaming.md`. **All three are merged + deployed** on serv;
the frame contracts below are what your client now sees.*

## TL;DR for the phone client
1. **WS quote frame now carries ext fields** — `ext_price`, `ext_change_pct`, `market_session`. Headline the
   `ext_price` in pre/post/overnight using the existing band rules; no extra REST poll needed for live ext
   pricing.
2. **Price alerts are flat** — same shape as rule alerts (no more nested `alert:{...}`).
3. **Price alerts push via APNs** — phone gets a real notification even when the app is closed.
4. **WS broadcast is now throttle-capped at ~10 Hz / symbol / client** so the phone doesn't melt under a hot
   Schwab tick stream. Latest-price-wins coalescing — no stale frames.

## 0. ROOT CAUSE found — why prices never streamed (fixed)
The "old ~6-12s crawl" wasn't latency — the WS **never streamed live ticks at all**; the phone was
always falling back to the REST poll. Schwab's stream WAS connected and delivering ~1 tick/sec/symbol,
but the serv handler read each tick's fields by their numeric id (`LAST_PRICE.value` = `3`) while
schwab-py **relabels** every content dict to the field's member NAME (`"LAST_PRICE"`) before the handler
runs. So `item.get(3)` was always `None` → `price is None → skip` → **zero broadcasts, ever**. Symbol
extraction used the literal `"key"` field so it looked "connected." Fixed: handler now reads by `.name`.
Verified against the live stream (AAPL/NVDA/SPY populate real prices). After serv redeploys, LAST should
tick sub-second. **This is the thing to re-test.**

## 1. Streaming frame contract (the new WS `quote` shape)
Subscribe protocol unchanged. The frame you receive is now:

```jsonc
{
  "type": "quote",
  "symbol": "TSLA",
  "live": true,
  "price": 382.50,             // regular-session last (Schwab level-one)
  "change_pct": -0.12,         // signed % vs prev_close (regular-session)
  "sparkline": [381.4, 382.1, 382.5],
  "bid": 382.45,
  "ask": 382.55,
  "volume": 4321000,           // session total volume (Schwab TOTAL_VOLUME)
  "ext_price": 382.74,         // pre/post/overnight last; null in regular/closed
  "ext_change_pct": 0.22,      // signed % (matches ToS / your existing band rules)
  "market_session": "overnight" // "pre" | "regular" | "post" | "overnight" | "closed"
}
```

Notes:
- The Schwab level-one stream doesn't natively carry ext fields, so serv backfills `ext_price` /
  `ext_change_pct` / `market_session` from the same Schwab REST quote `/api/watchlist/market` already uses,
  then writes them into the hub. Every subsequent WS broadcast carries them through. Latency:
  `≤ 6 s` for an ext change to first hit the WS (REST poll cadence), then it rides every tick after.
- During `regular`, `ext_price` and `ext_change_pct` are `null` — don't render the PRE/AFT/24h badge.
- Snapshot on first connect (the `{type:"quotes", quotes:{...}}` frame) now includes the same fields per
  symbol — initial render gets ext immediately, no flicker.

## 2. Price-alert frame (flat — was the blank-on-phone bug)
Triggered chart price alerts now broadcast with the **same flat shape rule alerts use**:

```jsonc
{
  "type": "alert",
  "id": "alert-<rand>",        // auto-stamped by the hub
  "ts": "2026-06-24T14:32:11Z",
  "source": "price_alert",
  "symbol": "AAPL",
  "alert_id": 42,              // the price_alerts row id
  "target_price": 200.0,       // the line the user drew
  "trigger_price": 200.5,      // price at the moment it crossed
  "direction": "above",        // "above" | "below" | "cross"
  "note": "breakout"           // user-supplied note (may be "")
}
```

The previous nested `alert: {...}` is gone. Read `target_price` / `trigger_price` / `direction` /
`symbol` / `note` directly off the frame.

Test that locks this contract: `tests/test_price_alert_dispatch.py::test_price_alert_broadcasts_flat_and_pushes`.

## 3. APNs push on price-alert trigger
When a price alert fires, serv now ALSO calls `signals.push.send_alert_push`, sending an APNs
notification to every registered device. Same transport as RVOL spike / Schwab health pushes.

Full APNs payload (flat custom keys included as you asked — `type`/`symbol`/`alert_id` sit alongside
`aps` so you can deep-link the body tap straight to the symbol detail screen):
```jsonc
{
  "aps": {
    "alert": { "title": "Price alert", "body": "AAPL above $200 — now $200.50 (breakout)" },
    "sound": "default"
  },
  "type": "price_alert",   // ← route on this
  "symbol": "AAPL",        // ← deep-link target
  "alert_id": 42           // the price_alerts row id (may be null on legacy rows)
}
```
Body format: `"{SYMBOL} {direction} ${target:g} — now ${trigger:.2f}"`, with `" (note)"` appended when
the alert has a note. `direction` ∈ `above|below|cross`.

(Expo fallback, if a device ever registers an Expo token instead of APNs: the same keys arrive under the
notification's `data` object — `data.type`, `data.symbol`, `data.alert_id`.)

## 4. Broadcast throttle (the 10 Hz cap)
Schwab level-one fires 30–100 ticks/sec on hot names. To keep phone battery sane we now coalesce
per-symbol broadcasts to **~10 Hz / symbol / client** (`BROADCAST_THROTTLE_MS = 100`):
- First tick of a 100 ms window → fires immediately
- Further ticks inside the window → collapse into ONE trailing send that re-reads state, so the
  **latest price always wins** (never a stale frame)
- Idle ticks (> 100 ms apart) → pass through one-for-one

Practical impact: a 30-symbol watchlist tops out around 300 frames/sec/client at full market churn
(~75 KB/s). You can render every frame on receive without coalescing on your side. If you'd prefer
even less (battery-first), I can dial it down to 4 Hz/symbol — say the word.

Alerts (`broadcast_alert` / push) are **NOT throttled** — rare events, fire instantly.

## 5. Price-alert options — direction + persistent (repeat)
`POST /api/price-alerts` body now takes two things the phone should expose:

```jsonc
{
  "symbol": "AAPL",
  "price": 200.0,
  "direction": "cross",   // "above" | "below" | "cross"  (validated server-side)
  "repeat": false,         // false = one-shot (fires once); true = persistent
  "note": ""
}
```
- **`direction: "cross"`** already exists end-to-end (it fires on a crossing in EITHER direction) — the
  web UI exposes "Crosses / Above / Below". Add the same three-way picker on the phone; default `cross`.
- **`repeat`** (NEW): `false` is the current one-shot behavior (fires once, then it's done). `true` makes
  the alert **persistent** — it re-arms after each crossing and stays until the user deletes it
  (debounced by a 60 s cooldown so tick-noise at the line can't spam pushes).

The alert rows returned by `GET /api/price-alerts` now include `repeat` (0/1) and `last_triggered_at`
(ISO string or null). Suggested display: a `↻` glyph / "persistent" label for repeat alerts, and for a
fired persistent one show "last fired {time}" from `last_triggered_at` (it stays in the active list).
A one-shot that fired drops out of the active list as before.

(The fired-alert WS frame + APNs push shapes in §2/§3 are unchanged — `repeat` doesn't alter them; a
persistent alert just fires the same flat alert + push each time it crosses.)

## Still open (not in this round)
- **Extended-hours INTRADAY bars** — the 5-min `intraday[]` array on the REST payload is still
  RTH-only. No pre/post chart segments yet (Schwab pricehistory in extended-hours session is a
  separate fetch path; deferred). Headline ext price IS live (above); just no pre/post line on the
  chart.
- **Reorder cross-platform sync** — backend supports `PUT /api/watchlists/{id}` with a `symbols`
  array; phone-side wiring is yours when you're ready.

## Compatibility
- Older fields (`price`, `change_pct`, `sparkline`, `bid`, `ask`, `volume`) unchanged. New ext fields
  are nullable on older payloads — treat missing as "no ext data, render regular as today".
- Rule-alert frames untouched.
