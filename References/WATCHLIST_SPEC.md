# Watchlist v3 — design spec

*Status: DRAFT for review (2026-06-20). Supersedes the parked watchlist-v2 (to be reverted). The watchlist is
the glanceable radar — the heart of the phone app — and the one surface where dense, parallel, zero-latency
data beats the AI. Principle: **surface the anomaly, not the raw number** — express every metric relative to
its own normal, and visually flag outliers so the eye is pulled to what's abnormal without reading.*

## Principles
- **Glanceable / anomaly-first.** Ratios, %-of-normal, position-in-range — never bare numbers. Badges/color
  "light up" only when notable, so a 15-name list shows you where to look in half a second.
- **Complementary to the AI.** Watchlist = parallel real-time glance (the radar). AI = on-demand narrative
  (the analyst). Tap a row → ask the copilot "why is X spiking?". Don't make the watchlist narrate.
- **One component, desktop + phone.** Responsive; same data + customization. Phone shows fewer columns by
  default but stays personal (customization ships to both).
- **Customizable columns.** A gear opens a column manager (drag to reorder, toggle add/remove), persisted
  server-side so it syncs desktop ↔ phone.

## Column catalog
Default order follows the founder's thinkorswim preference: **ticker → %chg → last → volume(+RVOL) → 52w → …**

| Column | Definition | Source | Cadence | Visual |
|---|---|---|---|---|
| Ticker | symbol | — | static | bold; tap → focus/chart-link (channel) |
| % Change (day) | vs prior close | Alpaca WS | real-time | **color-intensity by magnitude** (primary glance) |
| Last / Mark | current price | Alpaca WS | real-time | number |
| Volume | today cumulative | Alpaca | real-time | abbreviated (1.3M) ⚠️ feed caveat |
| **RVOL / above-expected %** | vol vs expected-for-time-of-day | Alpaca bars (baseline) | real-time | **badge, lights up >Nx** ⚠️ feed caveat |
| **52w range slider** | position in 52w hi–lo + marker/arrow | Alpaca daily bars | hi/lo daily, marker live | **mini bar viz** (Yahoo-style) |
| Day sparkline | intraday line | Alpaca WS | real-time | sparkline |
| Move vs typical | today's move ÷ ATR | Alpaca bars | real-time | "2.1× normal" badge |
| vs SPY | rel. perf today | Alpaca | real-time | signed % (stock-specific vs beta) |
| From 50/200 DMA | % above/below | Alpaca bars | daily | signed % |
| 52w high/low flags | new high/low today | Alpaca bars | real-time | "52w high 🔥" badge |
| **P/E** | trailing P/E | **Finnhub** | daily cache | number |
| Market cap | — | Finnhub | daily | abbreviated |
| **Earnings in** | days to next earnings | **Finnhub** | daily | "3d" badge, flag if < N |
| Put/Call ratio | equity P/C | ❌ options source | — | **DEFERRED (needs paid data)** |
| Position P&L % | if held | internal | real-time | color |
| Alert distance | to nearest set price alert | internal | real-time | signed % |

## Default column sets (both reorderable)
- **Desktop:** Ticker · %Chg · Last · Volume+RVOL · 52w slider · Sparkline  *(P/E, Earnings opt-in)*
- **Phone:** Ticker · %Chg · Last · RVOL badge · 52w slider (compact)

## Customization
- Gear icon on the widget → **column manager** panel that mirrors the row layout: a list of available columns,
  drag to reorder, toggles to add/remove. Persisted via settings (server-side) so desktop ↔ phone stay in sync.
- Open decision: per-watchlist column sets vs. one global set (lean: global to start, simpler).

## Data architecture (3 tiers by cadence)
1. **Real-time tier** — extend `market_hub`'s per-symbol payload with server-computed signals: price, %chg,
   volume, RVOL, 52w-position, move-vs-ATR, vs-SPY, DMA distance, sparkline. Pushed over the existing WS →
   **the phone gets all of it for free**, no client number-crunching.
2. **Daily / slow tier** — a once-daily job pulls Finnhub fundamentals (P/E, market cap) + earnings calendar
   per watchlist symbol → cached in SQLite (new `symbol_fundamentals` table) → merged into the payload.
   Cheap; fits Finnhub's free tier.
3. **Deferred tier** — put/call ratio + options/IV → needs a dedicated options-data source (paid). Flagged,
   not built.

Baselines (avg volume by time-of-day, ATR, 52w hi/lo, 50/200 DMA) are computed from the **300-day daily bars
we already fetch**, refreshed daily and cached.

## Data sources — recommendation
- **Alpaca** (have it): all price/volume/52w/sparkline/news/real-time.
- **Finnhub free tier (add):** P/E, market cap, earnings calendar — daily cache. Recommended.
- **Not recommended:** Alpha Vantage (25/day), yfinance/Yahoo (unofficial, fragile). FMP is a fine
  alternative to Finnhub; Polygon if we later want options too (paid).
- **Put/Call ratio:** deferred — needs options-volume data (no good free source).

## ⚠️ Key caveat — consolidated volume / RVOL accuracy
The flagship "above-expected volume" needs accurate **consolidated** volume. The free **IEX feed sees only
~2–3% of market volume**, so intraday RVOL off it is unreliable in absolute terms. Options:
- (a) **Alpaca SIP feed (~$99/mo)** — true consolidated real-time volume.
- (b) **Approximate** — use delayed/EOD consolidated volume + intraday IEX as a trend proxy.
- (c) **Accept IEX** as a rough directional proxy (absolute % unreliable).
**Decision needed** before RVOL is trustworthy. (Daily bars ARE consolidated, so EOD volume + 52w + ATR are
accurate regardless — only the *intraday* RVOL number is feed-sensitive.)

## Phasing
- **Phase A (zero new deps):** real-time tier + 52w slider + sparkline + customization + the Alpaca-derived
  signals (RVOL with the feed caveat, ATR-move, vs-SPY, DMA, 52w flags). Ships a great watchlist immediately.
- **Phase B (Finnhub):** P/E, market cap, earnings proximity (daily cache).
- **Phase C (deferred):** put/call ratio / options-IV (needs a paid options source); SIP feed if we want
  trustworthy intraday RVOL.

## Decisions (locked 2026-06-20)
- **RVOL feed:** ✅ build on **IEX-as-proxy now, behind a `market_data_feed` switch** (iex|sip). Flip to SIP
  when the founder activates it — RVOL math is feed-agnostic, zero rework. Label the number "approx" on IEX.
- **Finnhub:** ✅ Phase B, right after A.
- **Phone viewing does NOT require R3.** The watchlist is a responsive web component — viewable on a phone
  browser + "Add to Home Screen" (PWA: icon + full-screen glance), reaching the backend via Tailscale (now)
  or a small deploy (later). R3/Capacitor's unique value is **push notifications** (the approve-a-trade flow),
  which a *glance* surface doesn't need. So: build the watchlist responsive + PWA-ready; R3 stays the later
  "push" milestone. (Caveat: all-day phone use needs the backend reachable 24/7 — machine-on+Tailscale now,
  small always-on deploy eventually.)

## Still open
- **Customization scope:** per-watchlist columns or one global set? (Lean: global.)
- Put/Call ratio — budget a paid options source eventually, or drop it? (Deferred either way.)

## Build approach
Revert the parked watchlist-v2 first (clean slate). Then Phase A as a dispatched coder build against this spec
(routing per `AGENT_WORKBENCH.md`), with
the server-side signal computation reviewed (it feeds the phone). Salvage + commit the scans/memory widgets
separately so they leave the parked pile.
