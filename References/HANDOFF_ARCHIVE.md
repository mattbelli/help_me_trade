# HANDOFF archive — superseded "you are here" snapshots

*The LIVE handoff is `HANDOFF.md`. This file preserves past **full** handoff snapshots in chronological order
(oldest first) so the lineage is never lost when `HANDOFF.md` is consolidated.*

**Convention going forward:** `HANDOFF.md` is edited incrementally each round; when it is **consolidated/rewritten**
(a superseding rewrite, not a small append), copy the full pre-rewrite version here as a new dated `##` section
at the BOTTOM, headed with its date + the commit it was superseded by. Finest-grained per-commit history is
always in `git log -p -- References/HANDOFF.md`. Note: the cumulative snapshot below already contains the
content of the incremental versions between the two snapshots (it grew by appending), so intermediate states
are not duplicated here.

---

## 2026-06-19 — original session-resume bootstrap (commit 296e823)

# Handoff — "you are here" (2026-06-20)

*Bootstrap for a fresh session. Read this + `AGENTS.md` (codebase map) + `BUILD.md` "Realignment" (the R1–R5
plan + dated history). Everything else is in those — this is just current focus, next steps, and gotchas.*

## State
Autonomous trading agent; loop = PERCEIVE→REASON→DECIDE→**ACT**→DELIVER→LEARN. **R2 (ACT) just shipped:** a
fail-closed execution **chokepoint** (RiskEngine 10 checks → ExecutionService, the sole broker path) + **real
Alpaca PAPER execution** for **equities and LONG options** (buy-to-open calls/puts, limit-only, ×100
multiplier). The agent placed a real paper order end-to-end (TSLA equity queued for Mon open; long-option
flow works once dates are right). ~13 guardrail/grounding bugs caught by adversarial Sonnet review before
commit. `execution_mode` default = `dry_run`; flip to `paper` in Settings → Execution/Risk to place orders.
**LIVE is hard-blocked server-side** (Increment 3). All committed + pushed (`main`); tests ~297 green.

## Next options (pick one — my lean: sell-to-close, then phone delivery)
- **Sell-to-close** — let the agent EXIT what it opens (options are buy-only today). Finishes the R2 lifecycle.
- **R2 Increment 3 — LIVE behind a typed double-gate** (lower caps, market-hours + duplicate checks). Only
  after the founder trusts paper for a while.
- **R2 Increment 4 — bounded auto-execute** (code-defined envelope, no per-order approval inside it).
- **R3 — phone delivery** (Capacitor + self-hosted APNs, approve/deny/modify from phone). The "be a dad"
  unlock; ties to the deploy model (one server + thin clients).
- **R1 backfill** — Schwab Realized-Gain/Loss import (completes P&L on incomplete positions like PLTR) +
  **futures** (only in Schwab/thinkorswim PDFs / 1099-B; a big chunk of real history, not yet ingested).
- **Deploy/access** — Tailscale for private laptop+phone access now; the ship model is one always-on server
  (agent + SQLite source of truth) + thin clients over HTTPS, NOT device sync.

## Open threads / gotchas
- **Parked widget work is uncommitted** (watchlist-v2 [founder DISLIKED it], scans/memory widgets,
  command-console/dock tweaks). Resolve: revert OR commit-the-good-parts (scans/memory were solid).
- Free **IEX feed = regular hours only**; no options market-data subscription confirmed → **options orders
  are limit-only** (premium = `limit_price`). `get_option_quote` returns real bid/ask only if entitled.
- **Backend** runs on the founder's machine at `127.0.0.1:8000`; LAN access needs `0.0.0.0` + CORS origin +
  the frontend `NEXT_PUBLIC_API_URL` change (or Tailscale).
- mem0 OFF; memory store is clean (self-feeding "20% cap" loop fixed with semantic dedup).

## Working agreements (also in auto-loaded memory)
Build via **Composer** (`node tools/composer-bridge/dispatch.mjs --brief <file>`, one run at a time, logs to
`tools/composer-bridge/last-run.log`) — not hand-edits. Recon/review via **Sonnet** subagents.
**Adversarially review safety-critical code (execution/risk) before commit.** Verify the diff + run pytest
yourself; don't trust self-reports. Founder is **cost-conscious about the Opus main loop** — delegate heavy
work. Commit cadence: iterate to "happy" → update BUILD.md → commit + push.


---

## 2026-06-22 — cumulative handoff (06-19 → 06-22), superseded by the consolidation in d764ecf
*Grew incrementally across the R2 sell-to-close, R3 phone-approval, Decision-Engine, Norgate strategy-edge, and
multi-account (A→C.1) work, then replaced 2026-06-22 by the clean "you are here" now in `HANDOFF.md`.*

# Handoff — "you are here" (2026-06-20)

*Bootstrap for a fresh session. Read this + `AGENTS.md` (codebase map) + `BUILD.md` "Realignment" (the R1–R5
plan + dated history). Everything else is in those — this is just current focus, next steps, and gotchas.*
*Other refs: `ARCHITECTURE.md` (guided subsystem tour), `CODE_AUDIT.md` (cleanup backlog; Tier 1 done),
`WATCHLIST_SPEC.md` (parallel feature), `SERVER_SETUP.md` (migration, done), `STARTUP.md` (run + device access),
`CLAUDE.md` (mission + working agreements, auto-loaded).*

