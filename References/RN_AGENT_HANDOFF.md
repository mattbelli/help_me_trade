# RN_AGENT_HANDOFF.md — mobile/ pickup

*For the Mac/RN agent that owns `mobile/`. Last big update: 2026-06-24 (long session — charts, IG tab bar,
extended-hours fixes, titles, Live Activity scaffold). serv owns the backend.*

## 0. Orient first
- **Auto-memory** `help-me-trade-mobile-infra` (recalled automatically) has the full infra + gotchas — trust it.
- Repo: `/Users/matt/Coding/help_me_trade`, branch **`feat/mobile-app`** — NOW PUSHED to `origin/feat/mobile-app`
  (was local-only; pushed so the serv agent can read the `References/SERV_*` coordination docs). Not merged to main.
- Companions: `References/RN_APP_BRIEF.md`, `RN_UI_PLAYBOOK.md`, root `CLAUDE.md`,
  `References/SERV_BRIEF_live_activity.md` + `SERV_REPLY_live_activity.md` + `SERV_REPLY_live_activity_ACK.md`
  (Live Activity contract — AGREED), `SERV_HANDOFF_alerts_streaming.md`.
- serv: `http://serv.tail78aab8.ts.net:8000` (plain HTTP over Tailscale). `X-API-Token` header.

## 1. SHIPPED this session (committed on feat/mobile-app)
- **Company-info + EPS-beat disclosure** (`symbol/[symbol].tsx`): tap the ticker → inline panel (P/E, EPS,
  mkt cap, div yield, next/last earnings from the quote; EPS-surprise beat/miss bars from
  `GET /api/fundamentals/earnings-history`).
- **Candlestick charts** — TradingView **lightweight-charts** (Apache-2.0) vendored + inlined in a WebView
  (`features/chart/`). Inline preview on the detail screen (line/candle, 1D/1W/1M/1Y, live-synced to header) +
  a **full-screen route** `app/chart/[symbol].tsx` with an explicit **interval selector** (1m/5m/15m/30m/1h/4h/
  1D/1W), crosshair OHLC legend, full gestures. "Expand" pill opens it.
- **Live WS price streaming** into the watchlist (`cockpit-pager.tsx` passes `onQuote`+`subscribe`; merges
  ticks into the `watchlist-market` cache, preserving rvol/company/ext fields). serv fixed the real cause
  (Schwab tick-drop, `.value` vs `.name`) in main `77690b5`.
- **Extended-hours pricing**: shared `features/watchlist/session.ts` `sessionView()` headlines the ext price +
  PRE/AFT/24h badge in pre/post (watchlist rows, detail header, chart live price). serv's `ext_price` is STALE
  in pre/post (it freezes while ext_change_pct moves — serv bug, item 1b in `SERV_HANDOFF_alerts_streaming.md`),
  so mobile **derives** the ext price = `price × (1 + ext_change_pct/100)`. WS merge keeps the regular-close
  baseline during ext hours. Watchlist **sort is session-aware**. PRE/AFT is **one chip by the watchlist
  title**, not per-row.
- **Price alerts**: parse serv's flat WS frame (`source:'price_alert'`, target/trigger/direction/note); Inbox
  renders "SYM dir $target — now $trigger (note)"; push body-tap deep-links to `/symbol/<sym>`.
- **Inbox**: fixed FlashList-v2 scroll-jump (memoized data + `maintainVisibleContentPosition:{disabled}`),
  tap-an-alert → its chart, **"Clear read"** with persisted dismissal (AsyncStorage).
- **Instagram-style tab bar** (`components/nav/tab-bar.tsx`): frosted `expo-blur` pill + a **teal capsule that
  slides** behind the active icon. (Explored a Skia morphing-membrane first — `@shopify/react-native-skia` got
  installed in the process and is **still a dep but UNUSED by nav**; keep for potential native charts or strip
  on the next native rebuild.)
- **Uniform titles**: new `ScreenTitle` primitive (small lowercase accent mono) across watchlist/inbox/settings;
  dropped the divergent big titles. Home keeps its status line.
- **Instant price on navigation**: `useWatchlistMarket` seeds from the watchlist cache via `placeholderData`.

## 2. IN PROGRESS — Live Activity / Dynamic Island (the killer feature)
**Status: scaffold BUILDS + RENDERS on the Lock Screen (verified on the iOS sim).** Both APNs tokens
(push-to-start + per-activity, 128 bytes) are emitted.
- Module: **`expo-widgets` 56.0.19** (+ `@expo/ui`) — generates the widget Swift from JS, **no hand-written
  Swift** (Phase A). `widgets/trade-proposal.tsx` = the `createLiveActivity` layout (Lock Screen `banner` +
  Dynamic Island slots, Approve/Reject buttons). Temp `__DEV__` Start/End trigger in Settings.
- **Contract AGREED with serv** (`SERV_BRIEF/REPLY/ACK` docs): `attributes-type:"LiveActivityAttributes"`,
  all proposal fields in `content-state` (`proposalId,symbol,side,qty,orderType,winProb[0-1],status`), status
  enum = `pending|approved|executed|rejected|expired|failed` (+ optional `statusReason`). serv is GO to build
  the push slice (token storage → `_send_live_activity` → START/UPDATE/END).
