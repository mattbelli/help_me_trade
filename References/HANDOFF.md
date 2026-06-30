# Handoff — "you are here" (2026-06-27)

*Bootstrap for a fresh session. Read this first, then `CLAUDE.md` (mission + working agreements, auto-loaded),
`AGENTS.md` (codebase map), and per-topic refs: `MULTI_ACCOUNT_PLAN.md`, `BUILD.md` (roadmap + dated history),
`PLATFORM_PARITY.md` (web vs phone tracker — keep current), `DECISION_ENGINE_RESEARCH.md`,
`SHORT_TRADING_FINDINGS.md`, `ARCHITECTURE.md`, `STARTUP.md`, `SERVER_SETUP.md`. Deep server specifics: the
`serv-runtime` memory.*
*Past full handoff snapshots live in `HANDOFF_ARCHIVE.md` (oldest first) — when you do a superseding rewrite of
this file, append the old full version there as a new dated section.*

---

## CURRENT FOCUS — Workspace consolidation + polish/new-features (2026-06-30)

> ▶ **LATEST (2026-06-30) — workspace polish + new features phase.** Consolidation Stages 1–3 are shipped + deployed
> (notifications widget; multi-page foundation; rules/research/performance widgets). Since then, a rapid polish burst
> (all shipped + deployed): **widget-picker fix** (cmdk filter was dropping registered widgets → plain-list render,
> `93ed48a`); **chat restyle** to the mobile look (`7b20197`) + **terminal mono font** on the copilot (`3fd1ed8`);
> **theme port** then a **full theme OVERHAUL via an ultracode multi-agent workflow** (`2f7fc4b`): 28 hardcoded-color
> offenders app-wide wired to theme vars (+ new `--scrim`); palettes redesigned (warm=warm grey, oled near-black
> panels on true black, light redone clean, carbon kept); the **chart now rethemes live** (MutationObserver on
> `data-theme` → `themeNonce` in `tv-light-chart` effects, no more "one theme behind"); and a **THEME BUILDER**
> shipped (`theme ▾ → + Custom theme`: clone→live-edit→save; customs persist via additive
> `/api/workspace/custom-themes` KV + localStorage + anti-FOUC script; `theme-editor-dialog.tsx`,
> `use-custom-theme-store.ts`, `custom-themes.ts`). Recon: `THEME_OVERHAUL_RECON.md`. Open follow-up: the darker
> `--scrim` dialog backdrop is a deliberate change — revert if disliked. **QUEUED, recon-done, ready to build:**
> (1) **save pages + workspaces** — founder-confirmed SNAPSHOT semantics; `References/SAVE_LAYOUTS_RECON.md`
> (additive KV `saved_pages`/`saved_workspaces`, page-template right-click menu + a console-bar workspace switcher,
> `workspaceDirty` confirm). (2) **performance breakup** — split the whole-page `performance` widget into 5 focused
> tiles; `References/PERF_SPLIT_RECON.md` (research needs the same trim). Still open: consolidation **Stage 4** (the
> Configure-your-AI setup page) + **Stage 5** (retire old routes). Briefs live in `tools/composer-bridge/briefs/`.
>
> ▶ **START HERE: workspace consolidation — read `References/CONSOLIDATION_PLAN.md` (5-stage plan, founder-agreed)
> + `CONSOLIDATION_RECON.md` (current-state map).** Workspace is the UI; collapse the old page-nav routes into
> widgets; multiple savable, hot-swappable **pages** (cap 5; "channels" = within-page symbol-link groups). Two
> editable default pages: a Configure-your-AI setup page (config widgets) + the live canvas.
> **Stage 1 ✅ DONE + deployed** (notifications inbox widget — `5bbd3a1`). **Stage 2 ✅ DONE + deployed + polished
> (`a5bffbb` + polish).** Multi-page foundation: store migrated to `pages: {id→{name,layout[]}}` + `activePageId`
> (non-breaking — existing layout auto-wraps into a default page; legacy `layout` row/endpoints untouched; backend
> `key='pages'` additive, 8 new tests); page switcher in the console cluster (tabs + add / right-click+double-click
> rename / delete, cap 5); `Alt+1–5` + `Alt+[ / ]` hot-swap (matched on `event.code` for Mac); `/add <widget>`
> direct-add. **Polish round (founder feedback after eyeball):** default page name "Page 1" (not "Canvas");
> **static page switching** (grid keyed by `activePageId` → remount, no morph/bleed); `/add` live-filters widgets as
> you type; chart symbol dropdown no longer auto-opens on mount; **positions widget columns sortable** (mirrors
> watchlist). Recon: `CONSOLIDATION_S2_RECON.md`.
> **Stage 3 ✅ built + gate-green + committed (deploy pending) — componentize old pages.** Three new widgets:
> `rules` (`<RulesSection showCards={false}>` — full rule manager), `research` (wraps `ResearchPage`, Suspense for
> its `useSearchParams`), `performance` (wraps `PerformancePage`). All are large-tile wrappers reusing the existing
> page components (no logic rewrite); the `/research` + `/performance` routes stay alive. Addable via
> `/add rules|research|performance`. Recon: `CONSOLIDATION_S3_RECON.md`. **DEFERRED from Stage 3:** the route
> redirects (Overview/memories/agents → /workspace) — held to cleanup so we don't strand the founder before widgets
> are placed; a launcher/modal refinement for research/performance is a later option. **NEXT = Stage 4: the
> Configure-your-AI setup page** (config widgets: settings/accounts/import + net-new onboarding/charter/goals).
>
> **Notifications supervision backbone — Phase 1 SHIPPED + deployed live (2026-06-29):** 1a (inbox foundation,
> `9ccbb99`) + 1b (all emits → `notify()`) + 1c (web unified consumer + server-backed bell). Detail below + in
> `NOTIFICATIONS_FOUNDATION.md`. Remaining: Phase 2 (Web Push + sound), click-through deep-links, Telegram/Discord.

The agent now runs many strategies (combined book **ARMED** + dip/earnings/session-node) across 3 accounts. The
next build is the **unified notifications + artifacts + scheduled-tasks foundation** so the founder can SEE +
TRUST what the agent does, on every device. **Full design (founder-agreed): `References/NOTIFICATIONS_FOUNDATION.md`
— read it** (principles, data model, the emit-site recon table, the absorb map, the 4 phases).

**Why:** emit sites are fragmented today — rule-triggers/fills are WEB-only (bell), RVOL/earnings/Schwab-health/
exit-prompts/auto-entry/rebalance are MOBILE-only (APNs), only price-alerts/proposals/exits hit both; read-state
lives in browser localStorage (never syncs). The unified `notify()` (persist → WS + APNs from one row, server-side
`read_at`) fixes all of it at the root.

### Status
- ✅ **Phase 1a — inbox foundation** — COMMITTED (`9ccbb99`), **inert + NOT deployed** (nothing calls `notify()`
  yet → zero behavior change; held so Phase 1 goes live as one unit). `db/notifications.py` (table: kind/title/
  body/severity + account_id/strategy_id/group_key + ref_type/ref_id/action_url + data_json + server-side
  read_at/dismissed_at; CRUD) + `signals/notify.py` (`notify()` persist-first then best-effort WS + APNs, both
  guarded; `notify_soon()`; `broadcast_notification_update`) + `api/notification_routes.py` (list+unread,
  unread-count, read, read-all, dismiss). 972 green.
- ✅ **Phase 1b — reroute all emit sites through `notify()`** (2026-06-29) — BUILT + reviewed + adversarially-
  reviewed clean, gate-green (ruff + 1010 pytest). **In the working tree, NOT committed (waits for 1c), NOT
  deployed.** Worklist + full detail: `References/PHASE_1B_EMIT_RECON.md` (16 logical events / 21 call sites,
  "★ LOCKED DECISIONS" block, post-build status). Governing rule: route ALL through `notify()` for persist+WS
  parity, **preserve each site's CURRENT mobile-push profile** (`push=True` iff it pushes today; proposal keeps
  its interactive `send_proposal_push` + Live Activity; exit `push=(exit_mode=="auto")`). Also **hardened
  `notify()` to never raise into its caller** (persist wrapped → `return {}`). Briefs: `tools/composer-bridge/
  briefs/phase1b-notify-{nontrading,trading}.md`. Adversarial Sonnet review of the 7 `trading/` sites: SAFE — all
  execution-safety invariants clean; one MINOR fixed (`proposals.py` `ref_id=str(...)`).
