# Order-Flow GUI — Footprint + DOM viewer (spec)

*Build target: a best-in-class, interactive order-flow viewer — **footprint chart + DOM ladder + liquidity
heatmap + raw order tape** — for /ES MBO data. Built **native to the app** (Next.js/React + FastAPI) so it
works on our **historical** Databento MBO now and can become a **live widget inside help_me_trade** later with
no rewrite. Purpose: study Adam's setups → an absorption **setup scanner** (the propose-half) → live
decision-support. NOT a manual execution terminal.*

## Hard requirements (the bar)
1. **Best-in-class rendering, not research-grade.** Dense, crisp, smooth zoom/pan. → **HTML5 Canvas** for
   footprint/DOM/heatmap (the only thing that renders these well). Plotly/Streamlit are explicitly rejected.
2. **Historical now, live later — same component.** The data schema is **source-agnostic**: historical
   (parquet replay) and a future live MBO feed populate the **identical JSON shapes**. Live = same component,
   different data source (REST/replay now → WebSocket later). Bake this in from day one.
3. **In-app widget.** It's a React component in `frontend/`, mountable as a standalone route AND as a
   `react-grid-layout` dashboard widget. Never an iframe/separate server.
4. **Tied to Adam's calls.** Each call's window overlays his level, call/entry/exit times (ET), direction,
   and outcome (points_net, exit_reason).

## Stack (all already present)
- Backend: **FastAPI** (`api/`), router-per-feature pattern (`api/*_routes.py` + `include_router`). Existing
  WebSocket `/ws/market` is the live-mode template.
- Frontend: **Next.js 16 + React 19 + TypeScript + Tailwind v4**, **`lightweight-charts` 5.2 already
  installed**, `react-grid-layout` for widgets.
- Data engine: **`research/_edgehunt/mbo_engine.py`** — reuse `load_mbo`, `extract_trades`,
  `reconstruct_book`, `build_candles`, `compute_absorption_read`, `select_calls`. (The PNG `render_png` is
  abandoned — we serve JSON instead.)
- Data: `data/research/_edgehunt/orderflow/<post_id>_mbo.parquet` (97 calls). Calls:
  `data/research/adam_trigger_study.csv`.
- Reference: **github.com/gty3/dom-replay** (React + Canvas + Databento MBO DOM ladder, **MIT**) — adapt its
  ladder rendering; ignore its AWS/SST/Rust/live-websocket infra (mismatched — we replay local parquets).

## Rendering approach
- **Price/candles + time axis + zoom/pan/crosshair:** Lightweight Charts (already installed) drives the
  time scale. **Footprint cells, DOM ladder, and heatmap are drawn on a Canvas layer synced to LWC's
  time/price coordinate mapping** (LWC v5 exposes `timeScale().coordinateToTime`/`logicalToCoordinate` and
  price-scale conversions). If LWC sync proves limiting for the dense footprint, fall back to a fully custom
  canvas with a shared time/price transform — the schema/endpoints don't change either way.
- /ES tick = **0.25**. Footprint cell = (price-tick row × time-bar col) → `{bidVol, askVol}`, colored by
  imbalance; numbers readable when zoomed. DOM ladder = right-side price column with bid-size │ price │
  ask-size at the cursor time; liquidity heatmap = resting size (color) over price×time (Bookmap-style),
  which is where the **iceberg holds** are visible.

## Source-agnostic data schemas (the contract — historical AND live emit these)
```
GET /api/orderflow/calls
  -> [{ post_id, date, direction, level, points_net, exit_reason, has_mbo }]   // selector list

GET /api/orderflow/{post_id}/meta
  -> { post_id, symbol, post_ts, entry_ts, exit_ts, exit_px, level, direction,
       points_net, exit_reason, window_start, window_end, tz:"America/New_York" }

GET /api/orderflow/{post_id}/candles?bar=30s
  -> { bars: [{ t, o, h, l, c }] }                          // t = epoch ms (UTC); client renders ET

GET /api/orderflow/{post_id}/footprint?bar=30s&tick=0.25
  -> { bars: [{ t, cells: [{ price, bidVol, askVol }], delta, cumDelta }] }

GET /api/orderflow/{post_id}/book?bar=30s            // book snapshots for DOM + heatmap
  -> { snapshots: [{ t, bids: [{price,size}], asks: [{price,size}] }] }

GET /api/orderflow/{post_id}/tape?limit=...&from=...&to=...
  -> { events: [{ t, action, side, price, size, order_id }] }   // raw order viewer

(LATER, live) WS /ws/orderflow?symbol=ES
  -> streams the SAME { footprint-bar | book-snapshot | tape-event } messages, appended live.
```
All times epoch-ms UTC over the wire; the client formats ET. `bar` (e.g. 30s/1m) and `tick` are params so we
can tune granularity without backend changes.

## Frontend component
- `<OrderFlowViewer callId | live={symbol}>` — one component, two data sources behind a `useOrderFlowData`
  hook (REST+replay now; WS later). Panes, vertically stacked + resizable:
  1. **Price/candles** (LWC) with Adam's level line + call/entry/exit vertical markers (ET).
  2. **Footprint** (canvas, time-synced) — bid×ask cells + per-bar delta.
  3. **Liquidity heatmap** (canvas) — resting book over time (iceberg view).
  4. **DOM ladder** (canvas/HTML, right rail) — bid/price/ask sizes at the scrub cursor.
  5. **Order tape** (virtualized table) — raw events, filterable by side/size.
- **Time scrubber + playback** (play/pause/speed) for replay; a crosshair syncs all panes + the DOM ladder.
- **Call selector** (dropdown of the 97, outcome-tagged). Standalone route `/orderflow`; also exported as a
  dashboard widget.

## Staged build plan (autonomous: build → test → refine → next)
1. **Backend** — `api/orderflow_routes.py`: all REST endpoints above, reusing `mbo_engine`. **Test:** for a
   real `post_id`, each endpoint returns well-formed JSON with sane values (book bids<mid<asks; footprint
   delta matches trades; tape ordered). Add a pytest. Wire `include_router`. `ruff` + `pytest` clean.
2. **Frontend MVP** — `/orderflow` route + `<OrderFlowViewer>` shell: **order tape + DOM ladder** for one
   call from the live backend (your "just see the orders," done right). **Test:** `npm run typecheck` +
   `build` clean; component fetches + renders; capture a headless screenshot if Playwright/puppeteer is
   available, else note for visual review.
3. **Footprint + candles pane** (canvas synced to LWC) + **liquidity heatmap**. **Test:** typecheck/build;
   footprint cells align to price/time; screenshot.
4. **Scrubber + playback + crosshair sync + call selector** (97 calls, outcomes). **Test:** typecheck/build;
   scrub updates DOM ladder + crosshair across panes; screenshot.
5. **Dashboard widget** wrapper (react-grid-layout) + polish (colors readable, performance at full zoom).
6. **(Later) Live mode** — `/ws/orderflow` + WS data source in the hook; same schemas. Not in this pass.

## Constraints / safety
- **Additive only.** New files: `api/orderflow_routes.py`, `frontend/.../orderflow/*`. Touch `api/main.py`
  only to `include_router`. **Do NOT touch `trading/`, execution, risk, `db/order_executions`** — out of scope.
- Always `PYTHONIOENCODING=utf-8` for any Python run (Windows console).
- Pre-done each stage: `ruff check .` (changed py) + `pytest -q` (new tests) + `cd frontend && npm run
  typecheck`. Don't commit until a stage is verified; keep stages in the working tree for review.