- **Remaining mobile wiring (Phase A):**
  1. **Register tokens with serv** — `addPushToStartTokenListener` → `POST /api/devices {token_type:"la_pushtostart", token}`; per-activity `getPushToken`/`addPushTokenListener` → `POST /api/live-activities {proposal_id, activity_update_token}`. (Endpoints are serv's to build.)
  2. **`addUserInteractionListener`** → target `approve:<id>` opens the app → Face-ID-gated approval card (Phase A safety = open-to-card, no Swift); `reject:<id>` → reject endpoint.
  3. Verify the **Dynamic Island** presentation (only saw Lock Screen — couldn't unlock the sim via clicks; check on device).
  4. **Device build** for the real push flow (sim can't receive remote LA push reliably).
  5. Remove the `__DEV__` test trigger before ship.
- **Phase B (later):** true one-tap gated Approve = a hand-written Swift **AppIntent** with
  `authenticationPolicy=.requiresAuthentication` (expo-widgets' `Button` doesn't expose it).

## 3. NEXT (after Live Activity) + queued
- Gradient fill under the Home equity curve (`expo-linear-gradient`, linked); persisted UI prefs
  (sort/last-tab/chart prefs — AsyncStorage proven). Multiple watchlists, custom columns, Home open-orders.
- serv item **1b** (stale pre-market `ext_price`) — verify fixed at the source next extended-hours session.

## 4. Infra + gotchas (don't relearn the hard way)
- **Typecheck:** `cd mobile && ./node_modules/.bin/tsc --noEmit` (NOT `npx tsc`). Lint: `./node_modules/.bin/eslint <files>` (watchlist-screen has known pre-existing errors — ignore those).
- **babel.config.js EXISTS NOW** (`module.exports = { presets: ['babel-preset-expo'] }`) — it was MISSING and the `'widget'` directive transform (in babel-preset-expo) never ran → `createLiveActivity` threw a String-cast error. If widget/worklet transforms misbehave, clear cache: `npx expo start --clear`.
- **`'widget'`-directive layouts are WORKLET-SCOPED** — the function is stringified + run in an isolated runtime. It CANNOT reference module-scope consts/helpers/imports (only the `@expo/ui` globals). Declare everything INSIDE the function (same rule as reanimated worklets).
- **Metro: one at a time.** Sim: `npx expo run:ios`. Device (UDID `00008130-000225002243001C`): `REACT_NATIVE_PACKAGER_HOSTNAME=100.95.172.50 EXPO_PUBLIC_API_TOKEN=<token>`. The phone must be **unlocked + reachable** or `run:ios --device` errors "No device matching". Kill stale Metro: `lsof -ti tcp:8081 | xargs kill -9`.
- **JS reload (no rebuild)** — sim: `xcrun simctl terminate booted com.helpmetrade.app && xcrun simctl launch booted com.helpmetrade.app`. Sim screenshots: `xcrun simctl io booted screenshot /tmp/x.png`.
- **Native rebuild (new native module / widget):** `npx expo install <mod>` → if it adds a target, **`npx expo prebuild -p ios --clean`** (regenerates `ios/`) → **re-apply signing** (it's wiped): add `DEVELOPMENT_TEAM = 525TCRWC4Z; CODE_SIGN_STYLE = Automatic;` to EVERY target's build configs in `ios/HelpMeTrade.xcodeproj/project.pbxproj` (the **widget extension target gets none by default** — patch both app + widget) → `pod install --project-directory=ios` → `npx expo run:ios [--device <udid>]`. (~12–18 min with the widget extension.) `ios/` is gitignored.
- **Live Activity verify:** dev trigger in Settings → lock the sim (Device ▸ Lock / ⌘L) to see the `banner`. Check `xcrun simctl spawn booted log show --last 3m --predicate 'subsystem == "com.apple.ActivityKit"'` for token emission + errors. Tokens log as `com.helpmetrade.app::pushToStart` + per-activity.
- **Sim synthetic mouse** can't do finger long-press/drag — verify those on the device.
- **Safety (non-negotiable):** never touch `trading/` chokepoint; `POST /api/proposals` places real paper trades (per-action consent); never commit/echo secrets; end commits with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## 5. Key files
- `mobile/src/features/watchlist/{watchlist-screen,session,mini-day-chart}.tsx` — watchlist + shared sessionView.
- `mobile/src/app/symbol/[symbol].tsx` — ticker detail (chart, company panel, alerts). `app/chart/[symbol].tsx` — full-screen chart.
- `mobile/src/features/chart/` — candle-chart (WebView + vendored lightweight-charts), ranges, chart-pill.
- `mobile/widgets/trade-proposal.tsx` — Live Activity layout. `mobile/app.json` — expo-widgets plugin. `mobile/babel.config.js`.
- `mobile/src/components/nav/tab-bar.tsx` — IG teal-capsule pill. `mobile/src/components/pager/cockpit-pager.tsx` — WS merge + push.
- `mobile/src/components/ui/{text,index}.ts(x)` — `ScreenTitle`. `mobile/src/api/{endpoints,queries,types,use-market-ws}.ts`.
- `mobile/src/push/use-push.ts` — APNs handlers (price-alert deep-link). `mobile/src/state/alerts.tsx` — inbox store + dismissal.