- ✅ **Phase 1c — frontend: unified `type:"notification"` consumer + server-backed inbox** (2026-06-29) — BUILT,
  typecheck + build green, **in the working tree, NOT committed, NOT deployed**. Recon + decisions:
  `References/PHASE_1C_FRONTEND_RECON.md`; brief: `tools/composer-bridge/briefs/phase1c-notification-frontend.md`.
  Collapsed the 2 notification-feeding consumers into ONE `frontend/lib/workspace/use-notification-stream.ts`
  (handles `type:"notification"` all kinds + `type:"notification_update"` for cross-tab read-sync), mounted
  app-wide; **deleted** `use-alert-stream.ts` + `use-execution-monitor.ts`. Store (`use-notification-store.ts`)
  rewritten: `hydrate()`→`GET /api/notifications`, **localStorage dropped (server = source of truth)**, optimistic
  `markRead`/`markAllRead`/`dismiss` → POST endpoints. `api.ts` gained `ServerNotification` + 4 fns. Bell/banner/
  mobile field renames (`ruleName→title`, `ts→created_at`, severity `info|success|warning|critical`).
  `use-price-alert-monitor.ts`: removed only its dead `type:"alert"` WS handler (kept client-side cross-detection
  + toast). **Locked decisions:** click = mark-read only (NO deep-link nav in 1c — `action_url` targets mostly
  don't exist in the workspace-only UI → 404; field carried for a later nav pass); `/api/alerts/recent` kept but
  unused (remove later). **NEXT: in-browser eyeball → deploy 1b+1c TOGETHER → update BUILD.md → commit.**
  (Mobile APNs push unaffected throughout.)
- ◻️ **Phase 2** web push (service worker + VAPID) + sound · **Phase 3** artifacts + Markdown viewer (extend
  `db.reports` with a `format` column) · **Phase 4** AI scheduled generative tasks (the "8am macro report"). Design doc has all.

**Principles (founder's "no compromise" bar):** server store = single source of truth; push = transport; ONE
emit path (`notify()`); Markdown-canonical artifacts (PDF export later, skip MDX); absorb existing reports/
Automations/bell/push (don't duplicate); design for the whole, build in phases.

---

## ALSO SHIPPED (2026-06-29) — Futures data, alert/price correctness, momentum fractional sizing (on `main`, deployed)
All gate-green (ruff + ~1007 pytest + typecheck), deployed live. The MOMENTUM changes touched `trading/`
(`risk_engine`, `fill_tracker`) and were adversarially reviewed clean; the futures/alert work did not touch `trading/`.
- ✅ **Futures on the main watchlist** — type `/ES` (`/`-prefixed) and it adds + persists with the slash; live
  price (bars + Schwab futures stream) + working sparkline; equity-only columns (RVOL/PE/EPS/div/mkt-cap/
  earnings) blank. Backend skips equity-only enrichment for `/`-symbols in `/api/watchlist/market`; frontend
  add-input already preserved the slash. **Data depth = Level-1 (top-of-book) + 1-min bars only — NO DOM/MBO/
  time-and-sales** (Schwab has no futures book service); a real ladder needs a CME-entitled vendor.
- ✅ **Globex session primitive** (`research/intraday_profile.py`): `globex_session_date`/`globex_bucket_index`/
  `segment_globex_sessions` (session = 18:00 ET prev day → 17:00 ET, daily 17:00–18:00 break excluded, 276
  5-min buckets). The shared correctness core for the two below. 9 boundary tests.
- ✅ **Futures RVOL** (`signals/volume_profile.py`): Globex expected-volume profile (parameterized `session=`
  "rth"|"globex"; equity path byte-identical). ⚠️ **caught a bug via live probe** — `compute_profile`/`rvol_now`
  grouped by **calendar date**, which splits a Globex session across midnight; fixed to group by
  `globex_session_date` (discriminating regression test). Futures profiles stored under `data/volume_profiles/
  futures/<sym>.json` (the bare `/ES` path resolved to FS-root + collided with equity ES). RVOL builds lazily on
  first watchlist poll.
- ✅ **Globex session nodes** (`signals/session_node_strategy.py`): `_fetch_rth_sessions` branches `/`-symbols
  onto `segment_globex_sessions` (10-day lookback) so `/ES` POC/VAH/VAL + prior-day H/L use the full ~23h
  session, not RTH-only (which was materially wrong — dropped ~14.5h of overnight).
- ✅ **Overnight/extended price alerts** (`brokers/market_stream.py` + `api/main.py`): `apply_extended` now drives
  `_check_price_alerts`, and a server-side `_poll_ext_quotes_for_alerts` (off-hours, in `_alert_symbol_loop`)
  evaluates alerts for ALL alert symbols even with no client open — so chart alerts fire overnight, any symbol.
- ⚠️ **Schwab price-source fix — was firing PHANTOM alerts** (`brokers/schwab_market_data.py`). The quote parser
  picked the block with the freshest `tradeTime`, but a STALE/empty `extended` block (bid/ask/volume all 0) can
  carry a recent tradeTime and beat the genuinely-traded `quote` block → a phantom price (e.g. 720 while real
  last was 737) flowed into the watchlist AND the new alerts → "SPY cross 731.27 — now 720" when SPY never went
  there. **Fix:** a block only counts as a live price if it has a non-zero bid/ask or volume. Two regression
  tests. (Founder caught this; my "real cross / thin pre-market" first read was wrong.)
- ⚠️ **Phantom alert part 2 — the STREAM feed** (`brokers/schwab_market_stream.py`). The parser fix corrected the
  REST/ext path, but alerts kept firing "SPY cross 731.27 — now 728.99": the equity stream's `LAST_PRICE` is the
  FROZEN regular-session last pre/post-market (728.99), and as it flickered against the live extended price it
  phantom-crossed. **Fix:** `_post_tick` only fires equity stream-alerts during RTH (`is_market_open()`); off-hours
  equity alerts come from the REST ext poll (true current price); FUTURES (`/`-prefixed, ~23h single live last)
  always fire. Regression test in `test_schwab_stream_handler.py`. Deployed.
- ✅ **Watchlist sparkline fix for futures** (`api/main.py`): `_session_intraday_closes` was RTH-filtered →
  empty for futures off-RTH → frontend drew no-data dashes (`intraday ?? sparkline` doesn't fall through on `[]`).
  Now futures use the latest Globex session's closes; frontend also falls back to the daily sparkline on empty.

### Momentum portfolio — fractional sizing + execution fixes (strat2, paper)
Founder checked in on momentum after its first rebalance (this morning, 86 buys); two real issues surfaced + fixed:
- ✅ **Stuck `partially_filled` orders** (`db/order_executions.py` + `trading/fill_tracker.py`, commit `5660d6a`).
  The reconcile pool queried `status='submitted'` only → an order stamped `partially_filled` left the pool and
  never re-polled the broker → stuck forever (the 86-order rebalance left 40 stuck despite being fully filled).
  Now `partially_filled` stays in the pool until terminal; side-effects gated on an actual status change.
  Adversarially reviewed: clean (`record_open_position` is idempotent — no dup managed rows).
- ✅ **Dollar-weighted FRACTIONAL sizing** (`signals/momentum.py` + `trading/risk_engine.py`, commit `3dac6b4`).
  Was whole-share floor + dropped every name priced > per-name budget → ~24% cash drag + missed NVDA/META/AVGO.
  Now buys = `per_name/price` fractional (ROUND_FLOOR 6dp), sells = the full held fraction. **RiskEngine permits
  fractional ONLY for `rebalance=True`** (momentum-exclusive — confirmed); every other path keeps the whole-share
  invariant. Chokepoint change adversarially reviewed: safe (gate contained, fractional sells degrade safely —
  reject not short). Takes effect automatically at each rebalance.
- ✅ **Forced one-off re-rebalance** (founder-approved override of the 2 monthly guards — `rebalance_runs`=complete
  + `last_rebalanced_month`; done in RTH). Deployed **$53.2k→$61.2k** (76%→87.5%): 18 fractional buys + 8 sells,
  0 rejected; the 40 stuck orders settled to `filled`. Remaining ~$8.8k cash = whole-share rounding on the 86
  ORIGINAL positions (a diff-rebalance buys entrants / sells dropouts but does NOT re-weight held names).
  **July's rebalance = the first fully-clean, fully-deployed fractional month** (treat June as a warmup/partial).
- ✅ **Position qty display capped at 3 decimals** (`frontend/lib/api.ts` `formatQty`, commit `769fe5f`) — fractional
  positions read clean (`0.871` not `0.871166`); whole shares unchanged. Applied to the workspace widget + dashboard table.
- ◻️ **OPEN — Area 4: strat2 has NO account-level equity snapshot** (`position_snapshots` exist but no
  `account_snapshots.portfolio_value`), so account-level P&L isn't visible for strat2. Recon was inconclusive
  (candidates: `compute_and_save_snapshot_analytics` skipped for non-default accounts at `api/main.py:245`;
  `_latest_run_id` not account-scoped → cross-account race in `db/sqlite.py`; or a `broker_external_id` mismatch
  skipping the save). **Needs its own focused pass.** Monitoring gap, NOT a trading-safety issue.
- ✅ **Personal status line on serv** (`~/.claude/statusline.js` + `~/.claude/settings.json`) — model + context% +
  5h/7d usage bars. Not in the repo (user-level config).

## ALSO SHIPPED THIS SESSION (2026-06-27, all on `main`)
- ✅ **Mobile copilot endpoint `POST /api/chat/stream`** (`6ff24db`+`962bba9`, LIVE + curl-verified). Single-pass
  `plan_tools → execute_tool → answer` reusing the web copilot's Python tool layer → **mobile inherits the
  perception layer** (ask anything about app data). Trade requests create a **PENDING** proposal via
  `create_phone_proposal` (Face-ID approve later) — **never executes** (`ExecutionService.submit` unreachable;
  adversarially reviewed; verified live: buy AAPL → proposal + pending row, zero executions). Mac handoff:
  `References/SERV_chat_stream_DONE.md` (flip `EXPO_PUBLIC_CHAT_STUB=0`). See [[mobile-chat-architecture]].
  Cross-platform chat history works via shared `chat_sessions` (`GET /api/chat/sessions` + `/{id}`); saved-only.
- ✅ **Data-correctness fixes** (deployed live): TSLA after-hours price used a STALE Schwab `extended` block →
  now `quote.lastPrice` (`brokers/schwab_market_data.py`); NVDA earnings label "Q1'27"→"Q1'26" (keep the
  company's fiscal quarter NUMBER, use the period's CALENDAR year — `_earnings_quarter_label` in `api/main.py`).
- ✅ **UI fixes** (deployed): workspace chart no longer jumps left on reload (gate RGL render on `colsMeasured`);
  Settings→Strategies enable toggle persists immediately (optimistic; safe — scan skips unbound strategies).

---

## Combined book — COMPLETE + **ARMED** (paper forward-test, 2026-06-27)

The "combined book" = 3 strategies sized 70/20/10: **residual-momentum** (alpha) + **steep-YC dip** +
**VIX-spike** (the two tactical sleeves = downside protection, drawdown ~24%→16.5%). All behind the sacred
chokepoint, **auto + paper-fenced**. Spec: `MOMENTUM_PORTFOLIO_SPEC.md` + `TACTICAL_SLEEVES_SPEC.md`.
**Founder ARMED it 2026-06-27** (momentum in its own dedicated account; the two sleeves can share — the bind API
hard-blocks momentum onto a shared account). NEXT: watch the first RTH rebalance + the one summary push, and the
first exit round-trips; **2–3 monthly paper cycles vs the backtest before any trust.**

### Status
- ✅ **Momentum (Phase 3, 70%)** — MERGED to `main` (Inc-1 data/ranking → Inc-2 read-only dispatch → Inc-3a
  residual sanity gate → Inc-3b rank→diff→**bulk-rebalance**). Gate-1 validated **bit-exact** vs the PC's golden
  targets. Seeded DISABLED + unbound. **Market-hours-gated** (month ticks at midnight ET → only fires in RTH).
  **Dedicated-account guard** in `api_update_strategy`. Two independent adversarial passes on the bulk path.
  Key memory: [[momentum-inc3-data-gate]] (residual thresholds +12 hard/+8 soft, dedicated-account precondition).
- ✅ **FRED/regime pipeline** — live. `db/regime` + `signals/regime` (`market_regime`, fail-closed
  `regime_is_fresh`) + a daily `_regime_refresh_loop`. `FRED_API_KEY` in `.env`. Verified term_spread +0.31 / VIX 18.9.
- ✅ **Phase 1 — `steepyc_dip` (20%)** — BUILT + reviewed + gate-green, committed (brief:
  `tools/composer-bridge/briefs/phase1-steepyc-dip.md`). The dip edge gated to `term_spread > 0.5`
  (fail-closed on stale/missing regime) + a 10-day fixed hold; seeded **disabled + unbound**, `exit_mode=auto`,
  notional $15k, kind `dip`. Mechanism: `yc_min_spread` param added to `DIP_ENTRY_PARAM_KEYS` so it round-trips
  into `ctx.params` but `_dip_check` strips it before `is_strategy_entry` and applies the regime gate after;
  `evaluate_dip_exit` now treats an explicit `None` stop/target/trail leg as DISABLED (→ pure max-holding exit).
  **Adversarial review done:** `dip_buy`/`dip_refined` are BYTE-IDENTICAL — they have no `yc` param (gate skipped)
  and real stop/target/trail numbers (every new `is not None` guard transparently true). The trail-`None` phase-2
  branch is unreachable for `steepyc_dip` (scale never fires when `first_target_pct` is None). 931 tests green,
  ruff clean. Honest framing in `strategy_stats.json`: tactical/high-beta, standalone alpha +0.47%/trade NOT
  significant — credit as tactical exposure, not alpha.
- ✅ **Phase 2 — `vix_spike` (10%)** — BUILT + reviewed + gate-green + **golden-fixture bit-exact**, committed.
  THE BOOK IS COMPLETE (momentum 70% + steepyc_dip 20% + vix_spike 10%). SPY-only "buy fear": new `vix_spike`
  kind (VIX>28 AND ≥8 of prior 12 closes <22, fail-closed on stale/short regime) + new pure `evaluate_vix_exit`
  (time-cap precedence: 60td OR VIX<20; HOLDS on missing VIX) wired into `exit_engine` (`_strategy_kind` + a
  `vix_spike` branch reading `market_regime` only when fresh). SPY-only universe via new `signals/universes/spy.json`
  (`universe="spy"`); re-entry blocked by the existing held-symbol skip. Seeded **disabled+unbound**, auto,
  $7.5k notional, per_trade. Pure entry seam `research/strategy_features.is_vix_spike_entry` is shared by the live
  scan AND the validation. **Golden fixtures (PC PR #27, merged):** `tests/test_tactical_fixtures.py` runs the
  REAL entry+exit logic over the PC's 9210-row FRED series and reproduces **all 14 VIX episodes + 5705 steep-YC
  days + 142 gate transitions bit-exact** (momentum-Gate-1 discipline). Confirmed the YC gate must use unrounded
  `DGS10−DGS2` (the rounded field missed 2 boundary days). Honest framing: thin pilot (n small, paper-forward is
  the gate). ⚠️ Live `holding_days` uses holiday-naive `_trading_days_until` (same as dip's 20d cap) → the 60d cap
  may fire a few td early; the VIX<20 reversion exit (9/14 episodes) is day-count-independent.
- ℹ️ **YC-gate flicker is EXPECTED (PC note):** the curve hovers right at +0.5 in 2026, so `steepyc_dip`'s gate
  flips OPEN/CLOSE within days. Not a bug (hard threshold near the live spread); daily gate check handles it. Add
  hysteresis later only if the flicker is operationally annoying. Fail-closed on stale FRED still applies.

### How to arm momentum (DELIBERATELY, when the founder says so)
Bind to a DEDICATED paper account (strat2 is reserved for it — dip/dip_refined/earnings/session-node are all on
strat1) + enable, **DURING MARKET HOURS** (after-hours = every order rejects on no-quote), watch the first
rebalance + the one summary push. **2–3 monthly paper cycles vs the backtest before any trust.** It's DISABLED
now — do NOT arm casually (arming = it rebalances on the next RTH scan). An accidental UI-arming (2026-06-26)
fired 80 paper orders into strat2 after-hours (79 correctly rejected) — cleaned up; it surfaced the market-hours
gate + the dedicated-account guard.

### CI / ops
`main` is green; `ci.yml` has `concurrency: cancel-in-progress`. ⚠️ Founder is **OUT of GitHub Actions minutes**
(CI paused until monthly reset — push fixes; they sit green until then). `serv-deploy.sh` does NOT rebuild the
frontend — `cd frontend && npm run build` FIRST (see [[serv-runtime]]).

---

## PRIOR FOCUS — Web UI consolidation + the supervision backbone (2026-06-25)

**Read `PLATFORM_PARITY.md` too** (web-vs-phone feature tracker; update it every change — agreed discipline).

### Locked product decisions (don't re-litigate)
- **The Workspace (`/workspace`, the draggable widget grid) is the home and the ONLY web UI going forward.**
  The old page-nav routes (`/trading`, `/charts`, `/rules`, etc.) are to be **deprecated**. Full/rich views
  (Automations, Performance, Research) should *launch from* the Workspace, not be a separate UI.
- **Charts: ONE chart.** Controls live in the **workspace frame toolbar** (`ChartHeaderControls` in
  `frontend/components/workspace/workspace-grid.tsx`): symbol + timeframe ▾ + Candles/Line ▾ + **Studies ▾**.
  `TvLightChart` renders chart-only in the workspace (`compact` prop hides its internal toolbar/overlay).
  ⚠️ `TvLightChart` STILL contains an old-UI internal toolbar (shadcn `Button`s) used only by the deprecated
  `/trading` + `/charts` pages — strip it when those pages go (it's the last old-UI styling in the chart path).
- **Studies menu** holds chart studies; only one so far: **"Session Nodes"** (prior-session volume-profile
  POC/VAH/VAL + prior-day H/L), toggled via the Studies dropdown → `config.showSessionNodes` →
  `TvLightChart`'s controlled `showLevels` prop → `/api/session-node/levels` → `createPriceLine`s. Future
  studies (MAs, S/R, drawings) join the same dropdown.

### Shipped today (2026-06-25, all on `main`, deployed live on serv)
CPU peg fixed (missing `account_snapshots(poll_run_id)` index — 101%→2%); **post-market/extended-hours charts**
(Schwab, `/api/bars` deduped — fixed a Schwab duplicate-candle bug that broke 1/5/15-min); **Session Nodes study**
+ chart overlay; **one full chart** w/ frame Studies dropdown; **Session-Node strategy promoted to the Strategies
cockpit** (armed, propose-only, 1-min scan loop); **morning levels report** (8am push, scheduled, Generate-now,
Automations); **Automations cockpit page** (`/automations`); chart-switch **bars cache**; **company-info last-
earnings fix** (Finnhub `/calendar/earnings` was row-capped → now chunked; serve report date not Schwab's stale
field); **Live Activity** trade-proposal lifecycle verified end-to-end on device.

**★ CRITICAL bug fixed (2026-06-25) — autonomous proposals required a browser open.** Found while debugging
"why didn't my vol-node strat prompt today?" (SPY tagged prior-session VAL/PDL support at 9:54 ET → a real long
trigger → zero proposals). Root cause: `trading/risk_engine._live_market_price()` read the live quote ONLY from
the in-process stream hub (`market_hub.quote_snapshot`), and that hub only carries a symbol while a **browser is
viewing it** or a **price alert** exists (the sole `ensure_symbols` caller). No browser on the symbol ⇒ no quote
⇒ `RiskEngine.validate` "no live quote" / sizing returns None ⇒ no proposal, **silently** (the scan claims-then-
`forget`s the dedup key, leaving no log/event). This gated EVERY market entry — session-node AND dip/earnings —
defeating "works while you step away." **Fix (mission-correct, single point):** `_live_market_price` is now async
and falls back to a fresh **REST quote** (`provider.aget_quotes` `regular_last`, then a today-dated 5-min bar
close) on a hub miss. **RTH-only** (off-hours → None, fail-closed, so no stale price slips the chokepoint) and
fail-soft. Adversarial Sonnet review (2 rounds): SAFE TO COMMIT; caught/fixed off-hours-stale + Alpaca-daily-bar
staleness. 850 tests green, live-verified (`_live_market_price("SPY")` returns a price on a hub miss).

### THE QUEUE — what to build next (priority order)
1. **Notifications / inbox (highest value — the supervision backbone; founder's biggest active pain).**
   Build ONE persistent notification store feeding both platforms. **Web bell** (`notification-bell.tsx`): entries
   must be **clickable with detail** (which rule, what level/price, a link to act) and **reports must register
   there** (they don't now — bell only shows proposals + a non-clickable "rule triggered"). **Phone**: a persistent
   **inbox** that holds reports/alerts until deleted (currently ephemeral push only). Backend likely needs a
   notifications table + an endpoint; the alert broadcast path is in `brokers/market_stream` + `send_alert_push`.
2. **Finish "Workspace is the only UI".** Deprecate/remove the old pages; **strip `TvLightChart`'s internal
   toolbar** (chart-only everywhere); add an in-Workspace **launcher** to the surviving full views (Automations,
   Performance, Research); **default-place the 6 orphaned widgets** (reports, agent-proposals, price-alerts, scans,
   memory, rvol-scanner) so nothing's hidden. (They're registered in `widget-registry.tsx` but off the default
   grid in `lib/workspace/layout-persistence.ts`.)
3. **Chart/Studies polish.** Founder to eyeball Session Nodes line rendering. Studies dropdown grows (drawings,
   MAs, S/R). If the chart's top toolbar wraps badly at small widget sizes, condense to icons (NOT a separate
   compact chart).
4. **HTML Session-Nodes report** — richer visual report (levels as an SVG ladder), beyond the plain push text.
5. **Earnings + RVOL alert surfaces** — backend endpoints so they appear in Automations → Alerts (currently
   backend-only, no UI).
6. **Auto-execute / guardrails** — the autonomy-ladder controls (act within hard caps + kill switch); the mission
   endgame; belongs in the Automations cockpit.
7. **Strategy descriptions + tunable strategies** (founder asked 2026-06-25; deferred for now — DO build).
   Two parts: **(a) descriptions in the UI** — a readable per-strategy blurb in each StrategyCard saying *what
   triggers it → what it does → how it exits*, with HONEST validation status (dip/dip_refined/earnings have
   validated/forward-test edges; **session_node is exploratory, propose-only, NO validated edge — never show a
   fabricated win-rate**). **(b) tunable strategies** — make the trigger/exit numbers adjustable, via an editable
   config file (yaml) AND/OR per-strat fields in the Settings UI. Recon done: 3/4 strats are already
   **param-driven** — thresholds live in `strategies.params_json`, read at runtime (dip/dip_refined: stop −3 /
   target +3 / trail 11 / oversold −5·3d / mkt −1.5·3d / far_support_min 5; earnings: blowout 5 / entry-lead 10td
   / exit-lead 1td / gap 40–90td); **`session_node` is HARDCODED** (rearm 2pts, max_per_node 2, max/day 6, VAH/VAL/
   PDH/PDL→POC in `research/session_node_signal.py`) → extract to params first. ⚠️ **Safety:** strategies are
   FROZEN/validated — editing a threshold changes the backtested edge, so any editable-params path MUST carry a
   "this de-validates the strategy, re-validate before trusting" warning (CLAUDE.md: learning layer may observe,
   never silently mutate validated logic). **Scope still undecided** (descriptions-only vs +read-only params vs
   +editable params). Key files: `db/strategies.py` (seed + `params_json`; would add a `description`/config source),
   `signals/strategy_kinds.py` (kind registry — natural home for shared descriptions), `api/strategy_routes.py`
   (GET/PUT `/api/strategies` — add `description`), `frontend/components/settings/settings-strategies-panel.tsx`
   (StrategyCard; render block ~just above Save), `signals/universes/*.json` (the only file-config precedent today).

### Key files
Chart: `frontend/components/trading/tv-light-chart.tsx`, `…/workspace/workspace-grid.tsx` (`ChartHeaderControls`),
`…/workspace/widgets/chart-widget.tsx`, `lib/workspace/layout-persistence.ts` (`WidgetInstanceConfig`).
Session nodes: `signals/session_node_strategy.py` (`compute_session_levels`), `GET /api/session-node/levels`.
Reports: `db/reports.py`, `signals/reports.py`, `_morning_levels_report_loop`/`_morning_report_window` in
`api/main.py`, `GET/POST /api/reports`. Automations: `frontend/components/automations/`, `/automations`.
Notifications: `frontend/components/workspace/notification-bell.tsx`.

### Working notes
- **Deploy:** `bash tools/serv-deploy.sh [backend|frontend|both]`. **REBUILD the frontend first**
  (`cd frontend && npm run build`) when frontend changed — serv-deploy only RESTARTS; serving a stale `.next`
  build is the recurring "where's my change?" bug.
- **Discoverability rule (founder pain):** when you add a widget/page, default-place it or tell the founder exactly
  where it is — don't leave it off-grid. Update `PLATFORM_PARITY.md` every change.
- **Coding:** Composer bridge (`tools/composer-bridge/dispatch.mjs --brief …`, one at a time) OR Sonnet subagents.
  Founder is considering dropping Cursor → use **Sonnet subagents for legwork, Opus for orchestration + `trading/`
  safety**. (Evaluated the `ponytail` plugin — skipped, redundant with `CLAUDE.md`.)
- **Safety:** `trading/` changes get an adversarial Sonnet review; session-node strat is **propose-only** (never
  auto-executes); Schwab stays read-only; LIVE execution hard-blocked.

---

## SHIPPED (2026-06-24) — Schwab data hub: RVOL + real-time streaming + extended-hours + auto re-auth
**Architecture locked (2026-06-24, see `data-vs-execution-architecture` memory):** consolidate **DATA on
Schwab** (volume, fundamentals, extended-hours, streaming) + **Finnhub** for the forward earnings date; keep
**EXECUTION decoupled on Alpaca paper** (Schwab read-only, never execute). Futures = its own later track.
The live-quote hub follows `MARKET_DATA_PROVIDER` (=schwab), NOT the execution broker.

**SHIPPED + DEPLOYED + LIVE on serv (2026-06-24, commits on `main`):**
- ✅ **Watchlist RVOL column** — `signals/volume_profile.py` (median 20-day, 78-bucket cumfrac S-curve;
  `rvol_now` warming/live/settled). `/api/watchlist/market` returns `rvol:{value,state}`. Web + **phone (RN
  `13ee660`)** render `2.3×` + color band (dim/yellow/amber/red), overnight dimmed. Validated live (CAT 1.78×,
  SPY 1.23×, NVDA 0.83×…).
- ✅ **RVOL for ANY stock** — lazy on-demand profile build on cache-miss (`_schedule_profile_build`, 10-min
  cooldown) + **nightly 986-symbol precompute** (S&P500 ∪ Russell1000 ∪ Nasdaq100, rate-limited ~100/min,
  skip <20h-fresh). Universe lists in `signals/universes/{russell1000,nasdaq100}.json`.
- ✅ **Schwab real-time streaming** (`brokers/schwab_market_stream.py` `SchwabMarketStreamHub`; refactored
  `market_stream.py` → `BaseMarketHub`). Level-one equities + futures (/ES routed to futures handler), dynamic
  subscribe/unsubscribe, 30s heartbeat + reconnect. Hub selected by `market_data_provider_name()`. **Fixed a
  bug:** `_client_from_token_basic` built Client with `token_metadata=None` → StreamClient.login crashed;
  now populates TokenMetadata (`f71f599`). Verified: `Schwab market stream connected`. Live ticks at market open.
- ✅ **Extended-hours + 24/5 OVERNIGHT pricing** — `SchwabMarketData.aget_quotes` (regular/extended/quote
  blocks); `/api/watchlist/market` stamps `market_session` (pre/regular/post/**overnight**/closed, weekday-aware)
  + `ext_price`/`ext_change_pct`. Overnight (Sun 20:00→Fri 20:00 ET) IS covered — Schwab's `extended` block is
  live overnight (verified: TSLA 382.74 +0.22%, etc.). ext change% uses Schwab's `postMarketPercentChange`
  (matches ToS). Web shows PRE/AFT/24h badges; RN brief updated for the same.
- ✅ **Schwab 2FA auto-codes** — Symantec VIP (Security token, Always-at-login) set up. Fresh VIP credential
  provisioned on the founder's Mac via `python-vipaccess`; **secret in `.env` `SCHWAB_VIP_SECRET`** (+
  `SCHWAB_VIP_CREDENTIAL_ID=SYMC38763863`, registered in Schwab). serv generates codes via `pyotp` — verified
  serv↔phone match. `SCHWAB_LOGIN_USERNAME`/`PASSWORD` also in `.env`.
- ✅ **Deploy unblocked:** `tools/serv-deploy.sh [backend|frontend|both]` + the founder added
  `Bash(bash tools/serv-deploy.sh:*)` to `.claude/settings.local.json`, so the agent CAN deploy now.

**ALSO SHIPPED + DEPLOYED this session (continued):**
- ✅ **Volume Scanner** — `GET /api/scanner/rvol?min_rvol=&limit=` ranks the precomputed universe by current
  RVOL (batched `aget_quotes` totalVolume ÷ expected-cum; RTH-only, empty off-hours; Schwab-only). Web
  "Volume Scanner" widget (`rvol-scanner`, polls 25s). (`6bc0eed`/`fc04618`)
- ✅ **RVOL-spike alerts + Schwab health monitoring** — `_rvol_alert_loop` (watchlist ≥`RVOL_ALERT_THRESHOLD`
  =3.0 during RTH, once/symbol/day) + `_schwab_health_loop` (token invalid / ≤1d-to-expiry, once/day) via new
  `signals.push.send_alert_push`. Env-gated, fail-soft. (`72526df`)
- ✅ **Fundamentals** — `/api/watchlist/market` adds `pe`/`eps`/`div_yield`/`market_cap`/`last_earnings`/
  `next_earnings` (Schwab `fundamental` block + earnings-calendar; lastEarningsDate is an ISO string, not
  epoch — `1e64d55`). Web **tap-to-expand panel** (chevron) shows them. Earnings refresh now also covers the
  watchlist so next_earnings populates (best-effort; Finnhub free-tier gaps). (`ddb7575`/`d871e83`/`7b7d265`)

**OPEN / IN-FLIGHT:**
- ✅ **Headless Schwab re-auth — WORKING end-to-end, cron installed (`9b19666`).** `scripts/schwab_reauth.py`
  runs under `xvfb-run -a` with `SCHWAB_REAUTH_HEADED=1` (Akamai blocks pure headless); selectors confirmed
  from a real run (Security Code field by label, #acceptTerms + #agree-modal-btn-, account-link #submit-btn,
  final "Done" by role). OTP is generated JUST before entry (waits for next 30s window if <8s left) so it's
  never stale. Crontab: every 6 days at 06:00 → `data/reauth.log`. Day-6 APNs fallback (`_schwab_health_loop`)
  pings the phone if a run ever fails silently.
  ⚠️ **CORRECTION (2026-06-29): the CRON was silently broken** — it ran `python -m scripts.schwab_reauth` but
  cron's CWD is `/home/mbelli`, so `scripts` wasn't importable (`No module named 'scripts'` every run). The
  SCRIPT works (manual runs from the project root minted real tokens — that's what kept the token alive). Fixed
  by prepending `cd /home/mbelli/dev/help_me_trade &&` to the cron line. ALSO learned the hard way: **never
  burst-request Schwab while debugging — Akamai WAF-blocks the serv IP (`403 Access Denied` on everything,
  incl. quotes); normal ~10-20s polling is fine, bursts are not.** See [[schwab-data-ops]].
- ✅ **Past-earnings performance — DONE as EPS beat/miss + surprise %** (`611ef2f`). Finnhub free
  `/calendar/earnings` has no history AND the EDGAR path needs an unbuilt CIK cache, so the 1-day *price
  reaction* was dropped (needs paid announce dates; `research/earnings.py::compute_reaction_pct` kept for
  later). Instead `signals/finnhub.afetch_eps_surprises` (free `/stock/earnings`) feeds
  `GET /api/fundamentals/earnings-history` → `{period,label "Q2'26",eps_actual,eps_estimate,surprise_pct}`
  most-recent-first; the web panel shows last-N quarters' beat/miss. Verified live (AAPL all beats).
- 🔧 **Mac/RN agent** to wire extended-hours (brief `References/RN_RVOL_BRIEF.md` updated with `overnight`);
  RVOL column already live on phone (`13ee660`). Relay sent re: ext-hours + reorder-persist via `PUT
  /api/watchlists/{id}` + intraday is RTH-only (no pre/post chart bars yet).
- Watchlist reorder syncs cross-platform via the shared server-side list (web persists; phone once it PUTs).

**SHIPPED THIS ROUND — the three Mac/RN serv-side items (relay doc:
`References/RN_STREAMING_ALERTS_RELAY.md`, contract lock-in test:
`tests/test_price_alert_dispatch.py`, 721 green, ruff clean, frontend typecheck clean):**
1. ✅ **Streaming actually works now — ROOT CAUSE was a dropped-tick bug, found by live probe.** The WS
   never streamed live ticks at all (phone always fell back to the 6-12s REST poll). Schwab's stream was
   connected + delivering ~1 tick/sec/symbol, but `_on_equity`/`_on_futures` read each tick's fields by the
   numeric enum `.value` (`LAST_PRICE.value` = 3) while **schwab-py relabels every content dict to the field
   MEMBER NAME** (`"LAST_PRICE"`) before the handler runs (`_Handler.label_message`). So `item.get(3)` was
   always `None` → `price is None → continue` → **zero broadcasts, ever**. Symbol extraction used the literal
   `"key"` field, so it looked "connected" but was silent. **Fix:** read by `.name` (constants `_EQ`/`_FUT`).
   Verified against the LIVE stream — AAPL/NVDA/SPY populate real prices (297.38/+1.05%, 201.36, 739.52).
   Regression lock: `tests/test_schwab_stream_handler.py` (feeds the labeled shape, asserts QuoteState).
   ⚠️ Schwab allows only ONE stream session per login — a local probe transiently kicks the prod stream
   (it reconnects in ~2s); don't run a second streamer while debugging unless you mean to.
   Also: streaming frame now carries ext fields — `QuoteState` + `_quote_payload` include
   `ext_price`/`ext_change_pct`/`market_session`; `BaseMarketHub.apply_extended(sym, ...)` lets the REST
   `/api/watchlist/market` poll write ext through to the hub, so every WS broadcast rides with the same
   pre/post/overnight values the phone headlines. Hardened the sync handler too: replaced deprecated
   `asyncio.get_event_loop().create_task(...)` with `get_running_loop()` + a `_inflight_ticks` hard-ref set
   so the GC can't cancel `_broadcast_quote` mid-run.
2. ✅ **Price alerts now broadcast FLAT** (matching the rule-alert shape). The nested `alert:{...}` is gone;
   `_check_price_alerts` routes through `broadcast_alert` with `{source:"price_alert", symbol, alert_id,
   target_price, trigger_price, direction, note}` + the auto-stamped `id`/`ts`.
3. ✅ **APNs push on price-alert trigger.** Same `_check_price_alerts` path also calls
   `signals.push.send_alert_push("Price alert", body)` — fail-soft, never raises — so a closed phone still
   gets notified.

**ALSO THIS ROUND — WS broadcast throttle (10 Hz / symbol / client).** Schwab fires 30–100 ticks/sec on hot
names; phone battery would suffer. `BROADCAST_THROTTLE_MS = 100`: first tick of a window fires immediately,
further ticks inside the window collapse into a single trailing send that re-reads state (latest price always
wins; never stale). Alerts unthrottled. Tunable single constant; lock-in tests
`test_broadcast_throttle_coalesces_hot_ticks` + `test_broadcast_throttle_idle_ticks_pass_through`.

Deferred (still): `/api/bars`+`1Hour` aggregation to the provider; extended *intraday bars* (RTH-only now so
no pre/post chart segments yet); watchlist reorder cross-platform sync (phone-side wiring; backend already
supports `PUT /api/watchlists/{id}` with a `symbols` array).

> ⚠️ **SCHWAB AUTH BUG — recorded, fixed, but know it (could resurface on dep updates):** schwab-py builds its
> authlib OAuth2 session **without** setting `token_endpoint_auth_method`, so authlib defaults to
> `client_secret_post` (creds in the body) — which Schwab's token endpoint **rejects with `invalid_client`**.
> Schwab requires **HTTP Basic** (`client_secret_basic`), and authlib binds the auth method at construction
> (setting the attribute post-hoc does NOT work). Fix lives in `brokers/schwab_auth.py::_client_from_token_basic`
> (builds the session with `client_secret_basic` + a token-persistence writer). **This fixed Schwab auth
> APP-WIDE — the existing account-sync `SchwabAdapter` was broken by the same bug.** Second, separate gotcha:
> `config.settings.schwab_config()` reads creds **only from `data/settings.json`, never `.env`** — that file
> had `.env.example` placeholder Schwab creds shadowing the real `.env` values (corrected on serv; gitignored).

## SHIPPED & MONITORING — make dip + earnings live ROUND-TRIPS: the per-strategy EXIT MODULE (Phase D) + EARNINGS sleeve (Phase E)
Entries already work (phone-approved through the chokepoint). What's missing is **exits** — and the earnings
strategy *can't* go live without them (its edge IS selling ~1 day before the next earnings). Settled design
(founder): **exits are per-strategy**, governed by each strategy's existing **`exit_mode`** field
(`auto` = agent sells through the chokepoint; `propose` = agent pings the phone to approve the sell), and the
**user is always notified** either way.

**Build status (each slice committed + gate-green; adversarially reviewed where it touches `trading/`):**
- ✅ **E1 — earnings data layer** (`signals/finnhub.py` client, `db/earnings_calendar.py` table,
  `signals/earnings_refresh.py`). `FINNHUB_API_KEY` is in `.env` and **the live feed works** (verified: 1500
  upcoming-earnings rows/45d; NVDA next = 2026-08-25). ⚠️ Free-tier coverage looks partial (AAPL/MSFT/TSLA
  didn't appear at 75d) — monitor; fewer earnings setups to start. EDGAR (`research/earnings.py`) is past-only;
  Finnhub gives the FORWARD date EDGAR can't.
- ✅ **E2 — live predicate** `is_earnings_drift_entry` in `research/strategy_features.py`. LONG-only (prior
  earnings blowout-UP ≥5% → buy ~10 td before next earnings, exit ~1 td before). Forward date via holiday-naive
  business-day count (documented approx). **DORMANT** — not yet called by the scan.
- ✅ **D1 — managed-position ledger** (`db/managed_positions.py` + `trading/position_ledger.py`, hooked into
  `trading/fill_tracker.reconcile_open_executions`). When a strategy BUY fills, records an OPEN row tagging the
  position with its owning strategy + an `exit_mode`/`params` snapshot. **RECORDS ONLY** — no selling; chokepoint
  untouched. `managed_positions.qty` is ADVISORY (float-derived) — the exit engine MUST take the authoritative
  sell qty from the live broker position, never that column.
- ✅ **D2 — per-strategy exit evaluators** (`signals/exit_evaluators.py`, pure fns + `tests/test_exit_evaluators.py`,
  14 tests). `evaluate_dip_exit` (−3% stop / +3% sell-half→breakeven / ~11% trail / 20d cap) +
  `evaluate_earnings_exit` (sell within `exit_lead_td` td of next earnings + stale-calendar safety cap). Both
  return an `ExitDecision(action: hold|scale|exit, reason, exit_fraction, new_high_water_mark,
  new_first_target_hit, detail)`. **`exit_fraction` is relative to the LIVE qty** (scale=0.5, exit=1.0) — D3
  multiplies it by the authoritative broker position, never `managed_positions.qty`. Faithful single-cycle
  translation of the validated `research/strategy_sim.simulate_exit` path (time-cap precedence, stop-before-
  target, phase-2 stop-check-then-advance-HWM). Pure: floats not Decimal, no `trading/`/`db/`/broker imports
  (only reuses `_trading_days_until`); the Decimal seam stays at D3's chokepoint. **No execution.**
- ✅ **D3 — exit engine + `exit_mode` dispatch (the FIRST auto-sell) + notify** (`trading/exit_engine.py` +
  `tests/test_exit_engine.py`, 13 tests). `run_exit_engine()` walks open managed rows per account, reads the
  LIVE snapshot basis (never `managed_positions.qty`), evaluates via the D2 fns by strategy kind, persists
  HWM/first-target, **closes rows whose live position is gone** (sells aren't reconciled into the ledger by
  `fill_tracker`, so the engine owns closure), and on scale/exit dispatches whole-share sell-to-close per LIVE
  `exit_mode`: `auto` → `ExecutionService.submit()` (sole broker path; kill-switch + RiskEngine + paper-gated;
  reduces a held long only, NO shorting), `propose` → `create_phone_proposal()`. User always notified
  (`exit_engine` alert). **Double-sell defense:** a per-row `pending_exit` marker resolved against live
  `get_execution`/`get_proposal` status, a snapshot-lag guard (never re-fire until a snapshot captured AFTER the
  last attempt exists), and a 300s throttle; fail-closed everywhere (no snapshot / no position / unknown kind /
  qty<1 / risk-reject ⇒ do nothing). **Heavy adversarial Sonnet review done** (2 rounds): caught + fixed 2
  CRITICAL (propose status string never matched → proposal-flood/double-approve; closure wrongly closing a
  freshly-filled BUY → orphaned position), 2 MAJOR (no marker on non-risk submit error → retry-storm; throttle/
  snapshot-staleness double-sell window), and a wrong-account-snapshot mass-close guard. Re-review: all FIXED,
  no regressions. **NOT yet armed** — see LIVE STATE.
- ✅ **D4 — schedule the exit engine + deploy.** `run_exit_engine()` runs once per `_poll_loop` cycle in
  `api/main.py::_poll_once`, AFTER all per-account snapshots are saved + fills reconciled (try/except-isolated so
  an exit hiccup can't break polling). Deployed onto D1→D4. **`dip_refined` exit_mode set to `propose`** (every
  exit pings the phone to Approve/Reject — the safe default; flip to `auto` when trusted). 0 open managed
  positions at deploy, so the engine is inert until approved `dip_refined` entries fill.
- ✅ **E3 — earnings_drift registered + strategy-kind registry + daily refresh.** New `signals/strategy_kinds.py`
  is a **kind registry** (`STRATEGY_KINDS`: `kind → {entry_param_keys, check_entry, build_rationale}`); the scan
  (`signals/strategy_scan.py::_run_strategy`) now dispatches entry through it instead of hardcoding the dip
  predicate — adding a future kind = write a point-in-time predicate + one registry line (dip behavior
  unchanged). `earnings_drift` seeded **disabled, unbound, exit_mode `propose`** (`db/strategies.py`;
  also fixed a latent seed early-return that skipped any new built-in once the dip rows existed, and added an
  `exit_mode` arg to `_insert_strategy`). Daily earnings-refresh loop in `api/main.py` (`_earnings_refresh_loop`,
  boot + 24h, scoped to ENABLED earnings strategies' universes via `arefresh_earnings_calendar`). Earnings EXIT
  was already live (D2/D3) — untouched. 13 new tests; full suite 623 green.

**Combiner (pulled into `main`, commit 3b82e84) — research only, NOT live.** `research/combined.py` +
`overfit.py` (Deflated Sharpe / PBO) + `earnings.py`/`earnings_strategy.py` + deps. Founder's validated run
(dip+earnings): COMBINED **+23.2% CAGR full window, beats SPY in all 4 windows, PBO 0.18** (not overfit). There
is NO single "combined" predicate — live "combining" = run dip + earnings on ONE account; the per-strategy caps
+ overlap produce the portfolio effect. Reproduce:
`python -m research.combined --source dip:data/research/strategy_labeled.db:0.20 --source earn:data/research/earnings_labeled.db:0.0`

## LIVE STATE right now (serv; deploy = `kill <MainPID>` → systemd reloads working tree)
- **Backend deployed onto D1→D4** (exit module live). The exit engine runs every poll cycle; with 0 open managed
  positions it's inert until approved `dip_refined` entries fill, then it manages them in `propose` mode.
- THREE Alpaca PAPER accounts polling per-account: default `acct_fc78907f4bda` ("Alpaca", ~$104k), STRAT1
  `acct_alpaca_strat1` (~$100k), STRAT2 `acct_alpaca_strat2` (~$100k, ext `dfd08049…`). No errors.
- **⚠️ BOTH sleeves are LIVE (paper forward-test), both `exit_mode=propose` (phone-approved entries + exits;
  nothing auto-sells):**
  - `dip_refined` — ENABLED, $10k notional, **STRAT1**, sp500, **UNCAPPED**. `dip_buy` disabled.
  - `earnings_drift` — ENABLED, **$500 notional**, **STRAT2**, sp500 (founder bound + enabled it). Expect
    FEW setups (Finnhub free-tier coverage + dates only confirmed ~2-4 wks out). Note the small $500
    notional vs dip's $10k — founder may want to size it up to match the combined-backtest intent.
  - **Still no caps** on either — broad `sp500` + uncapped risks a cluster-day proposal flood; founder may
    want ~10 pos / ~$60–70k caps (`max_concurrent_positions` / `max_deployed_usd`).

## NEXT (in order)
1. **Both sleeves are armed (paper/propose) — watch the first real round-trips.** dip_refined (STRAT1) +
   earnings_drift (STRAT2) are live; the daily earnings-refresh loop populates the Finnhub calendar for the
   enabled earnings universe. Founder may want to size earnings up from $500 + set caps on both.
2. **Watch the first real exit round-trip** (dip or earnings): managed row recorded (D1) → on a trigger a phone
   exit proposal fires (propose) → row closes after the approved sell fills / position goes flat. Then consider
   flipping a strategy to `auto`.
3. **Caps on `dip_refined`** (founder call): broad `sp500` + uncapped can flood proposals on a cluster day —
   set `max_concurrent_positions` / `max_deployed_usd` before/alongside heavier use.

## Phone app (RN) — active parallel thread
Native iOS app (the "phone-approval unlock") is being built on a **MacBook** in a **`mobile/` dir
(monorepo)** by a dedicated agent. Specs: `References/RN_APP_BRIEF.md` (what/why + integration contract;
push-first delivery, Face-ID-gated approve, tasteful ephemeral Dynamic Island, manual orders
chokepoint-routed) + `References/RN_UI_PLAYBOOK.md` (adversarially-verified UI/RN dossier). **Server
push-delivery is LIVE** (`db/device_tokens.py`, `api/device_routes.py` → `POST /api/devices`,
`signals/push.py`, `push_config()`, fail-safe hook in `create_phone_proposal`). **Direct token-based APNs
sender (`_send_apns`, ES256 JWT via `cryptography` + HTTP/2 via `httpx`/`h2`) merged to main** (the RN
agent built it in its lane; serv reviewed line-by-line + verified: deps present, real `.p8` signs a valid
JWT, fail-safe, no key/token logged). `.env`: `PUSH_ENABLED=1`, `PUSH_PROVIDER=apns`, APNs creds,
`.p8` at gitignored `secrets/apns.p8`, `APNS_USE_SANDBOX=1` (flip to 0 for a prod/TestFlight build). **1
APNs device registered** → new proposals now fire a real push. Lanes: RN agent owns `mobile/` + the push
slice; serv stays out of `mobile/`. Live-Activity (real Dynamic Island) push is Phase 3+. **Follow-up:** a
`_send_apns` unit test (mock httpx; assert request shape + fail-safe) — path verified live but untested.

## Planned — execution enhancements (NOT implemented; design captured)
Current execution is **market orders, regular-hours only** (entries `strategy_scan.py`, exits `exit_engine.py` both
`order_type="market"`; RiskEngine requires a live quote and the free IEX feed is RTH-only → off-hours rejected,
gap-down stops fill at the gapped market price on the first post-open poll cycle). Two related, deferred builds:
- **Slippage-capped limit orders ("stop with slippage cap").** Our stops are synthetic (engine evaluates →
  submits an order), so this = on trigger submit a **marketable limit** at `current_price ∓ cap` instead of
  market. Per-strategy `slippage_cap_pct`. **Exits:** capped-limit then **widen-to-market fallback after N poll
  cycles** if unfilled (guarantee the exit; the real scope is the cancel/escalate order-lifecycle handling +
  the in-flight guard interaction). **Entries:** capped-limit, no fallback (a miss just skips the trade).
  RiskEngine already supports limit sells during RTH (no change). Caps *extra* slippage beyond a gap, NOT the
  gap itself. `trading/`-lane → mandatory adversarial review. Likely *improves* backtest fidelity (fills near
  the quote).
- **Extended-hours (pre/post-market) execution.** Builds on the above: needs `extended_hours=true` on a limit
  order (new field on `PlaceOrderRequest` + Alpaca payload) **and** an extended-hours quote source (IEX free is
  RTH-only → Alpaca SIP paid, or Schwab data read-only) to satisfy the live-quote gate. ⚠️ dip/earnings are
  daily-bar/close-validated, so trading them in extended hours **deviates from the validated edge → needs
  re-validation**, not a free toggle. Most relevant for the intraday profile-MR strat + gap stop-protection.
- (Heavier future option: rest a **native stop-limit at the broker** so it triggers in real-time/at the open
  without waiting for the ~20s poll — bigger order-lifecycle change.)
2. **E3** register `earnings_drift` + scan-dispatch by strategy kind + daily earnings refresh loop.
3. Then both dip + earnings are live round-trips governed by `exit_mode`; arm on STRAT1 (ideally add
   `APCA_*_STRAT2` so refined-dip / earnings get their own forward-test accounts for clean attribution).
4. Deferred UI follow-ups: equity-curve + agent-proposals widgets aren't account-scoped yet (positions +
   open-orders now ARE — they follow the toolbar account selector). Account-summary widget still duplicates the
   new toolbar strip; consider pulling it from the default desktop grid (keep for mobile).

## ⚠️ GOTCHAS — read these
- **Tests must NEVER touch the real `data/trading.db`.** `tests/conftest.py` forces `TRADING_DB_PATH` to a temp
  DB at import. Still — after any Composer round, verify the real DB wasn't mutated (`strategies`,
  `trade_accounts`, `paper_order_proposals`, `order_executions`, `managed_positions`) before trusting a deploy.
- **The chokepoint is sacred.** `trading/execution_service.submit()` is the ONLY broker path; `RiskEngine.validate()`
  gates every order (kill-switch-first, deny-by-default, per-account capability hard-block). Sells only reduce a
  held long — NO shorting. Decimal-only for money in `trading/`. **Adversarially review (Sonnet) ANY change to
  `trading/` or `db/order_executions` before commit** — it keeps catching real safety bugs. D3 is exactly this.
- **Strategies are isolated, independently toggleable, FROZEN/versioned modules**; the learning layer may OBSERVE
  outcomes but must NEVER silently mutate a strategy's validated logic. Registry is config/data only.
- **Deploy w/o sudo:** `kill <MainPID>` (`systemctl show hmt-backend -p MainPID --value`); systemd `Restart=always`
  relaunches into the working tree; boot runs migrations + `seed_default_strategies` idempotently. **Frontend is
  a production Next.js build** (`hmt-frontend.service`) — UI changes need `cd frontend && npm run build` then
  restart its MainPID. Verify: HTTP 401 (backend up, auth-gated) / 200 (frontend), accounts saving snapshots.
- **Dashboard data is account-aware now:** `/api/snapshot` returns one snapshot PER account + an
  `all_accounts_summary` aggregate; top-level is pinned to the DEFAULT account (killed the flicker). A shared
  `useDashboardPrefs` store (account selector + privacy toggle) drives the toolbar + positions/open-orders.
- **Free IEX feed = regular hours only**; options orders are limit-only. **LIVE trading hard-blocked** server-side
  (`execution_mode` default `dry_run`; flip to `paper` in Settings; LIVE needs the typed double-gate, not built).
- **Access:** backend `0.0.0.0:8000` + frontend `:3001` on serv, tailnet only (`serv.tail78aab8.ts.net`). The app
  is pinned to that exact hostname (CORS + Host allowlist + baked `NEXT_PUBLIC_API_URL`) — accessing by raw
  tailnet IP fails CORS; use the `.ts.net` name. Never port-forward to the public internet.

- **Schwab auth needs `client_secret_basic`** (schwab-py defaults to `client_secret_post` → `invalid_client`)
  and **creds come from `data/settings.json`, NOT `.env`** (`schwab_config()`). Both fixed — see the boxed
  note under CURRENT FOCUS. Schwab is **READ-ONLY** (data + ledger sync; NEVER execute against it). The
  market-data path uses `get_market_data_provider()` (`MARKET_DATA_PROVIDER` flag, default `alpaca`), separate
  from the execution broker. **serv's Schwab token expires ~2026-06-29** (copied from PC) — re-auth before then.

## The validated edges
- **Dip-Buy (triple-validated, Norgate survivorship-free):** healthy(>SMA200) + oversold(−5%/3d) +
  market-down(SPY −1.5%/3d); exit −3% / +3% sell-half→breakeven / trail ~10–12% / 20d cap. 21,454 setups, ~57%
  win, honest net ≈ +0.4–0.5%/trade (alpha-vs-beta ✓, OOS ✓, block-bootstrap ✓). Predicate reused VERBATIM live.
- **Refined-dip (`dip_refined`, far_support_min=5.0):** dip + "≥5% above nearest support" (`research/levels.py`).
  ~doubled RECENT excess vs SPY (+1.6%→+3%/trade) but **NOT** block-bootstrap-validated — forward-test candidate,
  UI says so.
- **Earnings-drift (combiner's "Sharpe king"):** pre-earnings drift after a blowout-up; backtest Sharpe 1.47–1.85.
  Combined dip+earnings PBO 0.18. Honest caveats: favorable regime, gross-ish costs, naked-dip drawdown —
  paper-forward is the real gate. **Short mirror tested → NO edge → no-short invariant stays.**

## Stable foundation (context)
- **Decision Engine** (#1 value): PERCEIVE→REASON→DECIDE → calibrated win%/EV/CI on every proposal
  (`signals/decision_scorer.py`). Honest by construction. R3 phone-approval shipped end-to-end.
- **serv** = dev + runtime home + sole writer of `data/trading.db` (nightly backup → `mattpc`). Do all work on serv.
- Deprioritized threads: Layer-A per-account risk profiles; RN phone app (Expo/EAS; lock-screen Approve/Reject =
  the unlock); Watchlist v3 (`serv-parked-watchlist-v2` memory); R1 backfill (Schwab realized-G/L + futures PDF
  import); review nits — `cluster_mean==0` guard in `research/levels.py`, thread `db_path` into
  `get_broker_for_account`, M-2 migration-stamp hardening.

## Working agreements (also in `CLAUDE.md` + auto-loaded memory)
Build via **Composer** (`node tools/composer-bridge/dispatch.mjs --brief <file>`, ONE run at a time) — not
hand-edits (trivial/surgical excepted). Recon/review via **Sonnet** subagents. **Adversarially review
safety-critical code (execution/risk) before commit.** Review the real `git diff` + run the gate **yourself**.
Founder is **cost-conscious about the Opus main loop** — delegate heavy work. Cadence: iterate → "happy" →
update `BUILD.md`/`HANDOFF.md` → commit + push. Pre-push: `ruff check .` + `python -m pytest -q` +
`cd frontend && npm run typecheck`. Python is `uv`: `.venv/bin/python`, `.venv/bin/ruff`.
