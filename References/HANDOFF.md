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

## ▶ 2026-07-30 ⚠️ LIVE UNPROTECTED POSITION — stop_guard has never fired

DeepSeek book **GOOGL 61 @ 327.00** (`exec_7958562468e9`, filled 07-27 18:25 ET) is holding with
**zero open orders at the broker** — its 314.90 stop was never placed, and the guard has placed
**nothing since it shipped**. Full diagnosis + the measured Alpaca constraint matrix live on the cards;
only the actions are here.

- **⏳ Fix `stop-guard-never-fires` first** (card, `next`, critical). ~One line — `account_id` is
  dropped by `db/paper_orders.py:_row_to_proposal()`, so `trading/stop_guard.py:118` skips every
  candidate. Needs an **account-scoped** regression test; the existing tests pass `account_id=None`
  and never exercise the filter.
- **⚠️ Timing is load-bearing, not cosmetic:** Alpaca 422s a stop whose trigger is already breached
  (*"stop price must be less than current price"*), so the guard must place on fill while price is
  still above the stop. After a gap-down it **cannot** place one at all — "re-place it each morning"
  is not a workaround.
- **⏳ A second entry is resting NOW:** `paper_9fa4e9a31332` (Fable, GOOGL 54 @ 328, expires 07-30
  19:59) will repeat this on fill. Check it before assuming the blast radius is one position.
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
- ⏳ (was Mon 07-27+) re-run `tools/option_spread_probe.py` INTRADAY before recording the spread
  gate (prereg fixed: Saturday-close CHEAP would be decisive, PROHIBITIVE not; the ~3.2× cohort
  ratio is the robust part).
- ⏳ (was 07-28 open) books MOO probe `exec_e22a1bd20a39` (F, 1sh, market DAY, resting since
  Friday): filled at Monday's open ⇒ paper-MOO queued-DAY fix holds; expired ⇒ fix wrong, needs
  submit-at-the-open. The stop_guard half of this verify is blocked until `stop-guard-never-fires`
  lands (see the 07-30 section) — it cannot pass today.
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
- **⏳ 1.14f intraday screen — REGISTERED (46b1049, SEED=20260808), outcome store not yet
  fetched.** Sequence is LAW (fetch-to-the-spec): (1) bars backfill TOOL (agent building
  `tools/equity_bars_5min_backfill.py` on a worktree branch, 07-30 — review + merge like the
  earnings round); (2) run the FULL backfill (Alpaca SIP 5-min RTH, ~4.5–6.3k names, full corpus
  span, ~10–15 GB) — **deliberately AFTER the 07-30 serv RAM upgrade**, nice'd, off market hours;
  (3) build `tools/mover_intraday.py` per the registered spec; (4) single run, result recorded
  whatever it shows. Geometry probe (07-24 session, counts only) justified eligibility ≥20
  fenced prints/window and the boundary-of-day peer cell — recorded in the registration.
- **⏳ serv RAM upgrade 07-30 (+32 GB, 2 sticks now, 2 more later)** — post-boot verify, then
  DELETE this thread: `hmt-backend`, `hmt-flow-collector` (system unit, auto-starts; shadow flag
  stays on in dev-tree `.env`), frontend :3001, tailnet up, `crontab -l` intact (02:30 capture /
  04:30 perception nightly), `free -h` shows the new total. Nothing needs manual restart.
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
