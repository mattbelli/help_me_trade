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

## ▶ 2026-08-06 Schwab re-auth — the verdict on the Akamai hardening

Token is healthy (minted 08-01 13:28Z, cliff **2026-08-08**). serv's IP is still edge-blocked from
`sws-gateway.schwab.com`; six anti-detection fixes shipped 08-01 but **none tested against Schwab**.
Diagnosis + the full fix list live on card `schwab-reauth-akamai-block` and the 08-01 BUILD_LOG row.

- **⏳ 2026-08-06 — cron's first real attempt** (it skips while the token is under 5 days). Check
  `data/reauth.log`. Success = the hardening cleared the block. Failure now reports **"Akamai
  edge-blocked this IP"** with a `Reference #` instead of accusing the password — if you see a
  credential error instead, the guard itself is wrong.
- **⏳ If it fails:** `python -m tools.schwab_manual_exchange --start`, open the URL on an unblocked
  machine, paste the `127.0.0.1:8182?code=...` back **fast** (Schwab codes expire in minutes), then
  `--finish '<url>' --restart-backend`. Must land before the **08-08** cliff.
- ⚠️ **`data/schwab_reauth_profile` is load-bearing** — deleting it makes the next run look like a
  brand-new device, which is the signature we just spent the round removing.

---

## ▶ Book position protection — stops place AND release; deploy is the open action

Both prior alarms here are closed and their entries deleted per the tense rule: `stop-guard-never-fires`
shipped and the guard fired live 07-30, and `resting-stop-locks-the-position` shipped 08-01. Verified
live 08-01: AMZN 50 @ `acct_alpaca_fable` and GOOGL 61 @ `acct_alpaca_deepseek` each carry their GTC
stop (233.00 / 314.90), and both were locked at `available` 0.

- **⏳ First real exit is the verify.** Deployed 08-01 16:16 UTC (backend PID 2093943, frontend
  2094116, both collectors); the release path has never run against a live broker. Watch the first
  book exit on AMZN/GOOGL: expect the stop cancelled, the market exit filled, and either a residual
  stop at the same trigger or an inbox note saying nothing was left to stop. Any CRITICAL
  "UNPROTECTED position" push means a failure path fired — read it, it names which.
- **⏳ `sell-reservation-snapshot-window`** (card, `next`, high) — pre-existing, reproduced twice this
  round: for a sell-to-close every exposure gate is skipped, so the snapshot's `open_orders` is the
  ONLY quantity gate, and two sells inside one 20s poll window can oversell. The release path holds
  the submit lock to a placed exit and re-polls after, so it is the one sell route that closes its
  own window; every other one is open.
- **⏳ `unprotected-position-monitor`** (card, `next`, high) — now also owns the specific hole that a
  dead stop row removes an entry from the guard's work list for good. ⚠️ The obvious fix was built,
  reviewed and REVERTED 08-01 for three measured reasons (dry_run re-submits forever; a hand cancel
  ping-pongs uncapped and can exhaust `max_daily_orders`; re-protection is sized from the entry, not
  the position) — read the card before re-attempting. The chokepoint pages critically instead.
- **⚠️ Timing is load-bearing, not cosmetic:** Alpaca 422s a stop whose trigger is already breached
  (*"stop price must be less than current price"*), so the guard must place on fill while price is
  still above the stop. After a gap-down it **cannot** place one at all — "re-place it each morning"
  is not a workaround. The release path's residual re-stop carries the same gate.
- **Settled 07-31:** `ext` sizing stays at PARITY (founder cleared the decision without a change) —
  the overnight window is an accepted cost, not an open question. Sizing is no longer a lever here.
- Then `book-accounts-invisible-in-cockpit`, then `ext-hours-soft-stop` (data gate already passes —
  the 24/7 poll already carries a live overnight mark).

---

## ▶ Overdue dated verifies (close out or re-date — oldest first)

