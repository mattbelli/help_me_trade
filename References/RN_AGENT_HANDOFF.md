# RN_AGENT_HANDOFF.md — next session pickup (mobile/)

*For the Mac/RN agent that owns `mobile/`. Written 2026-06-24 at the end of a long session, right
before a context clear. Goal: pick up cold and build the next three features. serv owns the backend.*

## 0. Orient first (5 min)
- **Auto-memory** `help-me-trade-mobile-infra` (recalled automatically) has the full infra + gotchas — trust it.
- Repo: `/Users/matt/Coding/help_me_trade`, branch **`feat/mobile-app`**. App in `mobile/`. Not merged to main.
- Companion docs: `References/RN_APP_BRIEF.md`, `References/RN_UI_PLAYBOOK.md`, `References/RN_RVOL_BRIEF.md`, root `CLAUDE.md`.
- serv runs at `http://serv.tail78aab8.ts.net:8000` (plain HTTP over Tailscale). Auth header `X-API-Token` (token in `mobile/.env` / `.env`, gitignored).

## 1. State (what's already built + shipped to the phone)
- **Watchlist** (`mobile/src/features/watchlist/watchlist-screen.tsx`) — the most-iterated screen:
  - Full-bleed rows: `SYM` (+`24h`/`PRE`/`AFT` session badge above it in warm gold) · `[H/LAST/L]` · `[CHG%/CHG$]` · `[RVOL/vol]` · 1-day chart.
  - **RVOL column** + **extended-hours pricing** — RN_RVOL_BRIEF is **fully done** (see §3).
  - Tap a header to **sort** (desc→asc→user). **Drag-reorder** (long-press, persisted to serv). **Add ticker** (`+` top-right → inline row + `/api/symbols/search` autocomplete, validated). **Edit mode** (long-press → jiggle + Done + red minus-badge delete).
- **Frosted pill** nav (`mobile/src/components/nav/tab-bar.tsx`) — `expo-blur` BlurView, warm-gold active tab.
- **Native build is current** on sim + device with 4 modules linked (see §2).
- Recent commits on `feat/mobile-app`: …`e1814d4` (warm accent), `fc1d43f` (native rebuild + frosted pill).

## 2. Build these next — all pure JS, NO rebuild needed (modules already linked)
`react-native-webview`, `expo-linear-gradient`, `@react-native-async-storage/async-storage` are installed
+ in the native build. Wiring their features is plain JS + a JS reload. Suggested order:

### A. Candle chart on the ticker detail  (react-native-webview + lightweight-charts)  ← do first, biggest visible win
- Screen: **`mobile/src/app/symbol/[symbol].tsx`** (reached by tapping a ticker; today shows a sparkline + prev-close/high/low/volume + price alerts).
- Data: **`useBars(symbol, timeframe, days)`** (`mobile/src/api/queries.ts:134`) → `getBars` (`endpoints.ts:78`) → `/api/bars` returns `{bars:[{time,open,high,low,close,volume}]}`.
- Plan: a `WebView` hosting **lightweight-charts** (TradingView's free, **Apache-2.0** lib — no license/agreement; the full TradingView *widget* would need a license, do NOT use that). Render candles; add a line⇄candle toggle and a timeframe selector (1D/1W/1M/1Y). Theme dark, teal/green up + red down to match.
- Gotchas: **inline** the lightweight-charts JS + HTML in the WebView (`source={{html}}`) so it works offline/reliably; feed bars via `injectedJavaScript`/`postMessage`; transparent/`colors.bg` background; disable WebView bounce/scroll. Keep the existing simple sparkline as the default and candles behind the toggle.

### B. Gradient fills  (expo-linear-gradient)
- Equity-curve **area fill** under the Home portfolio line (`mobile/src/features/portfolio/portfolio-screen.tsx`) — fade the pnl color to transparent below the curve. That chart is SVG (react-native-svg), so an **SVG `<LinearGradient>` fill under the path** is cleanest; use `expo-linear-gradient` for non-SVG surfaces (card depth, header fades).
- Keep it subtle — the founder's direction is "polished dark, uncluttered."

### C. Persisted UI prefs  (async-storage)
- Persist across launches: watchlist **sort** (`{key,dir}`), the **1/2-col** toggle (currently parked — `columns` is a const in watchlist-screen; revive it if you re-add the toggle), **last active tab**, optionally per-symbol chart prefs.
- Build a tiny `usePersistedState(key, default)` hook (AsyncStorage get on mount, set on change) and swap the relevant `useState`s.

## 3. RN_RVOL_BRIEF.md — STATUS: DONE ✅ (nothing to pass off)
- RVOL column: `rvol: {value, state}` → `2.3×` with color bands (`<1.5` dim · `1.5–2` yellow · `2–3` amber · `>3` red), states handled (warming/null→`—`, overnight→opacity 0.5). `rvolDisplay()` in watchlist-screen.
- Extended-hours: `market_session` (incl. `overnight`) + `ext_price` + `ext_change_pct` wired — `sessionView()` headlines `ext_price` with a gold `PRE`/`AFT`/`24h` badge above the ticker, colors by `ext_change_pct`, CHG$ = `ext_price − price` (vs regular close, so sign matches the %).
- WS-merge gotcha from the brief is **N/A here**: the RN watchlist is REST-poll only (`useWatchlistMarket`, 6s) — `useMarketWs` does not merge quotes into the watchlist cache, so `rvol` is never clobbered. (Relevant only if someone later adds WS-driven price ticks — then spread the prior quote and apply only price/change/sparkline.)

## 4. Pending serv dependencies (NOT RN work — flag to the serv agent)
- **Extended-hours INTRADAY bars** — the only RVOL-adjacent thing not buildable yet. Today's `intraday` array is **RTH-only** (09:30–16:00). To draw the **dimmed pre/post-market chart segments + vertical 4pm/9:30 session lines** on `mini-day-chart.tsx` (the founder's 24h-trading-view idea), serv must add an extended-hours intraday series (or per-bar session flags). Wire `MiniDayChart` when it lands.
- Watchlist **reorder-persist**: DONE (`PUT /api/watchlists/{id}` with `{symbols}`).

