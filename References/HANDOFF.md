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

## NEXT (pick up here)
- **Real Norgate artifacts** (offline-on-Windows training → `git add -f research/artifacts/*.json`) — makes the
  win% trustworthy (dev artifacts are Alpaca/survivor-biased, gitignored).
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
