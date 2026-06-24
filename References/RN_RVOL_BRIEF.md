# RN app brief — add the RVOL column (+ extended-hours pricing) to the phone watchlist

*For the Mac/RN agent (owns `mobile/`). serv owns the backend contract below. The RVOL field is
**LIVE on the deployed backend right now** — you can build + test against it immediately.*

## What the backend now provides
`GET {API_BASE}/api/watchlist/market?symbols=AAPL,MSFT` (auth: `Authorization: Bearer ${API_TOKEN}`,
same as today) returns `{"quotes": { "<SYM>": Quote, ... }}`. The `Quote` object gained one field now,
and three more are landing shortly (extended hours):

```ts
type RvolInfo = {
  value: number | null;                 // the multiplier, e.g. 2.34; null while "warming"
  state: "warming" | "live" | "settled" | "overnight";
};

type Quote = {
  price: number;
  change_pct: number;
  sparkline: number[];
  prev_close?: number;
  volume?: number;
  day_high?: number;
  day_low?: number;
  intraday?: number[];                   // RTH 5-min closes (already used for the 1-day chart)
  rvol?: RvolInfo | null;                // ✅ LIVE NOW
  // ⏳ landing shortly (extended-hours feature, backend in progress):
  market_session?: "pre" | "regular" | "post" | "closed";
  ext_price?: number | null;            // pre/post last print (only meaningful when session is pre/post)
  ext_change_pct?: number | null;
};
```
The live WS at `{WS_BASE}/ws/market` (same subscribe protocol you already use) now streams **Schwab
real-time** quotes; `rvol` arrives via the REST payload (it's slow-moving, recomputed each poll), so make
sure your WS-tick merge **preserves** `rvol` rather than overwriting the whole quote object (the web client
hit exactly this bug — spread the prior quote, then apply price/change/sparkline from the tick).

## RVOL column — display rules (finalized with the founder)
- Render the value as a multiplier: **`2.3×`** (one decimal).
- **Color band:** `<1.5` neutral/dim · `1.5–2` yellow · `2–3` amber/orange · `>3` red.
- **States:**
  - `warming` (first ~15 min after 9:30, `value` is null) → show a muted `—`.
  - `live` / `settled` → show the value with its band color (settled = post-close full-day figure).
  - `overnight` → show the value **greyed/de-emphasized** (it's yesterday's final), e.g. `opacity 0.5`.
  - `rvol` missing/null → `—`.
- Meaning, for any tooltip/label: "today's volume vs. what's normal *for this time of day*" (a real
  20-day time-of-day volume profile on Schwab consolidated volume — not a flat daily average).

## Extended-hours (when those fields land)
- When `market_session` is `pre` or `post`: show `ext_price` as the headline price with a small **`PRE`/`AFT`
  badge** and use `ext_change_pct` for the up/down color. When `regular`, use `price`/`change_pct` as today.
  When `closed`, show the last regular `price` (no badge).
- This mirrors what the founder sees in thinkorswim (pre/post prices). Backend exposes it per-symbol; no
  extra call needed — same `/api/watchlist/market` payload.

## Scope
- Watchlist screen on the phone: add the RVOL column/badge; wire extended-hours price+badge when the fields
  appear. Keep it tasteful on a narrow screen (the founder's mobile-UX direction: native, uncluttered).
- No backend changes needed from your side — this is the contract. If a field is missing, treat as null and
  fall back gracefully (older backend / pre-deploy).

## Ship
- Build + deploy the RN app to the founder's phone (your usual EAS/dev path) so RVOL shows up on device.
- The push-approval slice is unaffected; this is watchlist display only.