- ⚠ **2026-07-30 `test_idea_tracker.py::test_tiebreak_stop_wins_same_bar` is FLAKY, not broken** —
  it failed (grades `lost=0`) during the data-wiring round and passes now both in isolation and in a
  full 3,036-test run. Intermittent means shared state leaks between tests, which is *worse* than a
  hard failure: the GRADING_TIEBREAK law (a bar touching stop AND target grades LOST) is only
  sometimes guarded, and a green suite is no longer proof. ⏳ Find the leak (suspect a shared DB path
  or module-level cache, cf. the two known CI shared-DB flakes) — do not close on a passing run.

- ⏳ (was Mon 07-27) flow-EOD: an `options_flow_daily` row landed for 07-27 at 16:xx ET, no 00:1x
  ET run; backfilled rows carry `close_oi IS NULL`.
- ⏳ (was Mon 07-27) IV corpus first live cron run: `iv_daily` row per universe symbol + sane
  `bars_current`; then 07-28: OI backfill lands on 07-27's rows (OI publishes a session late).
- ⏳ (was 07-28 open) books MOO probe `exec_e22a1bd20a39` (F, 1sh, market DAY, resting since
  Friday): filled at Monday's open ⇒ paper-MOO queued-DAY fix holds; expired ⇒ fix wrong, needs
  submit-at-the-open. The stop_guard half is no longer blocked — the guard ships and has fired.
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
- **⏳ Live flow pairing Inc 2 — shadow session #1 (2026-07-29) CLEAN; Inc 3 gates remain.**
  Full-day numbers (60s stats line, end of day): dbn=11.71M prints, **matched 98.0%**, expired
  0.89% (no twin ever), orphans 20,063 (0.17%); **enrich ≤100ms = 86.6%, ≤1s = 98.6%** (the
  match-first answer); pending drained to 0, keys_peak 476k of the 2M cap, evicted 0; wire lag
  p50=p95=0ms both sides; **1 reconnect/reset all day** (ping_timeout 60s tamed the 40–48/day
  keepalive flaps); tee_err=alp_err=0; RSS 1,653 MB end-of-day, unit peak 1.9 GB (above the
  ~1 GB estimate — fine, but check against unit limits before cutover).
  **Inc 3 cutover gates, in order:** (1) shadow session #2 rides 07-30 free; (2) the
  **standing-NBBO consumer audit** — NOT run yet, the real blocker (everything that silently
  reads the Alpaca quote lane's standing NBBO must be found before that lane turns off);
  (3) explain the **SPXW/XSP d<a gap** (dbn 1.538M vs alp 1.608M SPXW prints — likely the known
  ~4-min meta-cold open blind; confirm, don't assume). No code-family scoring before
  1.8-on-truth conclusions are folded in.
  ⚠️ Restarting this unit needs no sudo: it is `Restart=always` + `User=mbelli`, so `kill <MainPID>`
  is a clean restart (interactive sudo is unavailable to agents on serv).
- **⏳ Flow perception surface — SCHEDULED (cron 04:30 UTC Tue–Sat), earnings coverage SHIPPED,
  alerts unarmed, no UI widget** (BUILD_LOG 07-29/07-30 rows; card `flow-perception-ship`).
  Nightly wrapper = corpus roll → earnings coverage refresh (non-fatal) → scan. Earnings estimates
  land as `estimated=1`/`edgar_estimate` — money-path readers (`next_earnings_date`) stay
  confirmed-only BY DESIGN; `earnings_phrase()` renders the estimate caveat on every surface.
  ⏳ Open: **first full ~3.9k-name coverage sweep** on serv (wrapper does it incrementally at
  04:30, or run `tools/earnings_coverage_refresh.py` manually — ~9 min); founder arms
  `alerts.flow_perception_alerts_enabled` (recommend ≥3 components or top-N — ≥2 yields ~451
  candidates/session); UI widget; watchlist fundamentals panel still shows confirmed dates only
  (surfacing estimates there needs a UI label first).
- **⏳ 1.14g verdict LANDED 07-30 (BUILD_LOG row) — follow-ups unblocked:** PRODUCT = NOT
  DEMOTED (all three components clear their negligible bands after RV-stratification); SCIENCE =
  no survival authority (top-RV-quintile balance failed; benchmark (d) leans "mostly RV in
  disguise"). Open: (a) the letter "F" was consumed 07-28 — the judging-rules amendment is now
  **Addendum G IS LAW (v12, 2026-07-31 — BUILD_LOG row has the gauntlet record**; card
  `addendum-g-judging-rules`). **Pre-run obligations 1+2+3/3b ALL DISCHARGED 07-31** (BUILD_LOG
  rows: `FLOW_1_14F_FINDINGS_REGISTRATION.md` IS LAW v5, 17-blocker gauntlet; G.8 root-
  classification snapshot FROZEN + bar backfill; obligation-3 registered run COMPLETE — Q5 null
  USABLE, f −0.87pt BELOW f₀, **DEAD (dilution) is the near-certain scoring-run verdict**).
  Remaining obligations 5→6 are **calendar-blocked by G.10 itself** (they need bars through
  d+10, so they inherit the ≥ close-of-2026-08-07 bound) — nothing on this path is runnable
  before then; (b) RESOLVED 07-30 — founder PARKED the
  flow perception surface (card `flow-perception-ship`, backlog); a vol/volume abnormality
  scanner is carded instead (`vol-abnormality-scanner`, next); (c) `short_dte_share` grain
  contradiction stays parked.
- **⏳ H1 quiet-tape — implementation BUILT + FROZEN 07-31 (H1.6 #2 discharged both halves;
  BUILD_LOG row has the full review record; FREEZE HASH `a2cf65f` — the registered
  implementation; any later change to `tools/h1_quiet_tape.py` = dated addendum).**
  `tools/h1_quiet_tape.py` + 40 tests; oi_reconcile extended through 07-29 (07-24 coverage
  81.4% — judgment window keeps 37 dates). Open, in order: ⏳ ≥ 2026-08-08: `--stage record`
  (#3) then `--stage calibrate` (#4) on the frozen code — BOTH refuse earlier in code; SPY +
  equity bars must be topped up through d+10 first (pure acquisition); ⏳ `--stage score`
  (#5) STRICTLY AFTER the Addendum G verdict is recorded (pass the verdict artifact via
  `--g-verdict`); any code change before #5 = dated addendum + re-freeze.
- ✅ Confirmatory abstention run COMPLETE 07-30 (BUILD_LOG row): 19.97% pooled — 0.03pt below
  the band, recorded as the registered finding. Thread closed.
- **⏳ Phase 3 (dealer hedge footprint): 3.1 + 3.2 DONE (3.2 verdict FOOTPRINT-AMBIGUOUS,
  direction-without-size — BUILD_LOG rows + ROADMAP result blocks are the record). 3.3
  REGISTERED 2026-08-01, founder-blessed "Ok lets run it", labels FROZEN** (SHA
  `104c184e…a089e57`, HIGH 25 / NULL 84 / ANTI 8, `tools/hedge_footprint_freeze.py`).
  Measurement BUILT (`tools/hedge_label_drift.py` + 177 tests, seal locks in code).
  **Gate 1 FAILED on both populations — the registered ladder is EXHAUSTED: NO-RUN,
  forward returns NEVER unsealed. 3.3 is DORMANT-ACCRUING** (ROADMAP Amendments 1–3:
  narrow 0.18 → wide 0.29 power at the 1.5% floor; MDE 3.0%; binding leg =
  m-residualized; HIGH accrues ≈3–4/month → meaningful re-gate ~a year out; any earlier
  re-test = NEW registration, disclosed weaker). Wide artifacts:
  `hedge_footprint_wide/equity/` (250 events frozen, SHA `c87592df…97264f`).
  ⚠️ Standing seals: forward returns SEALED (no gate has passed); frozen writers refuse
  overwrite — treat a SHA mismatch as an incident. ⚠️ If a future re-gate passes: deep
  review of the unseal/analyze paths of `hedge_label_drift.py` MUST complete first
  (gate1 path reviewed; the rest built+tested, not yet line-by-line reviewed).
  ⏳ **OPEN: backfill label scoring → new frozen labels → re-gate** — phase B stages 1+2
  COMPLETE 2026-08-02 (`tools/flow_backfill_phaseb.py`, parity-gated 925/927 vs the
  corpus with 0 extras; 6,656/6,656 windows → 942 stage-1 records → **923 stage-2
  events**, 729 primary; BUILD_LOG 2026-08-02 phase-B row + ROADMAP round-3 block = the
  record). Slippage check MEASURED: yield flat to the purchase boundary = SATURATED;
  **tranche 2 justified but DEFERRED** — label-score the 923 first (free), re-gate, and
  only buy tranche 2 (exact quote first, ≤$30 ceiling) if power still fails. Open items:
  (1) label scoring on the underlying equity tape over the 923 events (mirror the 3.3
  label pipeline; outputs stay sealed exactly as the corpus's); (2) new frozen label set
  via dated addendum — TWO parallel Opus adversarial reviewers BEFORE freeze
  (`review-agents-opus`); (3) re-run Gate 1 at the grown HIGH stratum; if it passes,
  the line-by-line unseal/analyze review of `hedge_label_drift.py` MUST complete before
  any unseal (standing).
  ⏳ **Next Phase-3 move: 3.4 spread orientation** — NOT gated on 3.3 (needs only 3.2's
  CONFIRMED direction result); unlocks the ~33% of premium fenced as spreads. Needs its
  own pre-registration round (founder-visible). No collision with the live-pairing
  thread (other agent).
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
backfill never ran) · `fusion-unminted-dots` (4 of 4 BUILT 07-28; `rvol_ex_prints` awaits its deploy —
see fusion section).
REASON/DECIDE wall-down was proven live Monday (TSLA/XLE admissions); the `REASON_EMIT_ENABLED`
flip is founder-gated on the z-vs-outcome audit (~08-05, see fusion section).

## ▶ Fusion — Horizon Redesign Round 5 + measurement follow-ups

**⚠️ TWO DEPLOYS OWED, and they must land on SEPARATE cohort dates** (news moves catalyst reach UP, the
attention frozen-peak fix moves legacy scores DOWN up to −3.5 — together the score step is
uninterpretable; rationale in the two 07-28 BUILD_LOG rows). 07-29 pre-open was missed:
- ⚠️⚠️ **THE RAM-UPGRADE REBOOT (server down 07-29 evening) DEPLOYS NEWS BY ITSELF — no deploy step is
  owed.** All four `hmt-*` units are `enabled` at boot and the dev tree already contains `8cf8008`
  (PR #134), so the first post-upgrade backend start runs the news lane. **FIRST JOB ON THE WAY BACK:
  record that start time (`systemctl show hmt-backend -p ActiveEnterTimestamp`) — that timestamp, not
  07-30, IS the news cohort boundary.** If the box returns mid-session, that day is a PARTIAL cohort:
  label it and never use it as a baseline. (Pre-upgrade the backend had not restarted since 07-28
  12:33 UTC, so everything before the reboot is clean measurement-round data.)
- ▶ **PR #135** (rvol_ex_prints + attention fix) is `CONFLICTING` — merge main in, re-verify (full suite
  + ruff), merge, then deploy on the session AFTER news has had one full clean day. Never the same
  cohort date as news.
- ⏳ after the news deploy (RTH): counters `news_dot_minted` / `news_backfill_minted` /
  `news_roundup_zeroed` / `news_no_open_episode`, and catalyst-group share should rise materially.
- ⏳ day-2 after the rvol deploy: `rvol_dots` counter; the per-pass write-cost benchmark the review asked
  for (never done); attention scores stepping DOWN = **the correction, not signal**. Watch the one-time
  attention migration — it full-scans ~591k `evidence_dots`, so keep it off the lock-starve path.
- ⏳ next in this lane: filings → fusion dots — needs `ingest_seq` on `filings_events` plus a founder call
  on insider-buy direction (`none` like news, or a deliberate exception). Congress rows stale since 07-02.

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

- ⏸ **2026-07-30 Matt + LLM PAUSED** — account verified empty (0 positions). Founder wants this as a
  CHAT interaction, not a nightly pass; deliverable is on card `matt-llm-belongs-in-chat` (pull
  `build_book_pack` into a chat turn, then decide whether the book/journal/report-kind retire).
- ⏸ **2026-07-30 Claude Panel PAUSED** (cron line commented + `BOOKS['panel'].paused=True`). Solo books still run.
  - ⚠ `acct_alpaca_open` positions now get NO nightly hold/trim review. Resting GTC stops still cap
    downside; nothing trims winners or reacts to a broken thesis. Decide: review manually, let the exit
    engine own it, or resume the panel.

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
- ⏳ **2026-07-30 tonight: confirm the seats actually USE the perception CLI.** The books were
  blind (no perception access at all) until `tools/perception_query.py` was wired into
  `book_session.sh --allowedTools` + both prompts. This assumes Agent-spawned seats inherit the
  session allowlist — likely (the 07-29 scrapers used session-level WebFetch/WebSearch) but
  UNVERIFIED. Check tonight's transcripts for `perception_query.py` calls and for a drop in
  web-scrape sub-agents; if seats can't run it, the pack must carry the data instead.
- ⏳ **2026-07-30 forced tool-choice was NOT ported and is now gone from the code.** The retired TS
  route forced `toolChoice: propose_order` on an order turn; `ai/chat/loop.py` is `tool_choice="auto"`
  throughout. The intent prompt block says the same thing in prose, so this is belt-and-braces — but if
  "[Calling propose_order...]" narration reappears in chat, this is the first suspect. Porting it needs
  per-provider handling (DeepSeek thinking models 400 on `tool_choice`).

## ▶ Waiting on founder

- ⚠ global `allow_short_selling=true` in prod settings — broader than the per-strategy book
  authorization (any approved sell with no held long can short-to-open on paper; books + stops
  excluded, chat/manual not). Confirm or narrow when increment 5 builds the per-strategy flag.
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

## ▶ Options flow classification scorer — ✅ REGISTERED RUN COMPLETE 07-30, thread closed

Zero viable cells across all 10 method×variants; per the prereg's conclusion clause the paid
paired feed is justified — the 07-26 Databento tcbbo subscription stands validated by
pre-registered measurement (BUILD_LOG row; card `classification-lab-scorer`, done). Deferred
audit findings F3/F4/F8 still ride `index-0dte-concentration-study` / gauge cards.

## ▶ Backend composition — Rank-2 open

Rank-2 = exit-engine own cadence (in-process, off the `_poll_once` tail); `trading/`-adjacent →
max depth + two-reviewer adversarial review. NOTE: the audit argues AGAINST extracting the order
path out-of-process — resolve against the old rung-3 note at design time.

## ▶ Exit engine — first real proof 2026-07-29

7 earnings_drift positions must auto-sell 2026-07-29. Verify throttle marks (`exit_engine.py:390`)
don't delay the open fill; then fix the amc `td==1` docstring (order fills via next-cycle retries
after the open, not after-close queueing — RiskEngine's no-live-quote gate rejects after-close).
Sibling unguarded fallbacks tracked on `data-correctness-m-tail`.
