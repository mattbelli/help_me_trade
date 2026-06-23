# Brief: help_me_trade — native iOS phone app (React Native / Expo)

**For the RN builder agent. This is a PLANNING brief.** Read it end-to-end, read the referenced
backend code, then **enter plan mode** — do NOT write code yet. Produce a phased plan, surface
trade-offs, and ask the clarifying questions at the end first. This is a background build with **no
deadline and no shortcuts** — the bar is a *killer, polished, native-feeling app*, not an MVP. Quality
over speed at every step.

---

## 1. Why this app exists (read before designing anything)
help_me_trade is an **autonomous trading agent that works in the background** so the user — a dad with
a job — can step away. The phone app is **the single most important rung of the whole product**: the
mission doc calls **phone approval "the life-changing unlock."** The agent finds + sizes a trade, pings
the phone, and the user **taps Approve from the lock screen / Dynamic Island while away** — the trade
then executes through a hardened server chokepoint. Success = *how little he has to watch it and still
trust it.*

So this app is **first a supervision + approval cockpit** — it shows what the agent is doing and lets
the user approve/reject + review. It **also** supports **simple manual order entry by the user**
(chokepoint-routed; see Safety), but it is **not** a pro trading terminal, and the **AI agent never
places trades on its own** — it proposes, the human decides. Internalize that framing; it drives every
UX and safety decision below.

**Companion:** read `References/RN_UI_PLAYBOOK.md` (a research dossier on elite mobile UI craft + the
RN/Expo techniques to achieve it) alongside this brief — it's the "how to make it feel premium" evidence
base for your plan.

## 2. What already exists (the integration surface)
The backend is built and running on a home Linux server ("serv"), reachable over **Tailscale only** at
`https://serv.tail78aab8.ts.net:8000` (backend) — the app is pinned to that exact hostname (CORS + Host
allowlist), so the phone must be on the tailnet and use that name, never a raw IP. There is an existing
**web** frontend (Next.js) and a basic mobile web shell — study them for *inspiration and data shapes*,
but this is a fresh native app, not a port.

**Auth:** every request needs header `X-API-Token: <API_TOKEN>`. WebSocket auth via `?token=` query or
`x-api-token` header. Store the token in the iOS Keychain. (See `api/auth.py`.)

**The approval contract — the heart of the app** (`api/ai_routes.py`):
- `GET /api/proposals/pending` — pending agent proposals. Each carries: order details (symbol, side,
  qty, order_type, limit_price, account), a human **summary**, a **decision score** (calibrated win% /
  EV / confidence from `signals/decision_scorer.py`), `expires_at` (proposals expire — TTL ≤ 1h), and a
  **`payload_hash`**.
- `GET /api/proposals/{id}` — single proposal.
- `POST /api/proposals/{id}/approve` — **`payload_hash` is REQUIRED** (anti-stale-approval guard).
  Re-validates against live state and executes through the SOLE broker chokepoint
  (`trading/execution_service.submit` → `RiskEngine.validate`). Approval is atomic/idempotent server-side
  (concurrent double-approve executes at most once).
- `POST /api/proposals/{id}/reject`.
- (`POST /api/proposals` exists for the agent to CREATE proposals — the phone app should NOT create
  orders; it only approves/rejects/reviews.)

**Read-only data the app will render:**
- `GET /api/snapshot` — per-account snapshot(s) + an `all_accounts_summary` aggregate. Each account:
  cash, buying_power, portfolio_value, and `positions[]` (symbol, qty, side, avg_entry_price,
  current_price, market_value, unrealized_pl).
- `GET /api/account`, `GET /api/broker` — account/broker metadata.
- `GET /api/portfolio/history?range=1D|...` — equity curve series.
- `GET /api/watchlist`, `GET /api/watchlists`, `POST /api/watchlists` (create / set active),
  `POST /api/watchlist` (add/remove a symbol), `GET /api/watchlist/market` (live quotes for the list).
- `GET /health`.

**Realtime:** `WS /ws/market?token=…` broadcasts live events: `order_proposal` (new approval needed),
`fill`, `execution`, `exit_engine` (the agent sold), and `rule`/`alert`. This stream drives the **live
activity feed** in-app and is the in-app trigger to refresh pending approvals.

**Accounts in play:** Alpaca **paper** accounts (default, STRAT1, STRAT2) are where trading happens.
Schwab accounts are **live-funded and strictly read-only** — display positions if you want, but there is
NO trading affordance for any Schwab account, ever (see Safety).

