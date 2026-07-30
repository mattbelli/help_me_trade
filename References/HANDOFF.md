# Handoff — open threads (pruned 2026-07-28)

*Bootstrap for a fresh session: read this, then `CLAUDE.md` (auto-loaded), `AGENTS.md` (module map),
`References/INDEX.md` (which doc is current).*

*Contract (tense rule 2026-07-17; anti-bloat rules 2026-07-28): this file holds ONLY what is **open
right now** — pending verifies, armed experiments, decisions in flight. PAST tense lives in
`BUILD_LOG.md` (grep it) and git; when a thread closes, **delete its entry** — no archive. In-flight
build work lives on `board.json`, not here. Anti-bloat (the growth cycle happens when these are
skipped): (1) every ⏳/▶ item is an ACTION with a date — durable facts/doctrine/warnings go to a
memory, a design doc, or the BUILD_LOG row, never here; (2) any session that touches this file
first DELETES what it can prove closed (BUILD_LOG row or a live check) before adding its own; (3)
a dated verify whose date has passed is either closed out or re-dated — never left; (4) soft
budget ~150 lines: over budget = prune debt, prune before appending.*

---

## ▶ Overdue dated verifies (close out or re-date — oldest first)

- ⏳ (was Mon 07-27) flow-EOD: an `options_flow_daily` row landed for 07-27 at 16:xx ET, no 00:1x
  ET run; backfilled rows carry `close_oi IS NULL`.
- ⏳ (was Mon 07-27) IV corpus first live cron run: `iv_daily` row per universe symbol + sane
  `bars_current`; then 07-28: OI backfill lands on 07-27's rows (OI publishes a session late).
- ⏳ (was Mon 07-27+) re-run `tools/option_spread_probe.py` INTRADAY before recording the spread
  gate (prereg fixed: Saturday-close CHEAP would be decisive, PROHIBITIVE not; the ~3.2× cohort
  ratio is the robust part).
- ⏳ (was 07-28 open) books MOO probe `exec_e22a1bd20a39` (F, 1sh, market DAY, resting since
  Friday): filled at Monday's open ⇒ paper-MOO queued-DAY fix holds; expired ⇒ fix wrong, needs
  submit-at-the-open. Then confirm stop_guard placed the resting GTC within a poll tick.
- ⏳ chart prewarm: `/health` bars_prewarm `lag_s` stays well under the shortest TTL across a few
  sessions (`chart-bars-prewarm-storms`).

## ▶ 2026-07-27 Postgres migration — design BANKED, execution PAUSED on trigger

Trigger (instrumented): db_error-attributed dropped sweeps, `database is locked`, or sustained
multi-second slow-txn queuing → execute `References/POSTGRES_MIGRATION_2026-07-27.md` as written
(§12 Round 0 first), no re-litigating. Card `postgres-migration-decision` (backlog). Open:
- Pull forward regardless: backup watchdog fails OPEN on unreadable backup age
  (`api/jobs.py:189-190` — must fail closed) + `.env` chmod 600.
- When triggered: open the `daily_bars` schema-leak and `rule_events` FK-orphan pre-cutover cards.

## ▶ Whole-market flow corpus — v6 landed; discovery + live builds open

Corpus state: 251/251 sessions at v6 (`multileg_source=codes`), doctrine recorded in ROADMAP 1.8,
Addendum D–F, and memories (multileg fence reaches single names; orders-not-prints for N; one BBO
snapshot per sweep). Open threads only:
- **⏳ Live package-awareness build** (board notes 28s/28u) — priors now measured at v6;
  1.7/1.10 and the 1.11+ discovery program are unblocked on trustworthy labels.
- **⏳ Phase 2 OI backfill running** (launched 07-28, `opra-oi-backfill.service`, ~9.5 h): check
  07-29 that `data/databento/opra_oi/` holds ~251 npz files and the log shows no cost-gate abort;
  then build 2.3 (reconcile prints vs OI deltas — aggregate open-vs-close, never per-print) and
  2.4 (re-grade the Phase 1 signal on the split). 2.1 facts recorded in ROADMAP §2.1. Also 07-29:
  cross-check `2026-07-17.npz` vs `options_flow_daily.close_oi` date 07-17 (both = 07-16 close
  positions; broker chain pipe vs statistics pipe — same OCC number, validates both decodes).
  NOT runnable today: no concurrent ALL_SYMBOLS pulls while the backfill grinds.