## 5. Infra + gotchas (don't relearn the hard way)
- **Typecheck:** `cd mobile && ./node_modules/.bin/tsc --noEmit` — do NOT use `npx tsc` (it fetches a bogus `tsc` package).
- **Metro: one at a time.** Sim: `npx expo run:ios`. Device (UDID `00008130-000225002243001C`): Metro must advertise the Mac's Tailscale IP — start with `REACT_NATIVE_PACKAGER_HOSTNAME=100.95.172.50 EXPO_PUBLIC_API_TOKEN=<token>` (the token enables dev auto-auth). Kill a stale Metro with `lsof -ti tcp:8081 | xargs kill -9`.
- **JS reload (no rebuild)** — device: `xcrun devicectl device process launch --terminate-existing --device 00008130-000225002243001C com.helpmetrade.app`; sim: `xcrun simctl terminate booted com.helpmetrade.app && xcrun simctl launch booted com.helpmetrade.app`. Sim screenshots: `xcrun simctl io booted screenshot /tmp/x.png` (downscale large ones with `sips -Z 700`).
- **Native rebuild ONLY if adding a new native module:** `npx expo install <mod>` → `pod install --project-directory=ios` → `npx expo run:ios [--device <udid>]`. `ios/` is gitignored but signing (Apple team `525TCRWC4Z`, automatic) persists in the pbxproj — no `expo prebuild` needed. (~10 min build.)
- **The Simulator's synthetic mouse CANNOT reliably trigger a finger long-press or drag** — verify edit-mode / reorder / candle gestures on the **physical device**, not the sim. Layout/taps/data verify fine on the sim.
- **Deferred, heavier (own efforts):** `@shopify/react-native-skia` (native charts — only if going native instead of the WebView), and **Live Activity / Dynamic Island** (Phase 3 — a Swift widget extension + ActivityKit push-to-start over the existing direct-APNs transport; not a module drop-in).
- **Safety (non-negotiable):** never touch the `trading/` execution chokepoint; creating proposals (`POST /api/proposals`) places real paper trades and needs explicit per-action user consent; never commit/echo secrets; end commits with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## 6. Key files
- `mobile/src/features/watchlist/watchlist-screen.tsx` — watchlist (rows, RVOL, ext-hours, sort, reorder, add, edit mode).
- `mobile/src/features/watchlist/mini-day-chart.tsx` — 1-day chart (RTH; ext-hours segments TBD per §4).
- `mobile/src/app/symbol/[symbol].tsx` — ticker detail (candle chart goes here, §2A).
- `mobile/src/features/portfolio/portfolio-screen.tsx` — Home + equity curve (gradient fill, §2B).
- `mobile/src/components/nav/tab-bar.tsx` — frosted pill.
- `mobile/src/api/{endpoints,queries,types}.ts` — API layer (`getBars`/`useBars`, watchlist add/remove/reorder/search, RvolInfo, WatchlistsState).
- `mobile/src/theme/colors.ts` — tokens: `accent` teal (primary), `warm` amber/bronze `#e0975b` (secondary).
