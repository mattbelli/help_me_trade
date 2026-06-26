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


## 2026-06-23 — pre-RVOL-focus snapshot (superseded by 46c40e6 "new focus = Schwab real-volume data → watchlist RVOL")

*(Archived 2026-06-24 — backfill: this snapshot's superseding rewrite happened on 2026-06-23 but the archive
append was missed at the time.)*

*Bootstrap for a fresh session. Read this first, then `CLAUDE.md` (mission + working agreements, auto-loaded),
`AGENTS.md` (codebase map), and per-topic refs: `MULTI_ACCOUNT_PLAN.md`, `BUILD.md` (roadmap + dated history),
`DECISION_ENGINE_RESEARCH.md`, `SHORT_TRADING_FINDINGS.md`, `NORGATE_PULL.md`, `ARCHITECTURE.md`, `STARTUP.md`,
`SERVER_SETUP.md`. Deep server specifics: the `serv-runtime` memory.*
*Past full handoff snapshots live in `HANDOFF_ARCHIVE.md` (oldest first) — when you do a superseding rewrite of
this file, append the old full version there as a new dated section.*

---

## CURRENT FOCUS — make dip + earnings live ROUND-TRIPS: the per-strategy EXIT MODULE (Phase D) + EARNINGS sleeve (Phase E)
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


## 2026-06-24 — pre-"Schwab data hub" snapshot (superseded by 6bc0eed's CURRENT FOCUS rewrite)

*(Archived 2026-06-24 — same-day backfill.)*

*Bootstrap for a fresh session. Read this first, then `CLAUDE.md` (mission + working agreements, auto-loaded),
`AGENTS.md` (codebase map), and per-topic refs: `MULTI_ACCOUNT_PLAN.md`, `BUILD.md` (roadmap + dated history),
`DECISION_ENGINE_RESEARCH.md`, `SHORT_TRADING_FINDINGS.md`, `NORGATE_PULL.md`, `ARCHITECTURE.md`, `STARTUP.md`,
`SERVER_SETUP.md`. Deep server specifics: the `serv-runtime` memory.*
*Past full handoff snapshots live in `HANDOFF_ARCHIVE.md` (oldest first) — when you do a superseding rewrite of
this file, append the old full version there as a new dated section.*

---

