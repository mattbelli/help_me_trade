# SERV_HANDOFF — price alerts + WS streaming (for the serv/backend agent)

*Written 2026-06-24 by the RN/mobile agent. Three serv-side items the mobile app needs. The mobile
app owns `mobile/`; serv owns the API + the market stream. Mobile changes referenced here are on
branch `feat/mobile-app` (not merged).*

---

## Context: what mobile just shipped (so you know the consumer)
- Ticker detail now shows **company info + EPS-beat** (tap the ticker → discloses a panel). Consumes the
  new quote fields (`pe`, `eps`, `div_yield`, `market_cap`, `last_earnings`, `next_earnings`) and
  `GET /api/fundamentals/earnings-history`. **These are great — no changes needed.**
- Mobile now **subscribes the watchlist symbols on `/ws/market`** and merges `{type:'quote'}` ticks into
  the watchlist cache (previously it never called `subscribe()` / never consumed `onQuote`, so the
  watchlist rode only the 6s REST poll). This is why "prices felt frozen." But it surfaced two serv gaps
  below (#1 streaming).

---

## 1. WS quote frames don't carry extended-hours price → watchlist looks frozen pre/post-market
**Symptom (founder, ~9 AM):** watchlist LAST price not updating; CHG crawls every ~12s; "if we're
streaming over WS shouldn't price tick many times/sec?"

**Root cause (serv side):**
- `brokers/market_stream.py` `_quote_payload()` emits only `{price, change_pct, sparkline}`, where
  `price` is the **regular-session** last (computed off `prev_close`). The frame has **no extended-hours
  price**.
- During pre/post market the mobile watchlist row headlines `ext_price` / `ext_change_pct` (the gold
  PRE/AFT badge), which come **only** from the 6s REST `/api/watchlist/market` path. The WS never
  updates them → the visible number only moves on the poll. The user's own hypothesis was right.

**1b. SEPARATE, CONFIRMED serv bug — the REST `ext_price` itself is STALE/WRONG in pre-market.**
Observed 2026-06-24 **09:27 ET (pre-market)**, `GET /api/watchlist/market?symbols=TSLA`:
```jsonc
{ "price": 381.61,            // last regular-session print (correct as "close", frozen)
  "change_pct": -5.79,
  "prev_close": 405.05,
  "market_session": "pre",
  "ext_price": 383.50,        // <-- what the watchlist headlines
  "ext_change_pct": -0.17 }   // <-- tiny move
```
Real pre-market TSLA at that moment was **~380.80** (down from the 381.61 close). serv's `ext_price`
(383.50) was **stale by ~$2.70 and the wrong direction** — and 383.50 happens to be where the founder set
alerts ~30 min earlier, i.e. it looks frozen at an old value. So this is NOT just "WS doesn't carry ext
price" — even the **6s REST `ext_price` is not tracking the live extended-hours quote**. Find out why the
ext-hours snapshot is stale (wrong/delayed data source? cached? only refreshed at session boundaries?) and
make `ext_price`/`ext_change_pct` follow the real pre/post quote. This is the headline number on the
watchlist, so it's the most visible wrongness.

**Asks:**
1. **Extend the WS quote frame to carry extended-hours pricing** during pre/post/overnight sessions, so
   the stream can drive the headline. Proposed contract (mobile will consume whatever you settle on —
   let's agree before either side codes):
   ```jsonc
   { "type":"quote", "symbol":"AAPL",
     "price": 294.30, "change_pct": -0.91,        // regular session (unchanged)
     "market_session": "pre|regular|post|overnight|closed",
     "ext_price": 295.10, "ext_change_pct": 0.27, // present only when session != regular
     "sparkline": [...] }
   ```
   When you ship this, mobile will widen its `QuoteUpdate` type + merge to apply `ext_price`/
   `ext_change_pct`/`market_session` (today it merges only `price`/`change_pct`/`sparkline`, on purpose,
   to avoid clobbering the REST-supplied ext fields).
2. **Verify the upstream is actually delivering high-frequency ticks** now that serv "updated to Schwab
   websocket streaming." `_handle_alpaca_message()` only routes message types `"t"` (trade) and `"db"`
   (daily bar). If the Schwab streamer emits **level-one quote** frames in a different shape, serv may be
   silently dropping them → few/no `{type:'quote'}` broadcasts → mobile sees nothing to merge. Confirm
   the Schwab frames are parsed and broadcast (and that ext-hours trades/quotes flow, not just RTH).
   - Note the free-tier caveat if any Alpaca fallback remains: IEX trades are sparse (a few/min for many
     names), which also reads as "frozen." Schwab level-one should be far richer.

**Mobile-side note (not yours):** the fix above only helps once the device runs the new bundle — the
founder is testing on the **physical device**, which still has the OLD JS (the subscribe/merge change is
in the working tree / on the sim only). Mobile agent will handle redeploying to the device.

---

## 2. A fired price alert renders near-blank in the mobile Inbox (delivery shape mismatch)
**Bug:** `MarketStreamHub._check_price_alerts()` broadcasts
```python
{"type":"alert", "symbol":symbol, "price":current, "alert": <triggered alert dict>}
```
i.e. the descriptive content is **nested under `alert`**. But the mobile WS parser (`parseAlert` in
`mobile/src/api/use-market-ws.ts`) reads **top-level** `id`/`rule_name`/`action`/`ts`. So a triggered
price alert shows up in the Inbox as a bare row (synthetic id, no "AAPL crossed above $X" text).

Contrast: `broadcast_alert()` (rule alerts) correctly spreads fields **flat** (`{type:'alert', id, ts,
**payload}`) — those render fine.

**Ask:** make the price-alert broadcast match the flat rule-alert shape — emit top-level
`id`, `ts`, `symbol`, `price`, a human `rule_name`/title (e.g. `"AAPL ↑ $201.00"`), and a `direction`,
rather than nesting under `alert`. (Mobile can also defensively read a nested `alert`, but a consistent
flat contract is cleaner — let's align.) There are 5 alerts armed right now (TSLA, AMD, NVDA), so this
is testable as soon as one crosses.

---

## 3. Price alerts have no push delivery (WS-only) → missed when the app is closed
`_check_price_alerts()` pushes **only to connected `/ws/market` clients**. There's no APNs path, so if the
app is backgrounded/closed the user never learns the alert fired. This cuts against the core mission
("ping his phone while he's away").

**Ask:** route triggered price alerts through the same **direct-APNs** transport the proposals use
(`trading/proposals.py` push slice / device-token registry), as an actionable/standard notification.
Mobile already registers device tokens (`registerDevice` / `usePush`) and handles the proposal category;
a price-alert push category can reuse that wiring. Lower priority than #1/#2 but it's the difference
between "alerts work while I'm staring at the app" and "alerts work."

---

## Quick repro / test handles
- Live price alerts: `GET /api/price-alerts` (5 armed as of writing).
- WS: `ws://serv.tail78aab8.ts.net:8000/ws/market?token=<API_TOKEN>` → send
  `{"action":"subscribe","symbols":["AAPL","TSLA"]}`; observe `{type:'quote'}` cadence + fields.
  (Direct external WS clients hit a Host/token gate — easiest to verify from serv-side logs or the app.)
- Mobile consumer files: `mobile/src/api/use-market-ws.ts` (parser/contract),
  `mobile/src/components/pager/cockpit-pager.tsx` (subscribe + merge),
  `mobile/src/features/watchlist/watchlist-screen.tsx` (`sessionView()` headline logic).