- **⏳ 2.3 running across the corpus** (`oi-reconcile.service`, log `data/oi_reconcile.log`, ~8s
  per session): confirm 07-29 it covered every consecutive OI pair and the flag rates stayed near
  the smoke (clean ~98.6%). Numbers + the three bugs are recorded in ROADMAP §2.3 and the
  BUILD_LOG row. THEN 2.4 — which needs its OWN pre-registered pass/fail before it runs, and may
  use `f_open` **cross-sectionally only** (its >0.5 level is an artifact of OI decaying via
  exercise/assignment/expiry, not a finding).
- **⏳ o band → §4 classifier sequencing** (§3c PASSED out-of-sample 3.33×, 07-28): o is a
  feature awaiting siblings (Phase 2 OI deltas, Phase 3 hedge prints); no standalone surface, no
  grading without prereg; later generalization = 3+leg clusters (~37–43% of package premium).
- **⏳ Live flow pairing Inc 2 — SHADOW IS LIVE since 2026-07-28 21:02 UTC** (dev tree pulled to
  `8cf8008`; the code was already on main via PR #133. `OPTIONS_FLOW_PAIRING_SHADOW=1` in dev-tree
  `.env`; collector PID 1186654; wildcard ack `wide=True`, no warnings, RSS 406 MB at start).
  ⏳ NEXT (07-29, after a full session): read shadow stats — wire lag both sides, enrich ≤1s
  fraction, per-root d-vs-a coverage, RSS across the open burst (measured ceiling ~1 GB at
  MAX_PENDING 2M) — plus a full-day session replay for open-burst RSS. Watch for the matcher-reset
  epoch lines on any Alpaca re-subscribe (keepalive flaps are 40–48/day on this account).
  Inc 3 cutover still needs the standing-NBBO consumer audit. No code-family scoring before
  1.8-on-truth conclusions are folded in.
  ⚠️ Restarting this unit needs no sudo: it is `Restart=always` + `User=mbelli`, so `kill <MainPID>`
  is a clean restart (interactive sudo is unavailable to agents on serv).
- **⏳ Flow perception surface — SCHEDULED (nightly cron live), alerts unarmed, no UI widget** (BUILD_LOG
  07-29 rows; card `flow-perception-ship`). `tools/flow_perception_nightly.sh` (cron 04:30 UTC Tue–Sat)
  now rolls the corpus + runs the scan after the capture chain; live dry-run smoke done (4,060 rows /
  2,366 symbols on 2026-07-24). Open: founder arms `alerts.flow_perception_alerts_enabled` (recommend
  ≥3 components or top-N — ≥2 yields ~451 candidates/session), UI widget, and earnings-calendar
  coverage widening (own thread, agent dispatched 07-29).
- **⏳ Confirmatory abstention run** (now unblocked — corpus rolled): re-measure direction-rule
  abstention across the ~250 unexamined sessions vs the 20–35% band; outside the band is a
  finding, not a reason to retune; report the three abstain states separately.
- **⏳ Deferred until a consumer exists:** wire `by_measure` calls into the idea ledger so grading
  can rank the three weightings ordinally.
- **⏳ Two known guard holes** (deliberate, close later): 16:00–16:15 ET index-settlement window
  unmonitored (equity `is_market_open` flips False at 16:00; ~31% of premium settles there);
  truncation detection is size-only with a hard ceiling — durable fix = assert last record ~16:15 ET.
- **⏳ Equity-bar coverage gaps** feed ROADMAP 1.4/1.5: live names (CMA/PSTG/MMC/CTRA/CFLT/MPW/AZN)
  have no bars → no moneyness; adjusted contracts (WOLF1 etc.) inflate `no_spot_$` via the
  hardcoded 100 multiplier. 1.4 must read the per-root breakdown, not the bucket.

## ▶ 2026-07-26 audit round — remaining open cards

Findings live on cards; six of nine closed. Still open: `chat-dead-ts-surface` (dead TS brain incl.
an unguarded `propose_order` endpoint) · `copilot-memory-store-empty` (0 active memories, embeddings
backfill never ran) · `fusion-unminted-dots` (3 of 4 minted 07-28; `rvol_ex_prints` remains).
REASON/DECIDE wall-down was proven live Monday (TSLA/XLE admissions); the `REASON_EMIT_ENABLED`
flip is founder-gated on the z-vs-outcome audit (~08-05, see fusion section).

## ▶ Fusion — Horizon Redesign Round 5 + measurement follow-ups

Lane DARK (`REASON_EMIT_ENABLED=False`) until Round 5. Open:
- ⏳ ≈2026-08-05: T=3.5 revisit (recount T tables on print-price-era + flow-list-era dots) AND the
  z-vs-outcome audit from the 07-28 integrity round — together they feed the REASON_EMIT decision.