## CURRENT FOCUS — Schwab real-volume market data → watchlist RVOL ("vol % above expected")
**Goal (founder's next feature):** a watchlist column that flags, in real time intraday, when a symbol's
volume *so far today* is unusually high *for this time of day* — comparing today's cumulative volume to the
historically expected cumulative volume by that time (time-of-day **volume profile**, NOT a flat daily
average). Needs **real consolidated volume**: Alpaca's free IEX feed is only ~2.4% of volume. Research +
specs are saved: `References/RVOL_VOLUME_PROFILE_RESEARCH.md` (algorithm: 5-min buckets, fraction-of-day
normalize, ~20-day median/trimmed-mean window, exclude half-days/earnings, `RVOL(t)=today_cum/expected_cum`;
display FINALIZED = "2.3×" multiplier + color band, suppress first ~15 min, carry yesterday greyed overnight)
+ `References/SCHWAB_MARKETDATA_RESEARCH.md` (integration plan). Memory: `watchlist-rvol-feature`.

**Shipped this session (commits on `main`):**
- ✅ **Watchlist intraday series** (`0345350`) — `/api/watchlist/market` returns a per-symbol `intraday`
  array (today's RTH 5-min closes) for the phone card's real 1-day chart; fails soft to `[]`.
- ✅ **Chart pagination fix** (`83ffcbf`) — Alpaca `/v2/stocks/bars` is paginated; our fetchers never followed
  `next_page_token`, so long windows truncated to page 1 (the web 1H/180d chart's last candle was stuck at
  Feb 2). All four `brokers/alpaca.py` fetchers now paginate (50-page cap). Verified: 1H/180d 216→1069 bars.
- ✅ **Schwab market-data provider — step 1** (`0c9ac3e`) — new `MarketDataProvider` protocol (`brokers/base.py`)
  + READ-ONLY `SchwabMarketData` (`brokers/schwab_market_data.py`, **no order methods**) mapping Schwab
  `pricehistory` → `StockBar`; `get_market_data_provider()` factory resolver on `MARKET_DATA_PROVIDER`
  (**default `alpaca`** → inert until flipped). **Verified live end-to-end: SPY 66.6M / AAPL 51.9M daily vol
  vs Alpaca IEX ~1.9M = ~30-35× (real consolidated).** 5Min intraday works; `1Hour` raises (deferred). 664 tests.

**RVOL feature — BUILT + validated, NOT yet deployed (in the working tree, uncommitted) [2026-06-23 later session]:**
- ✅ **`signals/volume_profile.py`** (+ `tests/test_volume_profile.py`, 7 tests) — pure `compute_profile`
  (78 RTH 5-min buckets, fraction-of-day normalize, **median** across a 20-day window, renormalized cumfrac
  S-curve, median daily total, half-day exclusion via a "last bucket ≥76" short-session filter) +
  `rvol_now` (`today_cum / (median_daily_total × cumfrac[t])`, states warming/live/settled) + JSON profile
  store (`data/volume_profiles/*.json`, 7-day mtime staleness, `build_profile`).
- ✅ **`api/main.py`** — `/api/watchlist/market` rewired to `get_market_data_provider()` (was `_require_alpaca`);
  per-quote `rvol: {value, state}` field computed from the already-fetched 5-min batch (bumped days=2→4 for the
  overnight fallback) + cached profile; **overnight relabel** in the endpoint (clock-aware). Nightly
  `_volume_profile_refresh_loop` (boot + 24h) builds profiles for all watchlist symbols. Full suite **671 green**, ruff clean.
- ✅ **frontend** (`api.ts` `RvolInfo`+`WatchlistQuote.rvol`; `use-watchlist-market.ts` — fixed a WS-tick bug
  that dropped `rvol`; `watchlist-widget.tsx` — new **RVOL column** `2.3×` + color band yellow/amber/red,
  muted overnight, `—` while warming). `npm run typecheck` clean; `.next` production build done.
- ✅ **`.env`** `MARKET_DATA_PROVIDER=schwab` set (data path only; `BROKER` untouched). Schwab token is **fresh**
  (`auth_status` → age ~0, ~7d runway) — the token-cliff is no longer urgent.
- ✅ **Validated in-process against LIVE Schwab** for all 15 watchlist symbols: e.g. CAT 1.78×, COLA 1.61×,
  SPY 1.23×, AAPL 1.16×, NVDA 0.83×, XLF 0.70× — calibrated, self-consistent (today's RTH 5-min vs the RTH
  5-min profile). SPY profile cumfrac is the textbook S-curve (3.7% by +5min, ~47% midday, 100% close).

**⛔ ONLY remaining step = DEPLOY (restart both services).** The agent's auto-mode classifier hard-blocks the
agent from killing the prod backend AND from editing `.claude/settings.local.json` to self-grant it, so a human
must trigger it. A deploy helper exists: **`tools/serv-deploy.sh [backend|frontend|both]`** (kill MainPID →
systemd relaunch). To let the agent deploy directly, add to `.claude/settings.local.json` `permissions.allow`:
`"Bash(bash tools/serv-deploy.sh:*)"`. After restart: `curl :8000/api/watchlist/market?symbols=SPY` should show
`rvol`; check the column renders in-browser.

**NEXT after deploy (in order):**
1. **In-browser check** the RVOL column; confirm `/api/watchlist/market` serves `rvol` live. Then commit the round
   (exclude `.env`/`data/`) + update BUILD.md.
2. **Deferred from step 1b (NOT needed for RVOL):** rewire `/api/bars` + `/api/portfolio/history` to the provider
   and add **`1Hour` aggregation** (Schwab minute API tops out at 30Min → aggregate two 30-min bars) for the chart.
3. **`trading-panel.tsx` + mobile card** RVOL column (only the workspace widget got it).
4. **APNs day-6 token-expiry alert** + a headless weekly re-auth path (dad-with-a-job mitigation for the 7-day cliff).

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