## DONE (2026-06-20): the home Linux server is the dev + runtime home
Migration to the always-on server **`serv`** (`serv.tail78aab8.ts.net` / `100.94.77.104`, user `mbelli`, repo
`~/dev/help_me_trade`) is **complete**: uv-managed CPython 3.12 venv, deps installed, `ruff` clean + **312
pytest** green on Linux, systemd `hmt-backend` (`0.0.0.0:8000`) + `hmt-frontend` (`:3001`) enabled, Tailscale
up, nightly DB backup (`serv` 03:00 → `~/backups/` 14-day retention; `mattpc` pulls 03:30 over Tailscale).
Composer bridge verified on Node 22. `serv` is the **sole writer** of `data/trading.db`; Windows = one-way
backup only — **do all work on `serv`**. **Access from any tailnet device:** http://serv.tail78aab8.ts.net:3001
(see `STARTUP.md`). Deep server specifics are in the `serv-runtime` memory.

**Deferred:** Dockerize (backend + frontend + SearXNG via compose, `data/` as a volume) at the **live-prep
milestone** (Increment 3 era) — 12-factor groundwork already done, so a small lift. (Node ≥22 for the bridge.)

## DONE (2026-06-21): R3 phone-approval + the Decision Engine spine
Huge session — all committed + pushed to GitHub (`main`), **371 tests** green. **(1) R3 phone delivery
SHIPPED** (A: proposal lifecycle + approve/reject through the existing chokepoint; B: mobile Approve/Reject
card; C: agent trigger — actionable rule → `propose_trade` seam → phone proposal). **(2) Mobile bottom-tab
shell** (native-style, the desktop-grid stays for desktop). **(3) The Decision Engine** (the app's #1 value):
3 adversarially-verified **research rounds** (`DECISION_ENGINE_RESEARCH.md`) + the **offline backtest harness**
(`research/`: bars→triple-barrier labels→PurgedKFold+Platt calibration→EV/win% artifacts) + the **live scorer**
(`signals/decision_scorer.py`) putting **calibrated win%/EV/CI** on every proposal across desktop + phone.
Verified live (honest: dev model shows ~no edge + low-confidence flag — the machine refuses to fabricate).
**(4) Engine-deepening roadmap** captured in BUILD.md (S/R+TP+hold · world-context/events/macro/X-API/crypto ·
portfolio + risk profiles · the agent SIMULATION "value test"). Deploy w/o sudo: `kill`+systemd (services run
as `mbelli`, `Restart=always`). Cheap research: `Workflow({name:"deep-research-cheap"})`.

## DONE (2026-06-21 eve): real Norgate pull + the first strategy edge (Windows offline session)
Platinum Norgate pulled survivorship-free on Windows (`.venv-win`): **909 symbols / 2.9M bars / 16y**,
delisted names included (`XLNX-202202`, `YELLQ`, etc.). Five reviewed+committed research changes (this
pushed round): **delisting-artifact guard** (clip return_pct, winsorize EV — killed a fake +657% EV),
**parallel `--jobs` dataset build + replace-by-source** (fixed a silent append-double-count), **scale-out
exit simulator** (`research/strategy_sim.py`), **entry predicate + SPY market-context features**
(`research/strategy_features.py`), and the **strategy backtest builder** (`research/strategy_dataset.py`
→ `strategy_setups`/`strategy_labeled.db`). 403 tests green.
**Generic 22-feature model = NO edge** (Brier 0.2477 ≈ baseline 0.2479; collapses to base rate) — honest +
expected; those artifacts are NOT shipped. **The founder's own swing strategy = a real (small) edge:**
healthy(>SMA200) + oversold(−5%/3d) + market-down(SPY −1.5%/3d); exit −3% / +3% sell-half→breakeven /
+10% runner / 20d cap. **21,454 setups, 55.8% win, +0.59% EV/trade, 13/16 years positive** (only 2022 bear
negative). **Alpha-vs-beta control PASSED:** same entries without the market-down filter = +0.31%/52.3% →
the market-timing condition ~doubles EV (real alpha, not just drift). Hold sweep EV 0.46→0.56→0.59% for
5/10/20d (runner needs time; 10d ≈ 95% of the edge). Survives ~0.3% cost. **Caveat before real money:
naive per-trade significance is INFLATED — setups cluster on dip dates → need overlap-aware testing
(block bootstrap / purged CV).**
**Train/test-gated tuning (train 2011-20 / test 2021-26, +2 commits — trailing-stop sim + builder
threading):** all knobs PLATEAU (no edge-of-grid runaway = robust). TP levels: wider runner helps OOS
(ranking held perfectly train→test, test EV even > train), but +4% first-target buys EV by selling
win-rate (→50%). **Trailing the runner is the best lever — lifts EV while KEEPING ~57% win%:** trail
plateaus ~15% at train +0.71 / test +0.98 (vs original fixed 3/10 = +0.51/+0.69). At a loose trail the
runner mostly rides to the 20d cap, so the gain ≈ "sell half at +3%, ride the rest to cap w/ breakeven
floor." **Recommended tuned config: first +3% sell-half → breakeven → trail ~10-12% → 20d cap.** Honest
forward expectation: anchor to TRAIN (~+0.7%) not the friendlier test window, **minus ~0.2-0.3% costs →
~+0.4-0.5%/trade net at ~57% win** — real but thin. Tuning is DONE (plateaus reached); further knob-turning
= overfitting. Sweep logs: `data/research/_sweep_*.log`.
**SIGNIFICANCE PASSED (`research/significance.py`, +1 commit):** block bootstrap on the per-entry-date
return series (collapses dip-date clustering: 21,454 trades → only **468 effective dip-days**). Tuned
config full-15y per-trade EV **+0.81%**; naive IID CI [+0.75,+0.87] (overstated) vs **honest block CI
[+0.38%, +1.02%], P(mean≤0)=0.0% → significant at 5%**, robust to block size (20/30d identical). So the
edge is statistically real, NOT a clustering illusion. **Triple-validated: alpha-vs-beta ✓, out-of-sample
✓, overlap-aware significance ✓.** Honest caveats remain: numbers are GROSS (honest floor ~+0.38% → after
~0.2-0.3% costs the net floor is thin ~+0.1%); ONE 15y history (regime risk; 2022 was negative);
significance ≠ forward guarantee. Run: `python -m research.significance --source norgate`.
**SHORT MIRROR TESTED → NO EDGE (+1 commit: `side` support in `research/strategy_*.py`; research-only).**
Founder raised enabling short trading; backtest says don't. Overbought stock + overbought market → short:
shorter hold IS better (confirms drift-headwind intuition) but best config (5d) is only +0.21% GROSS and
**NOT significant** (honest CI [-0.07%, +0.37%], P(mean≤0)=9.5%) — negative after borrow, and the sim
optimistically understates squeeze losses. Long edge (~4× the short, significant) ≈ beta tailwind + small
mean-reversion; the short can't beat the drift. **Decision: KEEP the no-short invariant.** Full record +
the safety checklist (if ever revisited) → `References/SHORT_TRADING_FINDINGS.md`. Short backtest DB:
`data/research/strategy_short.db` (gitignored).

## ACTIVE THREAD (2026-06-21 late): full multi-account — Phase A in progress
Founder chose **full multi-account first** (multiple Alpaca paper + multiple Schwab accounts, execution-capable
where allowed) so strategies can bind to a dedicated account. Spec: **`References/MULTI_ACCOUNT_PLAN.md`**
(two-level model: broker connections → accounts; per-account RiskEngine hardening = the safety-critical core).
**Sub-step 1 DONE — schema + migration** (`db/multi_account.py`, `db/schema_helpers.py`): `broker_connections`
table + `trade_accounts` extended (connection_id/enabled/execution_capable/is_default) + `account_id` columns on
poll_runs/order_executions/paper_order_proposals + idempotent backfill (legacy alpaca → default account; schwab
ledger accounts linked read-only). Additive + behavior-preserving; adversarially reviewed (idempotency +
behavior-preservation hold; backfill scoped to alpaca so a future schwab poll_run isn't mislabeled). Runs in
`api/main.py` lifespan. **Sub-step 2 DONE — account read-model + resolver** (`db/accounts.py`: list/get
connections+accounts, `get_default_account`, `default_execution_account`, `account_is_execution_capable`
deny-by-default; `brokers/factory.py:get_broker_for_account` resolves account→broker adapter, behavior-identical
for the default account; per-connection creds seam documented for Phase B). Read-only, behavior-preserving, 450
tests. **Sub-step 3 DONE — account_id threaded through the execution path** (`OrderIntent.account_id`;
`build_order_proposal_from_args`/`order_intent_from_fields` resolve the default execution account + keep
`broker` in lockstep; `create_phone_proposal` persists account_id; `approve_proposal` blocks non-execution-
capable accounts before submit; `submit` routes via `get_broker_for_account` + records account_id).
Behavior-identical for the default account; legacy proposals resolve to default; unmigrated DBs fall back to
primary_broker. Adversarially reviewed (items 1-4 hold; safe). 463 tests. **Sub-step 4 DONE — RiskEngine per-account hardening** (`trading/risk_engine.py`,
`db/order_executions.py`): execution-capability hard-block now INSIDE `RiskEngine.validate` (deny-by-default,
runs on every validate incl. dry_run + precheck → the chokepoint self-defends regardless of caller); per-account
daily-order ceiling ADDED beside the unchanged global ceiling (`count_executions_today` gained an optional
account_id filter; the no-arg/global path is byte-identical → never weakened). Snapshot/buying-power/
concentration left broker-keyed (already per-account-correct: one account per broker today; becomes account-keyed
in sub-step 5). Adversarially reviewed — all invariants hold, no CRITICAL/HIGH/MEDIUM, safe. 473 tests.
**Sub-step 5 DONE — per-account snapshot + account-keyed reads.** `save_snapshot` stamps `poll_runs.account_id`;
the poll loop resolves `get_default_account(broker)` and stamps every new run; `get_latest_snapshot(broker, *,
account_id=)` is account-keyed with a broker fallback (account_id None = byte-identical legacy path);
RiskEngine + the sell-side held-position check read by `intent.account_id`. Behavior-identical for the default
account; no fail-close (boot migration backfills NULL alpaca runs before the poll loop starts + the 5-min
freshness gate fail-closes on stale). Schwab multi-account snapshot iteration DEFERRED (adapter hardwires one
account_hash; read-only; → Phase B). Adversarially reviewed — safe; 2 latent multi-connection findings tracked
in MULTI_ACCOUNT_PLAN.md. 479 tests.

**Phase B sub-step 1 DONE (2026-06-22): 2nd Alpaca paper account ("STRAT1") live.** Per-connection credentials
(`APCA_*_STRAT1` env → discovered → `conn_alpaca_strat1`/`acct_alpaca_strat1` registered, execution-capable;
secrets stay in env), factory builds a per-connection adapter, poll loop iterates accounts with a fail-closed
id-guard. Default account byte-identical. Reviewed; gate green (485). Caught+fixed: a test-isolation flake
(real `.env` STRAT1 leaks into tests via `load_dotenv` at import → conftest now strips ambient suffix vars +
clears broker caches) and an active rules-double-eval regression (non-default accounts now sync snapshot ONLY;
reconcile + rules-eval stay on the default account). **Phase C.0 DONE — execution-reconcile per-account** (`fill_tracker` + `list/count`
fns take `account_id`; each account reconciles its own fills via its own adapter; per-account poll + boot
reconcile). Byte-identical default + None paths; adversarially reviewed (no cross-account reconcile, orphan-reject
scoped). Rules-per-account = resolved-by-design (rules stay default-only). **Phase C.1 DONE — strategy registry + account binding.**
`db/strategies.py` (registry table + accessors + idempotent seed: Dip-Buy seeded DISABLED, bound to STRAT1,
exit_mode=auto, dip params); `propose_trade` routes to a bound account (per-account dedup + sizing;
`invalid_account` deny-by-default for an explicit non-exec-capable account; legacy `account_id=None`→default
byte-identical); `signals/strategy_scan.py` rewritten registry-driven (`_run_strategy` per strategy, skips
disabled/unbound/not-ready, held-skip per bound account); `GET/PUT /api/strategies` (PUT validates exec-capable
binding). Adversarially reviewed — deny-by-default at 3 layers, legacy byte-identical, per-account isolation;
dip_buy requires explicit human PUT to arm. 503 tests. **To arm the forward-test:**
`PUT /api/strategies/dip_buy {"enabled":true,"account_id":"acct_alpaca_strat1"}` (entry-only until Phase D).
**NEXT: C.2** Strategies settings UI → **D** exit module (then exits actually fire per exit_mode). NOTE: the build's Composer/test runs
hit the real `data/trading.db` (DEFAULT_DB_PATH) — STRAT1 got pre-registered + ~88 snapshots there; benign
(additive, zero trades placed, confirmed by review).

**★ PHASE A COMPLETE** — accounts are first-class across schema, registry, execution, risk, and snapshot/sync;
the chokepoint self-defends per-account; everything behavior-identical for the single default Alpaca account
(when you restart the backend the migration runs + the default account is created/used, but trading is
unchanged). **NEXT: Phase B** (frontend account selector + connection-management UI to ADD a 2nd Alpaca paper
key — the dedicated forward-test account) → **Phase C** (Strategies settings section + strategy→account
binding) → **Phase D** (the exit module that completes the dip forward-test). The forward-test "lights up" once
Phase B gives you the dedicated account to bind in Phase C. Then Phases B (frontend) · C (strategy registry + account binding) · D (exit module).

## Then (post-multi-account)
- **FORWARD-TEST on paper — Increment 1 (ENTRY) SHIPPED; EXITS next.** The dip-buy entry path is wired +
  reviewed + tested (`signals/strategy_scan.py`): a **disabled-by-default** scan (`dip_strategy_enabled`,
  off) evaluates the watchlist each cycle with the **same** `research.strategy_features.is_strategy_entry`
  predicate as the backtest (zero train/serve skew, long-only), routes passes through the existing
  `propose_trade` chokepoint → phone/desktop proposal → approve → paper. Held-position skip (one entry per
  signal) + notional/SPY/date-alignment guards; adversarially reviewed (safety invariants all clean). 435
  tests. **Architecture principle (founder, 2026-06-21): strategies are isolated, independently toggleable,
  FROZEN/versioned modules — the learning layer may observe outcomes but must NEVER silently mutate a
  strategy** (that would destroy the calibrated win%). Generalize to a multi-strategy registry only when the
  2nd strategy lands. **NEXT: the EXIT module** (−3% stop / +3% sell-half→breakeven / trail ~10-12% / 20d
  cap) — completes the round-trip; prerequisite for any real EV-vs-backtest comparison. Known v1 limitation:
  a user-rejected setup can re-propose next cycle (needs a per-symbol cooldown) — fold into the exit round.
  To run it: flip `trading.dip_strategy_enabled` true (+ set `dip_strategy_notional_usd`) via settings.
- **Remaining research:** short-side mirror (overbought→short, shorter TP — RESEARCH ONLY, app can't short;
  diagnostic for how much of the long edge is mean-reversion vs beta); pure-beta decomposition (drop
  oversold); **sector context v2** beyond SPY-only; per-feature IC to rank/size setups → fractional-Kelly.
  Strategy harness: `research/strategy_*.py`; significance: `research/significance.py`. Per-year/cost
  analysis pattern is in this session's transcript.
- **Real Norgate artifacts for the live scorer** still open — the *generic* pull is done but edgeless; the
  shipped `git add -f research/artifacts/*.json` should come from a model trained on the *strategy* setups
  once the edge is validated. Runbook: `References/NORGATE_PULL.md`.
- **Layer-A risk profiles** (per-account conservative/moderate/aggressive — near-term, config + RiskEngine caps).
- **Deterministic TP-ladder + hold-length** output module (parallelizable; completes the "fully-planned idea").
- **Trade-rule authoring UI** (arm actionable `type:"trade"` rules from the app — they're created DISABLED).
- **RN phone app** — a PARALLEL track (decoupled: it's a native client of the done/stable backend API). Expo;
  much of it cloud-buildable via EAS (Mac mainly needed for APNs/Live-Activities native iteration). The web
  mobile shell is the design reference. The RN unlock = lock-screen Approve/Reject (APNs) + native feel.

## State
Autonomous trading agent; loop = PERCEIVE→REASON→DECIDE→**ACT**→DELIVER→LEARN. **R2 (ACT) shipped:** a
fail-closed execution **chokepoint** (RiskEngine 10 checks → ExecutionService, the sole broker path) + **real
Alpaca PAPER execution** for **equities and LONG options**, full lifecycle: **buy-to-open AND sell-to-close**
(options limit-only, ×100 multiplier). Sells are guarded so they can ONLY reduce a held long — no shorting.
The agent placed a real paper order end-to-end. ~16 guardrail/grounding bugs caught by adversarial Sonnet
review before commit. `execution_mode` default = `dry_run`; flip to `paper` in Settings → Execution/Risk.
**LIVE is hard-blocked server-side** (Increment 3). All committed + pushed (`main`); tests **312** green.

## NEXT FOCUS — R3: phone delivery (push approval) — IN PROGRESS
The "be a dad" unlock: the agent finds + sizes a setup → **pushes the phone** with Approve/Reject → tap approve
while away → order flows through the **existing** chokepoint (RiskEngine → ExecutionService, never a new path).

**Decision locked (2026-06-20):** native client target = **React Native / Expo** (founder has Mac+Xcode and
wants the native ceiling — Live Activities / Dynamic Island / lock-screen action buttons; assistant-ui's RN
support shares the chat *runtime*, only the UI restyles). **Web Push is SKIPPED** — validate the approval loop
via the existing **in-app notification** system, then build the native client straight onto APNs. The whole
approval *backend* is delivery-agnostic and reused verbatim by RN. See `[[mobile-ux-direction]]` memory.

**Done — R3-A (backend core, 322 tests):** proposal lifecycle + `POST /api/proposals/{id}/approve|reject`
routing through the existing chokepoint (see BUILD.md history 2026-06-20).
**Done — R3-B (mobile Approve/Reject card):** the Activity tab fetches `GET /api/proposals/pending` and
renders amber Approve/Reject cards (live expiry countdown, badge on the Activity pill tab, toasts) → call
the endpoints. The in-app loop is end-to-end: `POST /api/proposals` → card → Approve → chokepoint (dry_run
logs; flip to `paper` to place).
**Done — R3-C (agent trigger):** an **actionable signal-rule** (`action.type="trade"` + symbol/side/notional)
firing in the scan loop → the single **`propose_trade` intake seam** (`trading/proposal_intake.py`) → sizes
(fixed notional) → gates (enabled/kill-switch/market-hours/max-pending/dedup/dry-RiskEngine) → `create_phone_proposal`.
Agent NEVER executes; no new broker path. Adversarially reviewed + hardened: **trade rules are force-created
DISABLED** (`create_rule` — a human must enable; the LLM can't self-arm), `Action.type` is a validated
`Literal`, global pending cap. 344 tests. **The whole decision engine plugs in behind this one seam.**
**IN PROGRESS — the Decision Engine** (the headline value; research Rounds 1-3 in
`References/DECISION_ENGINE_RESEARCH.md`; architecture = train-offline/serve-online in BUILD.md). **Backtest
harness Increment 1 SHIPPED:** offline `research/` package — `bars.py` (source-agnostic SQLite daily-bars cache,
Alpaca adapter now / Norgate-on-Windows later), `labeling.py` (triple-barrier, vol-scaled, ATR barriers),
`features.py` (point-in-time feature vector reusing `signals/`), `dataset.py` (assembler → `labeled_setups`
table). 9 tests; point-in-time correctness verified. **Harness Increment 2 SHIPPED:** `research/` — `dataset_io.py`
(load+one-hot+standardize), `validation.py` (PurgedKFold+embargo, López de Prado), `calibration.py` (logistic
score → out-of-fold preds → **Platt** sigmoid → Brier + reliability), `ev.py` (per-bucket EV/win% + **Wilson
CIs** + insufficient-data flags), `train.py` (→ JSON artifacts in `research/artifacts/`). `scikit-learn` added.
362 tests. **Verified on real data (447 rows, 3 symbols): Brier 0.2456 ≈ base-rate baseline → it honestly
shows ~no edge + `low_confidence` flag (small/survivor-biased Alpaca sample) — the machine refuses to fabricate
an edge.** Dev artifacts gitignored; REAL artifacts come from **Norgate offline-on-Windows** (`git add -f`).
**Increment 3a SHIPPED — the live scorer.** `signals/decision_scorer.py` loads `research/artifacts/` and turns
a live BUY setup → **calibrated win% + EV + CI + low-confidence flag + top "why" factors** (reuses the training
transform `dataset_io.compute_raw_score`/`featurize_dict` → no train/serve skew; best-effort try/except, never
blocks execution; sells/options unscored). `score` is stored on the proposal (`paper_order_proposals.score_json`)
+ returned by the propose_order tool → rides the existing proposal API (UI-agnostic → serves desktop, phone,
future RN). 370 tests. **Verified live on serv** (AAPL → win 57.1%, EV +0.45%, CI [48.5,59.1], low_confidence).
**Increment 3b SHIPPED — score on BOTH desktop + phone.** One shared `ProposalScore` component (win% bar vs
base-rate + EV + CI band + low-confidence/thin-data badges + top "why" factors) reused on: the phone Activity
card (`PendingProposalCard`), a NEW desktop **`agent-proposals` workspace widget** (the cockpit equivalent of
the Activity tab — list + score + Approve/Reject), and the desktop chat `OrderProposalCard` (fetches via new
`GET /api/proposals/score?symbol=&side=`). Null score degrades cleanly. 371 tests; deployed live.
**→ Decision Engine Increment 3 COMPLETE: agent scores a setup → calibrated win%/EV/CI shows on every surface →
human approves → existing chokepoint.** Next: real Norgate artifacts (offline-on-Windows) to make the win%
trustworthy; trade-rule authoring UI; Modify; APNs+RN. Other threads: trade-rule authoring UI;
Modify; **APNs + RN**. Deferred hardening (before paper-autonomous):
stuck-`approving` reconcile on restart; auth-disabled warning; pass prebuilt to avoid double-build.
→ Original kickoff: `References/R3_KICKOFF.md` (mechanism section now superseded by the RN decision above).

**Parallel option still open — watchlist v3** (`WATCHLIST_SPEC.md`, Phase A = IEX-proxy RVOL + 52w slider +
customization, no new deps). Groundwork laid: scans/memory widgets committed; the disliked v2 RVOL work is
**stashed locally on `serv`** (`stash@{0}`, see `serv-parked-watchlist-v2` memory) for reuse.

Other options: **R2 Inc 3** LIVE behind a typed double-gate; **R2 Inc 4** bounded auto-execute; **R1 backfill**
Schwab Realized-G/L + futures import.

## Open threads / gotchas
- Free **IEX feed = regular hours only**; no options market-data subscription → **options orders are
  limit-only** (premium = `limit_price`). `get_option_quote` returns real bid/ask only if entitled.
- **Access:** backend binds `0.0.0.0:8000` on `serv` via systemd, reachable on the tailnet at
  `serv.tail78aab8.ts.net` (CORS origin + host allowlist set in `.env`; `NEXT_PUBLIC_API_URL` baked into the
  build). Plain HTTP is fine over Tailscale's WireGuard — never port-forward to the public internet.
- **Local-dev port clash:** systemd already holds `:8000` + `:3001`. Stop the services before `./dev.sh`
  (`sudo systemctl stop hmt-backend hmt-frontend`) or you'll get "address already in use". See `STARTUP.md`.
- **R3 secure-context note:** Web Push / service workers need HTTPS; plain http over Tailscale won't qualify —
  R3 will likely need `tailscale serve` (HTTPS on the `*.ts.net` name) in front of the frontend.
- Parked widget work **RESOLVED**: scans/memory widgets committed; watchlist-v2 stashed (`serv` `stash@{0}`).
- mem0 OFF; memory store clean (self-feeding "20% cap" loop fixed with semantic dedup).

## Working agreements (also in `CLAUDE.md` + auto-loaded memory)
Build via **Composer** (`node tools/composer-bridge/dispatch.mjs --brief <file>`, one run at a time, logs to
`tools/composer-bridge/last-run.log`) — not hand-edits (trivial one-liners excepted). Recon/review via
**Sonnet** subagents. **Adversarially review safety-critical code (execution/risk) before commit.** Verify the
diff + run pytest yourself; don't trust self-reports. Founder is **cost-conscious about the Opus main loop** —
delegate heavy work. Commit cadence: iterate to "happy" → update BUILD.md → commit + push. Pre-push: `ruff
check .` + `pytest` + `cd frontend && npm run typecheck` (all three run in CI).

---

## 2026-06-22 (late) — multi-account C.2 + caps/universe/account-display (superseded by the late-06-22 consolidation that added earnings E1/E2 + exit-module D1)

# Handoff — "you are here" (2026-06-22)

*Bootstrap for a fresh session. Read this first, then `CLAUDE.md` (mission + working agreements, auto-loaded),
`AGENTS.md` (codebase map), and the per-topic refs: `MULTI_ACCOUNT_PLAN.md` (the active build's full spec +
history), `BUILD.md` (roadmap + dated history), `DECISION_ENGINE_RESEARCH.md`, `SHORT_TRADING_FINDINGS.md`,
`NORGATE_PULL.md`, `ARCHITECTURE.md`, `STARTUP.md` (run + device access), `SERVER_SETUP.md`. Deep server
specifics: the `serv-runtime` memory.*
*Past handoff snapshots (the lineage this file consolidated) live in `HANDOFF_ARCHIVE.md` — when you do a
superseding rewrite of this file, append the old full version there as a new dated section.*

---

## CURRENT FOCUS — multi-account + the dip-buy forward-test (DONE through C.2 + refined-dip, DEPLOYED)
Founder chose **full multi-account** so a strategy can bind to a dedicated account. The whole arc is built,
adversarially reviewed, committed/pushed (`main`), and **deployed live on serv** — **503 tests** green.
Full spec + per-sub-step detail + carry-forward findings: **`References/MULTI_ACCOUNT_PLAN.md`**.

**Model:** two levels — **broker connections → accounts**. Alpaca: one key pair = one account (creds per
account). Schwab: one OAuth app = many account hashes (shared creds). Secrets stay in `.env`; the DB stores only
a non-secret `env_suffix` per connection.

**What's done:**
- **Phase A (accounts first-class)** — schema/migration (`db/multi_account.py`), account read-model
  (`db/accounts.py`), `account_id` threaded through proposal→`OrderIntent`→execution, **RiskEngine per-account
  hardening** (execution-capability hard-block INSIDE `validate` → the chokepoint self-defends; per-account
  daily cap added beside the unchanged global one — never weakened), per-account snapshot + account-keyed
  `get_latest_snapshot`. Byte-identical for the single default account throughout.
- **Phase B.1 — 2nd Alpaca paper account "STRAT1" LIVE.** `APCA_*_STRAT1` env → discovered → `conn_alpaca_strat1`
  / `acct_alpaca_strat1` registered (execution-capable); factory builds a per-connection adapter; poll loop
  iterates accounts with a fail-closed id-guard (snapshot `account.id` must match the account's
  `broker_external_id`). Both accounts sync per-account on serv right now.
- **Phase C.0 — execution-reconcile per-account** (`fill_tracker` + `list/count` fns take `account_id`; each
  account reconciles its own fills via its own adapter). (Rules/alerts stay default-account-only by design —
  watchlist-global, no double-fire.)
- **Phase C.1 — strategy registry + account binding.** `db/strategies.py` (registry + idempotent seed);
  `propose_trade` routes to a bound account (per-account dedup + sizing; `invalid_account` deny-by-default;
  legacy `account_id=None`→default byte-identical); `signals/strategy_scan.py` is registry-driven; `GET/PUT
  /api/strategies`.
- **Phase C.2 — Strategies settings UI + mobile Settings access** (2026-06-22). Cockpit panel
  `frontend/components/settings/settings-strategies-panel.tsx` (per-strategy enable / account-bind /
  notional / exit-mode / `far_support_min`, save-per-card, honesty caption on refined-dip, unbound-while-
  enabled warning). New `GET /api/strategies/accounts` feeds the account picker (`frontend/lib/strategies-api.ts`).
  Settings is now reachable on **mobile** — `<SettingsDialog collapsed />` lives in the phone header
  (gear icon) and the dialog nav stacks responsively (`flex-col sm:flex-row`). Reuses the desktop dialog (no
  duplicate mobile UI — RN is the eventual target). **Pending: in-browser/on-phone visual check.**
- **Per-strategy universe — decoupled from the active watchlist** (2026-06-22): a strategy now scans its OWN
  `universe` (col on `strategies`), not whatever named watchlist is active (that footgun silently repointed live
  strategies). Specs: `sp500` (built-in broad market, `signals/universes/sp500.json` = 503 Alpaca-validated
  constituents), `watchlist:<id>` (pin a SPECIFIC named list), `active_watchlist` (legacy). Resolver
  `signals/strategy_universe.py`; `GET /api/strategies/universes` feeds the UI picker. **Backfill is
  conservative:** existing rows → `active_watchlist` at migration (the live enabled $10k `dip_refined` is NOT
  silently broadened); NEW strategies default to `sp500`; user opts in via the UI Universe dropdown. Alpaca
  verified for breadth: 12k tradable equities available, 500-name × 420d fetch = ~2.5s, well under rate limits.
  Adversarially reviewed: migration atomic, resolver can't raise, no silent broadening.
- **Per-strategy risk caps** (2026-06-22): `strategies.max_concurrent_positions` + `max_deployed_usd`
  (nullable cols, NULL=uncapped; idempotent ADD COLUMN migration). Enforced in `_run_strategy` — the scan
  stops proposing once (open positions + pending proposals) in the bound account hit the cap. This is the fix
  for the **dip-cluster problem** (many names fire on the same down day). Best-effort/advisory at propose-time
  (assumes serialized scans, which the scheduler guarantees); the HARD guarantee stays `RiskEngine.validate()`
  + buying-power at the chokepoint. Adversarially reviewed: no cap-breach path in a serialized scan. Surfaced
  in the Strategies UI as "Max positions" / "Max deployed ($)" (blank = no cap).
- **Refined-dip strategy wired live** (2026-06-22, from `origin/research/decision-engine-data-layer`).
  Ported `research/levels.py` (swing-pivot S/R + `level_features`) + the `far_support_min` arg on
  `is_strategy_entry` (both pure/point-in-time; adversarially reviewed clean). New seeded strategy
  `dip_refined` ("Dip-Buy + S/R filter") = the dip edge PLUS "must be ≥5% above nearest support";
  **seeded DISABLED + UNBOUND**, `far_support_min=5.0`. Also fixed a latent test-only bug:
  `_account_is_ready` now threads `db_path` (was reading the default DB).

**Live state right now:** backend running C.1 code (deploy = `kill <MainPID>` → systemd `Restart=always`
reloads the working tree; migration runs idempotently at boot). Default Alpaca (`acct_fc78907f4bda`) + STRAT1
(`acct_alpaca_strat1`) both polling per-account, no errors. **The `Dip-Buy` strategy is seeded DISABLED**, bound
to STRAT1, $500, `exit_mode=auto`.

**To arm the entry-only forward-test** (when you choose to):
`PUT /api/strategies/dip_buy {"enabled": true}` (already bound to STRAT1). Entries are always **phone-approved**
(safe — Approve/Reject each); `exit_mode` governs EXITS, which **don't fire until Phase D** — so arming now means
approved dip entries accumulate as positions you'd manage by hand until the exit module exists.

## NEXT
1. **In-browser/on-phone check of the Strategies UI** (C.2 shipped but UI-only — verify the panel renders,
   account dropdown lists STRAT1/default, save persists, and the phone gear opens Settings legibly). Then
   decide bindings: refined-dip wants its OWN forward-test account (add `APCA_*_STRAT2` for a clean A/B vs
   dip_buy) — it's seeded unbound on purpose.
2. **Phase D — the exit module** (−3% stop / +3% sell-half→breakeven / trail ~10–12% / 20d cap), auto-executing
   per `exit_mode` through the now-account-aware chokepoint. Completes the round-trip → the forward-test
   measures real EV. Known v1 limitation to fold in: a user-rejected entry can re-propose next cycle (needs a
   per-symbol cooldown).
3. Carry-forward (in `MULTI_ACCOUNT_PLAN.md`): **M-2 migration-stamp hardening** before any DB rebuild; Schwab
   multi-account snapshot iteration (adapter change) if/when Schwab goes beyond read-only. Two low-pri review
   nits: guard `cluster_mean==0` in `research/levels.py`; thread `db_path` into `get_broker_for_account`.

## ⚠️ GOTCHAS — read these
- **Tests must NEVER touch the real `data/trading.db`.** It bit twice this session: the build's Composer/API
  test runs wrote through `DEFAULT_DB_PATH` (= the real DB) — pre-registering STRAT1 (B.1) and arming `dip_buy`
  (C.1). Both caught on post-deploy verification and reset; **zero proposals/trades occurred.** Now guarded:
  `tests/conftest.py` forces `TRADING_DB_PATH` to a throwaway temp DB at import. Still — when reviewing a
  Composer round, **verify the real DB wasn't mutated** (check `strategies`, `trade_accounts`,
  `paper_order_proposals`, `order_executions` for unexpected recent rows) before trusting a deploy.
- **Strategies are isolated, independently toggleable, FROZEN/versioned modules** (founder principle): the
  learning layer may OBSERVE outcomes but must NEVER silently mutate a strategy's validated logic (that would
  destroy the calibrated win%). The registry is config/data only.
- **Deploy w/o sudo:** `kill <MainPID>` (get it via `systemctl show hmt-backend -p MainPID --value`); systemd
  `Restart=always` relaunches into the working tree. Boot runs `migrate_multi_account` + `seed_default_strategies`
  (idempotent). Verify: API responds (HTTP 401 = up, auth-gated), both accounts saving `account=…` snapshots,
  no tracebacks/`mismatch`.
- **`dip_strategy_*` settings keys are DEPRECATED** (the registry supersedes them; the seed read them once for
  carry-over).
- **Free IEX feed = regular hours only**; no options market-data → **options orders are limit-only**.
- **Access:** backend `0.0.0.0:8000` on serv via systemd, reachable on the tailnet (`serv.tail78aab8.ts.net`).
  Plain HTTP over Tailscale's WireGuard only — never port-forward to the public internet. **Local-dev port
  clash:** stop the services before `./dev.sh` (systemd holds `:8000`/`:3001`).
- **LIVE trading is hard-blocked server-side** (`execution_mode` default `dry_run`; flip to `paper` in Settings;
  LIVE needs the typed double-gate, not yet built).

## The validated edge behind Dip-Buy (Norgate survivorship-free backtest)
Entry: **healthy (>SMA200) + oversold (−5%/3d) + market-down (SPY −1.5%/3d)**; exit (Phase D): −3% stop / +3%
sell-half→breakeven / trail ~10–12% / 20d cap. **21,454 setups, ~57% win, honest net ≈ +0.4–0.5%/trade** —
**triple-validated** (alpha-vs-beta ✓, out-of-sample ✓, overlap-aware block-bootstrap significance ✓:
P(mean≤0)=0%). Caveats: GROSS floor thin after costs; ONE 15y regime (2022 negative); significance ≠ forward
guarantee — hence the live paper forward-test. **Short mirror tested → NO edge → no-short invariant stays**
(`SHORT_TRADING_FINDINGS.md`). The dip predicate is reused **verbatim** by the live scan (zero train/serve skew).
Real Norgate harness: `research/strategy_*.py`, `research/significance.py`; runbook `NORGATE_PULL.md`. Still
open: ship real `research/artifacts/*.json` trained on the *strategy* setups (the generic 22-feature model was
edgeless — not shipped).
**Refined-dip (`dip_refined`, far_support_min=5.0):** the dip edge + a "≥5% above nearest support" filter
(`research/levels.py` swing-pivot S/R). Backtest on `origin/research/decision-engine-data-layer` showed the
filter ~**doubled the RECENT excess over SPY (+1.6%→+3%/trade)** — but ⚠️ this refinement is **NOT
block-bootstrap-validated** to the base dip's standard (no significance pass on the variant). Treat as a live
forward-test candidate, not a calibrated edge; the UI says so. The unvalidated branch siblings
(rsi2 / consec_down / momentum entry predicates) are ported into `research/strategy_features.py` but **left out
of the live registry** (no cited edge yet).

## Stable foundation (context)
- **The chokepoint is sacred.** `trading/execution_service.submit()` is the only broker path; `RiskEngine.validate()`
  gates every order (kill-switch-first, deny-by-default, now per-account capability hard-block). Real Alpaca
  **PAPER** for equities + LONG options, full buy-to-open / sell-to-close lifecycle; sells only reduce a held
  long (no shorting). Decimal-only for money in `trading/`. **Adversarially review (Sonnet) any change to
  `trading/` or `db/order_executions` before commit** — it keeps catching real safety bugs.
- **Decision Engine** (the app's #1 value): PERCEIVE→REASON→DECIDE → calibrated win%/EV/CI on every proposal
  (`signals/decision_scorer.py`, shown desktop + phone). Honest by construction (won't fabricate an edge).
  Train-offline/serve-online; research in `DECISION_ENGINE_RESEARCH.md`.
- **R3 phone-approval shipped** (in-app Approve/Reject loop end-to-end through the chokepoint). RN/APNs native
  client is a decoupled parallel track (`mobile-ux-direction` memory).
- **serv** is the dev + runtime home + sole writer of `data/trading.db` (nightly backup → `mattpc`). Do all work
  on serv.

## Other open threads (deprioritized)
- **Layer-A risk profiles** (per-account conservative/moderate/aggressive — config + RiskEngine caps; natural
  fit now that accounts are first-class).
- **RN phone app** (Expo; EAS cloud builds; Mac for APNs/Live-Activities). Lock-screen Approve/Reject = the unlock.
- **Watchlist v3** (`WATCHLIST_SPEC.md`; disliked v2 stashed on serv `stash@{0}`, `serv-parked-watchlist-v2` memory).
- **R1 backfill:** Schwab Realized-G/L import + **futures import** (PDF statements; `/ES /MES /CL` point
  multipliers — completeness gate for the retrospective).

## Working agreements (also in `CLAUDE.md` + auto-loaded memory)
Build via **Composer** (`node tools/composer-bridge/dispatch.mjs --brief <file>`, one run at a time, logs to
`tools/composer-bridge/last-run.log`) — not hand-edits (trivial/surgical excepted). Recon/review via **Sonnet**
subagents. **Adversarially review safety-critical code (execution/risk) before commit.** Review the real `git
diff` + run the gate **yourself**; don't trust self-reports. Founder is **cost-conscious about the Opus main
loop** — delegate heavy work. Cadence: iterate → "happy" → update `BUILD.md`/`HANDOFF.md` → commit + push. Pre-push:
`ruff check .` + `python -m pytest -q` + `cd frontend && npm run typecheck` (all three in CI).