- founder dials open: thesis/trigger half-lives, conviction bar, trigger TTLs — changes land as
  founder-visible diffs, never silent.
- NEXT round: position-watch (entry-snapshot divergence alerts, notify-never-act) → Round 5 ARM.
- ⚠ full pytest suite live-fires outbound HTTPS (suite-hang + WAF risk) — needs a network-marking
  round.
- wider-stop sibling lane + index-underlying spot (SPX/NDX/RUT/VIX unstampable) deferred on the
  `fusion-mech-measurement-integrity` card.

## ▶ Chart load perf — founder eyeballs open

PR #102 deployed + machine-verified 07-23. Open: founder verifies in-browser (warm paints on
timeframe switches); ETH-off charts still paint pre/post — make `ext=false` truly RTH-only in
`_build_bars_response` (mind half-days), next UI round.

## ▶ Per-symbol vol model — shipped disabled; ARMING is the open thread

Double-gated off (`volatility_enabled` + per-strategy `vol_target`). Open:
- ⚠ two split-mandate Opus reviewers owed BEFORE arming (not before commit) — point them at the
  clamp/fail-soft paths and halt guard, not estimator math.
- rollout order fixed: shadow → paper opt-in on ONE strategy → perception dot (own card).
- shadow output graded against outcomes before arming anything (`calibration-counts-everything`).

## ▶ LLM paper books — ARMED + LIVE; increments open

Spec `LLM_BOOKS_PROGRAM_SPEC_2026-07-23.md` (header amendments override body). Open:
- ⏳ first fully-armed nightly cycles end-to-end at 17:05/17:20/17:40 ET (proposals straight from
  the cycle, no replay).
- MOO probe outcome — see overdue list.
- increment 5: shorts unlock (own adversarially-reviewed round; `_propose_advanced` hard-blocks
  shorts until it ships).
- ⚠ NEXT increment before analysts lean on GTC styles: order-management verbs (cancel/replace for
  standing GTC entries + resting stops); until then analysts request cancels in `holds_review`,
  founder acts manually — the nightly pack's open-orders section is the review surface.
- Lab book positions section honest-empty (per-account positions endpoint); session-book research
  depth v2.

## ▶ Waiting on founder

- ⚠ global `allow_short_selling=true` in prod settings — broader than the per-strategy book
  authorization (any approved sell with no held long can short-to-open on paper; books + stops
  excluded, chat/manual not). Confirm or narrow when increment 5 builds the per-strategy flag.
- Multi-angle reasoning A/B go/no-go (`MULTI_ANGLE_REASONING_AB_2026-07-19.md`).
- ⚠ subscribe the phone to the ntfy dead-man topic (topic string in crontab) — pages go nowhere
  until then.
- Overnight 07-18 harvest + draft PR #100 — judge together.
- Classification-lab addenda sign-off (DTE-cut addendum + F3 direction-semantics stamp).
- `braveapi` engine signup for SearXNG (likely unnecessary at 30 results without it).

## ▶ Options IV corpus — verifies + the two premium-selling gates

Settled science lives in the three preregs + memories (rank gate unsupported; level real but absent
on our 37 names). Open:
- cron + OI verifies — see overdue list.
- spread gate: intraday re-run — see overdue list.
- the capped-payoff gate is UNTOUCHED by all measurement so far (seller keeps ≤ premium, left tail
  uncapped; C4 band 29.7% negative, p05 −0.3105) — nothing may open a selling lane past it.

## ▶ Options flow classification scorer — gates likely cleared, scorer unbuilt

Prereg = LAW (`CLASSIFICATION_LAB_PREREG_2026-07-16.md`). SPY/QQQ/IWM legs cleared ≥5 ordinary
sessions ≈07-24; SPX complex ≈07-29. Build the scorer once clocks are confirmed clear. Deferred
audit findings F3/F4/F8 ride the `index-0dte-concentration-study` / gauge cards.

## ▶ Backend composition — Rank-2 open

Rank-2 = exit-engine own cadence (in-process, off the `_poll_once` tail); `trading/`-adjacent →
max depth + two-reviewer adversarial review. NOTE: the audit argues AGAINST extracting the order
path out-of-process — resolve against the old rung-3 note at design time.

## ▶ Exit engine — first real proof 2026-07-29

7 earnings_drift positions must auto-sell 2026-07-29. Verify throttle marks (`exit_engine.py:390`)
don't delay the open fill; then fix the amc `td==1` docstring (order fills via next-cycle retries
after the open, not after-close queueing — RiskEngine's no-live-quote gate rejects after-close).
Sibling unguarded fallbacks tracked on `data-correctness-m-tail`.
