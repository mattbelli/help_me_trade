# Handoff — "you are here" (2026-06-20)

*Bootstrap for a fresh session. Read this + `AGENTS.md` (codebase map) + `BUILD.md` "Realignment" (the R1–R5
plan + dated history). Everything else is in those — this is just current focus, next steps, and gotchas.*
*Other refs: `ARCHITECTURE.md` (guided subsystem tour), `CODE_AUDIT.md` (cleanup backlog; Tier 1 done),
`WATCHLIST_SPEC.md` (parallel feature), `SERVER_SETUP.md` (migration, done), `STARTUP.md` (run + device access),
`CLAUDE.md` (mission + working agreements, auto-loaded).*

## DONE (2026-06-23): Databento deep data + Adam-as-trigger study — both threads close at BETA; setstudy stood up
Same branch (pushed, not merged). **(1) Databento `/ES` data layer** (`research/databento_loader.py`, CME
GLBX.MDP3): pulled **10y of true 1-min** (2016-06-23..2026-06-22, ~3.49M bars, `source="databento"`, $12.72,
tick-verified vs Schwab; ~$112 credits left). Estimate-before-pull is free; on-demand depth removes the
Schwab 40-day race. **(2) True-anchor recompute** (`profile_mr_swing.py weekly_anchors=` + `--anchor-source
intraday`): feeding the TRUE 1-min weekly POC into the swing test did NOT change the verdict — still
2025-dominated/6-of-11-years, avg_R degenerate (true VAL ≈ week low). The ~29pt daily-approx error wasn't
hiding alpha → **daily-swing profile-MR rejected a 4th way.** **(3) Adam-as-trigger event study**
(`research/adam_trigger_study.py`, 30 tests): extracted **135 directional $ES calls** from his full Twitter
history; entry modes post_price (realistic follower, ~96% fill) vs his_level (idealized); fixed + scaleout
(half day-1 + trailed runner) exits; slippage + always-long beta benchmark. **Finding: following him
mechanically lands at/below long-beta across all faithful variants** (entry/target 40-100/exit). Realistic
post-price entry carries a **+22pt median slippage tax** (he posts at/after entry → follower chases); his
scale-out UNDERperformed a flat 100pt target (caps runners; short runners die in the bull). **META-CONCLUSION:
his edge (if real) is discretionary read + ORDERFLOW, not price levels → use him as a discretionary guide, not
a mechanizable signal.** **(4) Setstudy** (`research/setstudy.py`): a daily review tool — his calls vs what
/ES did + a growing `setstudy_log.csv` corpus — the forward way to learn his read. **Backtest research is
WRAPPED.** Untouched lever = orderflow (TBBO, available 2010+, ~$112 credits) — deliberately deferred, not
momentum. Memories: [[es-data-archive-strategy]].

## DONE (2026-06-22): Schwab /ES data layer + profile-MR research — NO ROBUST EDGE (rejected 3 ways)
Branch `research/decision-engine-data-layer` (PC/Windows; pushed to origin, NOT merged). Tested @adaamset's
volume-profile mean-reversion method on `/ES` futures end-to-end. **Verdict: no robust alpha at daily
resolution — it's beta (long /ES in an uptrend).** Rejected three ways: (1) naive intraday MR (buy prior-day
VAL → prior-day POC, 10pt stop) = **−EV** (−3.4pt/trade, knife-catching); (2) faithful daily SWING (prior-week
POC anchor, structural prior-week-low stop, prior-week-high targets, multi-day hold) = +EV (+4.0pt, +534/16y)
BUT an **alpha-vs-beta control** (same machinery, profile entry stripped → "long first up-regime day of week")
makes ~2× the points (+766–1056) with better avg_R → the anchor adds nothing; (3) **VAL-entry variant** (buy
the dip *into* value — fixes the inverted R:R, stop ~15pt, T1 R:R 2.23) looked like alpha in aggregate
(+6.5pt, avg_R 0.365, beats control +289) but **per-year robustness REJECTS it**: beats control only 7/17
years, the entire edge is 2025 (+1168; strip it and it loses the other 16y), −484 blowup in 2022.
**Live-wiring stays PARKED behind edge-validation** (serv honors this gate; profile-MR never validated).
**Durable wins (reusable, committed):** Schwab data layer — `research/intraday_bars.py` (1-min cache + Schwab
source, 40k-cap chunking), `research/bars.py` (Schwab DAILY source: 16y `/ES`), `research/intraday_profile.py`
(RTH session profiles + prior-session reference), `research/intraday_reversion.py` + `research/profile_mr_swing.py`
(backtests + alpha-beta control), `scripts/schwab_probe_minute.py`. Schwab re-auth bug fixed
(`brokers/schwab_auth.py`). **Schwab data limits:** 1-min only ~40 days deep (40k-candle/response cap), daily
16y+. See the `es-data-archive-strategy` + `windows-gitbash-mangles-slash-futures-symbols` memories.

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

## NEXT (pick up here)
- **@adaamset research is WRAPPED — both threads = beta.** profile-MR (rejected 4 ways incl. true 1-min anchor)
  AND Adam-as-trigger (≈/below long-beta across all faithful entry/target/exit variants) show no robust alpha.
  His edge (if real) is discretionary + orderflow, NOT price levels. **Do not re-run the mechanical level
  tests.** Forward path = **the setstudy** (`research/setstudy.py` → `data/research/setstudy_log.csv`): run it
  daily to log his calls vs `/ES` outcomes and learn his read qualitatively.
  - **Setstudy daily pipeline (RESOLVED — ONE command, no LLM):** `python -m research.setstudy
    --refresh-tweets` chains pull → extract → report. Promoted to committed/tested modules:
    `research/x_pull.py` (X API v2; token in `data/research/x_token.txt` or `$X_BEARER_TOKEN`; ~$0.005/post;
    now MERGES into tweets.json by id so full history is kept) + `research/es_trigger_extract.py`
    (deterministic regex) → `es_triggers_full.json`; then the report appends the `setstudy_log.csv` corpus.
    Cost is printed before any X spend. **Caveat:** the regex extractor's DIRECTIONAL set (~119 long/short,
    what the setstudy uses) is solid, but its `entry_type` LABELS are approximate vs the one-time Sonnet
    deep-extraction (realtime≈65 vs 74, recap over-tagged, anticipatory under-tagged) — labels are cosmetic
    for the setstudy. `--refresh-tweets` REGENERATES es_triggers_full.json (regex), overwriting the richer
    LLM labels; the per-call ANALYSIS in setstudy_log.csv is label-independent and persists. Re-run the
    broad-extraction subagent periodically if you want the richer entry_type tagging back.
  - **Orderflow (TBBO) — deliberately DEFERRED, not momentum.** The one untouched dimension (where his real
    edge supposedly lives); Databento has it 2010+, ~$112 credits left. Big, noisy, overfitting-prone build —
    only pursue as a scoped decision, not a default.
  - **serv archive cron** for live 1-min Schwab is now LOW priority (Databento gives on-demand depth).
- **FORWARD-TEST on paper, not real money** (the right next gate): the strategy is long-only → fits the
  app's no-short rule → wire it as an actionable `type:"trade"` signal-rule (created DISABLED) → agent
  proposes these dip setups → phone approve → paper execution via the existing chokepoint. Compare live
  paper EV to the backtest's ~+0.5% net expectation over months before any real-money double-gate.
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