## 3. The missing server slice — part of THIS work
Today proposals only broadcast over WebSocket to *connected* clients. For the unlock to work when the
app is **backgrounded or the phone is locked**, you must build push delivery:
- A **device-token registration** endpoint + a `device_tokens` table (register/unregister APNs +
  ActivityKit push tokens per user/device).
- A **push send** hooked into proposal creation (`trading/proposals.py::create_phone_proposal`): on a new
  proposal, send an **actionable APNs notification** AND start/update an **iOS Live Activity** so it
  surfaces in the **Dynamic Island** + lock screen with Approve/Reject. On approve/reject/expire/fill,
  **update/end** the Live Activity.
- Decide Expo Push Service vs direct APNs (ActivityKit Live Activities require their own push-to-start /
  per-activity update tokens — plan this carefully; it's the most novel part).
This server slice is in the **`api/` + `db/` + `trading/proposals.py`** lanes — coordinate with the serv
agent (see §9) since those are shared files.

## 4. UX vision & principles (founder's first thoughts — refine, don't dilute)
The founder's words, to be taken as direction:
- **"Widgets shouldn't sit in the page — they should BE the page."** No grid of floating cards. Each
  surface is **full-bleed and native**, one focused thing done beautifully, edge to edge.
- **Slide page to page** — fluid horizontal paging between the primary surfaces; gesture-driven, with
  motion that feels native (reanimated, 60fps, haptics).
- **Watchlist: hold-and-drag to reorder tickers up/down**, live quotes, swipe actions (remove, chart,
  add-to-other-list). Persist order to the backend.
- **Live activity + Dynamic Island notifications/prompts** — the agent's actions stream live, and a
  pending approval becomes a **Live Activity** (persistent lock-screen + Dynamic Island) you can act on
  without unlocking into the app.
- **Polish is the point.** Dark, premium, calm-but-alive. Considered typography, spacing, motion,
  empty/loading/error states, haptics, accessibility. No placeholder UI, no "good enough."

Take inspiration from the existing app's information architecture and theme, but elevate it.

## 5. Surfaces (swipeable, full-bleed; plan mode finalizes)
Horizontal paging with the **Home dashboard in the CENTER as the default landing surface** — swipe
left/right to the others.
- **Home (center, default)** — the "how's my agent doing / am I winning" glance: portfolio value +
  today's P&L, recent trades, and **insights** — both **AI insights** AND a clear **"are you beating the
  S&P 500?" alpha** readout, **broken out per strategy** (dip / earnings / …). Calm, glanceable, premium.
- **Approvals** — the hero interaction: pending proposals as rich swipeable cards (thesis/summary,
  calibrated win%/EV, entry/stop/targets, sizing, account, expiry countdown), big Approve/Reject +
  haptics. Also the Live Activity deep-link target.
- **Portfolio** — account value, equity curve (`/api/portfolio/history`), positions w/ live P&L, account
  switcher (default / STRAT1 / STRAT2 / aggregate).
- **Watchlist** — reorderable (hold-drag up/down) live-quote list; **tapping a ticker opens the
  Chart/Info detail page**. Persist order to the backend.
- **Chart / Info detail** — price chart + key info for a symbol; also the home for **lightweight manual
  order entry** (simple symbol/side/qty/limit — see Safety; chokepoint-routed, not a pro DOM).
- **Activity** — the live WS feed: proposals, approvals, fills, agent exits ("what it did while I was
  away").
- **Settings** — **activate/configure strategies** (mirror the web Strategies panel: enable, bind
  account, notional, exit_mode, universe, caps), account/connection status, app prefs, token/onboarding.

**Capability benchmark: thinkorswim** — that's the bar for *what it can do* (powerful), but with the
polish TOS lacks. **Feel/visual anchors: Apple Stocks, Apple Health, Apple Fitness**, plus the craft
leaders in the UI playbook dossier.

## 6. The killer feature: get pinged, approve safely from the lock screen
**Delivery decision (founder, refined): lead with a standard ACTIONABLE PUSH NOTIFICATION — NOT a
persistent Live Activity.** A Live Activity sits on screen for up to 12h and isn't wanted all market day
(clutter / battery). Demote it to **optional, short-lived, opt-in** (auto-ends on decision/expiry) — not
the primary channel. Primary flow: a proposal arrives → an **actionable push** (expandable on the lock
screen) with **Approve / Reject** → the user acts without opening the app if they choose.
- This is also **lower-risk to build**: actionable notifications via `expo-notifications`
  (`setNotificationCategoryAsync` with action buttons) are standard and stable, versus the churning Live
  Activity native-extension path. So ActivityKit/Live Activity is now *off the critical path* — a later,
  optional nicety, not the foundation.

**Anti-fat-finger / pocket-press is a HARD requirement (this becomes real money):**
- **Asymmetric actions.** **Reject** = low-friction (one tap; worst case you forgo a trade — never moves
  money the wrong way). **Approve** must be *deliberate*.
- **Approve uses `authenticationRequired`** (iOS `UNNotificationActionOptions` / expo-notifications
  `isAuthenticationRequired`) → **Face ID / passcode required before it runs.** You can approve from the
  lock screen, but only after Face ID — proves intent + defeats pocket presses. For **LIVE** money,
  additionally require an in-app **slide-to-confirm** (deliberate gesture, not a tap) or a second Face ID.
- Notification action buttons only surface on **expand/long-press**, not a glance — keep it that way.
- Approve always sends the current **`payload_hash`** and respects **`expires_at`** (server rejects
  stale/expired); surface server rejections honestly.

**Post-action state** (approved→executing→filled, or rejected/expired): reflect via a follow-up push or
in-app. If a short-lived opt-in Live Activity is used for a genuinely-pending proposal, end it cleanly on
decision/expiry (mind the 12h cap — see `RN_UI_PLAYBOOK.md`). With push-first, that cap rarely matters.

Must-haves to plan for: the actionable-push category/actions + the `authenticationRequired` gate +
keeping `payload_hash` fresh. (Live Activity / Dynamic Island is optional — see the playbook for its
current, churned tooling reality if you choose to add it later.)

**Dynamic Island — yes, used tastefully (founder: "would be dope for prompts/insights").** The DI is
*driven by Live Activities* (no separate DI API), so reconcile it with "not all day" by making activities
**short-lived & event-driven**: start one for a prompt / fill / "pending-approval" pill and **auto-end it
in seconds** — the 12h-persistence/battery concern is moot when you never let it linger. Use the DI as a
**glanceable indicator + deep-link** (e.g. "1 approval pending — tap"), with the actual Approve still
Face-ID-gated (a DI/AppIntent button wakes the app → Face ID → `/approve`). This brings back the native
widget-extension work (expo-widgets/Voltra + some Swift) but scoped to *ephemeral* activities and as a
**Phase 3+ nicety, not the foundation** — actionable push stays the primary, reliable approval channel.

**"Notification while in app" is a SEPARATE surface — build a custom in-app system.** Foreground system
banners feel clunky; elite apps render their own **in-app toast / HUD / banner** (some a faux "island
pill" near the notch for the DI aesthetic) — fully controllable, animated, tappable. Use it for in-app
prompts / insights / fills. It's **pure RN (no ActivityKit), cheap, Phase 1-2** — ~80% of the "dope"
factor with zero native complexity. Net mapping:
- **Locked / backgrounded, decision needed** → actionable **push** (Face-ID Approve). *[primary]*
- **Backgrounded, transient status** → short-lived **Live Activity / Dynamic Island** pill. *[optional, Phase 3+]*
- **In foreground, prompt/insight/fill** → **custom in-app toast/HUD** (optional faux-island pill). *[Phase 1-2]*

## 7. Safety — non-negotiable (this becomes real money)
- **Every order — manual OR an approved proposal — routes through the SAME server chokepoint.** Whether
  the user taps Approve on an agent proposal or places a manual order on the chart page, it goes through
  `trading/execution_service.submit` → `RiskEngine.validate` (kill-switch, paper-gated, **sells only
  reduce a held long — NO shorting**, caps). There must be **no path that bypasses the chokepoint**.
  Manual entry stays simple (symbol/side/qty/limit) — not a pro DOM.
- **The AI agent never gets raw trading tools** — unchanged boundary. The agent *proposes*; the *human*
  approves or places. Do not wire any agent-callable order tool into the app.
- **Schwab = read-only, always.** Never render any trading affordance for a Schwab (live-funded) account.
- **Sells only reduce a held long** (server enforces; the app must never imply shorting).
- **Approval integrity:** always send the current `payload_hash`; respect `expires_at` (show countdown,
  disable expired); surface server rejections honestly (kill-switch, risk, expiry).
- **No accidental approvals.** Approve is the only action that can move money → it must require deliberate
  confirmation (Face ID / `authenticationRequired`, + slide-to-confirm for live). Reject and all read
  surfaces stay low-friction. A pocket press / glance must NEVER execute a trade. (See §6.)
- **Honest numbers only:** render the calibrated win%/EV the server provides — never invent or round-trip
  fabricated confidence. If a field is missing, show it as unknown.
- Secure the API token (Keychain); the app is tailnet-only — never weaken Host/CORS pinning.

## 8. Tech constraints & recommendations (plan mode decides specifics)
- **Expo + EAS Build**, with a **dev client** (required for the Live Activity native module). React Native.
- **iOS-first** (Dynamic Island = iPhone 14 Pro+/iOS 16.1+; Live Activities iOS 16.1+). Android can be a
  later phase; plan the architecture so it's not painted into an iOS corner, but don't let it slow the
  iOS polish.
- Data layer: React Query (or equivalent) over the REST API + a resilient WS client for `/ws/market`.
- Gestures/motion: react-native-reanimated + react-native-gesture-handler; reorderable list via a
  draggable-flatlist approach. Paged navigation via Expo Router / react-navigation — your call, justify it.
- Push: APNs (via Expo Push or direct) + ActivityKit for Live Activities. This is the riskiest area —
  spike it early in the plan.

## 9. Coordination (three agents, three lanes)
Two other agents are active: **serv** (live strategy layer, registry, web frontend, `signals/`,
`db/strategies`, `trading/exit_engine`, `api/main` strategy bits) and **PC** (Schwab data + offline
research, `research/`, `brokers/schwab_auth.py`). To stay collision-free:
- **You own:** the new RN app (a new top-level dir, e.g. `mobile/`, or its own repo — propose which) +
  the **push-delivery server slice** (`device_tokens` table, registration endpoint, the APNs/ActivityKit
  send hooked into `trading/proposals.py::create_phone_proposal`).
- **Stay out of:** `signals/`, `db/strategies.py`, `trading/exit_engine.py`, `research/`, `brokers/`, and
  the web `frontend/`.
- The one genuinely shared file is `trading/proposals.py` (adding the push hook) — flag that change so
  serv reviews it; keep it additive and minimal.
- Work on a dedicated branch; nothing merges to `main` without review. Do all real testing against a paper
  account.

## 10. Non-goals (for now)
Android polish, heavy TA/charting tooling, a pro DOM/Level-2 ladder, multi-user auth, and any Schwab
trading. (Manual order entry **is** in scope, but kept simple on the chart page and chokepoint-routed.)
Keep the focus on the glanceable home + approval unlock + cockpit, done *excellently*.

## 11. What plan mode should produce
- A **phased plan** (suggested: ① project scaffold + auth + tailnet connectivity + read-only Portfolio/
  Watchlist/Activity reading real data; ② push delivery + actionable notifications end-to-end on paper;
  ③ Live Activity / Dynamic Island approve-reject; ④ polish pass: motion, haptics, states, reorder UX, a11y).
- The **navigation + state architecture**, the **push/ActivityKit approach** (with the riskiest spike
  called out), and the **server push-slice design**.
- A short **risk list** (ActivityKit-in-RN feasibility, background approve actions, push reliability over
  tailnet) with how each gets de-risked early.
- A crisp **quality bar / definition of done** per phase.

## 12. Open questions to resolve in plan mode (ask the founder first)
1. App location: a new `mobile/` dir in this repo, or a separate repo? (affects CI/secrets/EAS setup.)
2. Delivery is **decided**: actionable push first (Face-ID-gated Approve), Live Activity optional/later.
   Remaining call: for **LIVE** money, is Face ID (`authenticationRequired`) on Approve sufficient, or
   also require an in-app **slide-to-confirm**? (Paper can be lighter.)
3. iOS-only v1 acceptable (Android deferred)?
4. Push transport preference: Expo Push Service vs direct APNs?
5. Visual direction: match the existing web app's dark theme exactly, or take a fresh premium pass that
   shares only the brand feel?
6. Auth/onboarding: how does the phone get the API token (manual paste once / QR from the web app / other)?

Plan first. Build nothing until the plan is reviewed.
