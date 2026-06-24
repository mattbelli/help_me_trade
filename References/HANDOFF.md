# Handoff — "you are here" (2026-06-23, late)

*Bootstrap for a fresh session. Read this first, then `CLAUDE.md` (mission + working agreements, auto-loaded),
`AGENTS.md` (codebase map), and per-topic refs: `MULTI_ACCOUNT_PLAN.md`, `BUILD.md` (roadmap + dated history),
`DECISION_ENGINE_RESEARCH.md`, `SHORT_TRADING_FINDINGS.md`, `NORGATE_PULL.md`, `ARCHITECTURE.md`, `STARTUP.md`,
`SERVER_SETUP.md`. Deep server specifics: the `serv-runtime` memory.*
*Past full handoff snapshots live in `HANDOFF_ARCHIVE.md` (oldest first) — when you do a superseding rewrite of
this file, append the old full version there as a new dated section.*

---

## CURRENT FOCUS — Schwab data hub: RVOL + real-time streaming + extended-hours + auto re-auth
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

**OPEN / IN-FLIGHT:**
- 🔧 **Headless re-auth NOT yet tested.** `scripts/schwab_reauth.py` written (Playwright drives **system
  chromium `/snap/bin/chromium`** — Playwright's bundled chromium has no ubuntu-26.04 build; `--no-sandbox`;
  enters the `pyotp` VIP code). Selectors are best-guess constants at the top → **first run will need tuning**
  (founder runs it; failures save a screenshot to `/tmp`; early field-not-found = no login attempt = no lockout).
  Cron line (every 6 days) + day-6 APNs fallback documented in the agent report. The 7-day cliff is the reason.
- 🔧 **Scanner / fundamentals IN PROGRESS** — `GET /api/scanner/rvol` (market-wide unusual-volume, ranks the
  precomputed universe via batched `aget_quotes` totalVolume / expected-cum) being built; then RVOL-spike APNs
  alerts + data-health/token-expiry monitoring; then fundamentals (Schwab P/E·mktcap·last-earnings + Finnhub
  next-date). RN extended-hours wiring is the Mac agent's lane (brief `References/RN_RVOL_BRIEF.md`).
- Watchlist reorder syncs cross-platform automatically (single server-side list; web persists via
  `PUT /api/watchlists/{id}`; phone will once the Mac agent wires the same PUT).

**NEXT:** finish scanner → alerts + monitoring → fundamentals; founder tests `schwab_reauth.py` (tune selectors);
deferred: `/api/bars`+`1Hour` aggregation to the provider; extended *intraday bars* (currently RTH-only, so no
pre/post chart segments yet).

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
