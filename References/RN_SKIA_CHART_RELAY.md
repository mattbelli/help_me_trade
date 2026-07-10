# RN relay — mobile Skia chart state + shared drawings/alerts layer (mobile → web-charts agent)

**TL;DR:** The phone already has a native Skia candlestick/line chart (WIP on the mobile workstation,
summarized here because the code isn't pushed yet). **Price alerts are already a shared layer today** —
both clients render the same `/api/price-alerts` rows; match the visual conventions below and that layer
is done. **Drawings are the open contract**: web already hits `GET /api/drawings` (seen in serv access
logs), mobile has nothing — before you bake in a schema, please confirm/publish it per §3 so mobile can
adopt it as-is. One hard requirement: **anchor everything in data space (`{time, price}`), never pixels.**

---

## 1. What exists on mobile (so you don't design blind)

`mobile/src/features/chart/skia-chart.tsx` — `SkiaChart`, @shopify/react-native-skia 2.6.2:

- **Modes:** `candle` | `line` (line = stroke + gradient area fill).
- **Data:** plain `Bar[]` from `GET /api/bars` (the contract in `RN_POSTMARKET_CHARTS_RELAY.md` —
  `{time: ISO-UTC, open, high, low, close, volume}`), plus a `livePrice` prop that keeps the last
  candle + a dashed accent "live" line in sync with the streaming quote.
- **Interactions (all UI-thread):** horizontal pan with decay fling, pinch zoom (12–300 candles,
  focal-point anchored), long-press crosshair with OHLC legend + axis price tag. Viewport auto-fits
  the visible price range per frame.
- **Alert overlay (built):** each alert renders as a dashed horizontal line + right-axis price tag.
  Colors: `above` → positive/green, `below` → negative/red, `cross` → muted/neutral; line opacity 0.55,
  dash `[6,4]`. Out-of-range levels hide rather than clamp.
- **Create-from-chart (built):** releasing the crosshair surfaces a transient "＋ alert @ price" pill;
  tapping it POSTs a price alert at that price. Worth mirroring on web for parity.
- **Overlay seams:** the chart maps everything through two pure helpers (`xFor(index)`, `yFor(price)`)
  inside a clipped pane group — new overlay types (SMA, drawings) are additive elements using the same
  helpers. i.e. mobile can consume any data-space drawing schema cheaply.
- Also: `eps-chart.tsx` (static quarterly EPS estimate-vs-actual lollipop, react-native-svg) — not part
  of the shared layer, listed for completeness.

## 2. Alerts — the shared layer that already works

Single source of truth: `/api/price-alerts` (CRUD, `{id, symbol, price, direction: "above"|"below"|"cross",
note?, triggered?, repeat?}`). Mobile subscribes via React Query + renders `{id, price, direction}` on the
chart. Nothing new needed server-side — if the web Skia chart renders the same rows with roughly the same
conventions (dashed level + axis tag, direction colors), phone and web are visually one layer.

## 3. Drawings — the contract to agree on BEFORE either side bakes one in

Web already calls `GET /api/drawings`; mobile has no drawings code yet, so **whatever you publish here,
mobile will adopt** — please add the schema (or a pointer to it) to this doc or a sibling relay doc.
Requests from the mobile side:

1. **Data-space anchors only.** Every point = `{time: ISO-UTC-string-or-epoch, price: number}`.
   No pixel/viewport coordinates anywhere in the persisted shape — the two clients have different
   viewports, densities and pan states.
2. **Typed shapes, small vocabulary.** Suggested minimum: `hline` (one price), `trendline` (two
   anchors), `ray` (two anchors, extends right), `rect` (two corner anchors), `note` (one anchor +
   text). An explicit `type` field so clients can skip shapes they don't support yet.
3. **Keyed by symbol** (`GET /api/drawings?symbol=AAPL` or equivalent) with stable `id`s for
   edit/delete sync.
4. **Style as semantics, not hex.** `{color?: "positive"|"negative"|"accent"|"muted", dash?: bool,
   width?: 1|2}` — each client maps to its own theme palette (mobile has custom themes; raw hex from
   web would clash).
5. **Change propagation:** mobile polls/refetches fine for v1; if you add a WS frame for drawing
   mutations, a flat `{type:"drawing", action:"upsert"|"delete", symbol, drawing}` matches how the
   existing `alert` frames look on the market WS.

## 4. Coordination notes

- Mobile chart code lives on `feat/mobile-app` (uncommitted WIP at time of writing — this doc is the
  readable summary). Ping via References/HANDOFF.md or board.json `founder` lane if you need the code
  pushed early.
- Shared-doc conflict surface between our branches is `References/board.json` + `BUILD.md` history —
  both sides append; merge conflicts there are expected noise, resolve by keeping both rows.
