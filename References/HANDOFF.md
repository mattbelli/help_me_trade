# Handoff — open threads (2026-07-26)

*Bootstrap for a fresh session: read this, then `CLAUDE.md` (auto-loaded), `AGENTS.md` (module map),
`References/INDEX.md` (which doc is current).*

*Contract (tense rule, adopted 2026-07-17): this file holds ONLY what is **open right now** — pending
verifies, armed experiments, decisions in flight. PAST tense lives in `BUILD_LOG.md` (one dense row per
round — grep it) and git; don't re-narrate shipped work here beyond one line + a pointer. When a thread
closes, **delete its entry** — there is no archive. In-flight build work lives on `board.json`, not here.*

---

## ▶ 2026-07-26 whole-market flow corpus — backfill IN FLIGHT, deadline is real

Databento Standard is live; whole-market OPRA `tcbbo` is $0.00 inside a **12-month window that rolls
daily**. Detail is on the `whole-market-flow-corpus` card and the BUILD_LOG row — only the open items live here.

- **⏳ Backfill running** — restarted 07-26 19:47 via `tools/opra_backfill_run.sh` (setsid-detached so it
  outlives the session, `nice`+`ionice`, **defs THEN tcbbo sequentially**). Progress: `tail
  data/databento/run.log`. **If it dies, restart it** — it is resumable and every day left unarchived
  ages out of the free window into a purchase. Watch for `ABORTING RUN` in the log: that is the per-day
  cost guard refusing to spend, and it means a day fell outside the window. Do **not** run the two
  schemas concurrently; two ALL_SYMBOLS pulls plus numpy-heavy agents is what swapped serv flat.
- **⚠️ Sweeps get ONE quote, not one per leg** (measured 07-26, see BUILD_LOG). The BBO is a single
  pre-sweep snapshot per `(instrument_id, ts_event)`: 99.4% of multi-print groups carry an identical
  quote. Right reference for signing direction, but **N legs are not N independent quote observations**
  and book-walking depth cannot be read off the attached BBO. Anything that weights by print count
  across a sweep needs to know this.
- **✅ Classifier audited** (`tools/flow_classifier_audit.py`, post-join). Midpoint rule signs **87.83%**
  (buy 42.29% / sell 45.54%); 10.06% print exactly at mid and stay unsigned; the strict/touch rule signs
  only 61.9%. Premium is clean at $26,310,928,243.
- **✅ Direction rule REGISTERED + CORRECTED** — Addendum C, corrected by **Addendum D** (C left in the
  record deliberately; D is appended, not an in-place edit). The rule and the 20–35% band are
  **unchanged**; what changed is why they are trusted. ⚠️ **The three measures are NOT three votes** —
  `sign(u)` equals the midpoint side by construction (`half > 0` under `good_q`), so all three share
  every print's sign and differ only in weight (`w=1` / `min(|u|,1)` / `|u|`). Their agreement tests
  **only** that direction survives the weighting choice. **A 2-of-3 majority is incoherent here**, not
  merely unregistered — it counts one opinion twice; the majority-vote `dissenters()` helper is deleted.
  Abstains now carry a state: `no_opinion` / `weighting_unstable` / `contested`, reported by premium
  share and **never pooled**. ⚠️ Robustness checks stay **premium-weighted**; raw-vs-clip disagreement is
  0.24% of buckets but 15.8% of premium. **`u_premium` is pre-registered — add measures beside it, never
  redefine it.**
- **⚠️⚠️ MULTI-LEG CONTAMINATION — the biggest live caveat on this corpus.** A spread leg is priced off
  the package, not its own NBBO, so its midpoint side is unreliable — and unreliable in **all three**
  measures, since they share one sign. Measured 2025-07-28: **12.96% of prints, 33.33% of premium**, and
  excluding them **flips the session sign of every measure** (net +$553.6M → −$219.3M, clip +$95.4M →
  −$84.3M, raw +$366.4M → −$176.5M). Now a **grain** (`multileg` in the rollup PK, `schema_version=3`),
  so both arms are stored and neither can be chosen after seeing the answer. **No bucket-level reading —
  directional or abstain — may be presented without both arms.** The flag is a heuristic (shared
  `ts_event`, different instrument): OPRA `flags` is constant 194 across all 6.95M prints and marks
  nothing (`tools/tcbbo_flags_probe.py`), but `ts_event` is true-ns so ~1 coincidence is expected per
  session.
  **▸ DE-ESCALATED 07-27 by roadmap 1.8 — it is an index/ETF artifact.** Split three ways
  (`tools/multileg_arm_probe.py`): **single names flip 0/3** measures (10.9% of prints / 19.5% of
  premium multi-leg), **index roots + ETFs flip 3/3** (16.5% / 48.5%). Institutional hedging is
  overwhelmingly spread structures; single-name flow is not. The both-arms presentation rule above
  **still stands** — but the alarm does not reach the single-name flow the 3–45 day thesis targets.
  ⚠️ One session, in-sample; re-run across the corpus. Related, and it matters for the Phase 4
  aggregate's denominator: single names were net **short** (−$354M) while index/ETF was net **long**
  (+$908M) the same session, so pooling them cancels two opposite signals.
- **⚠️ `orders`, NEVER `prints`, is the N for any z-score or standard error** (`schema_version=4`).
  A sweep arrives as N prints of one order; counting it N times inflates √N by up to 1.051× at p90,
  worst in thin heavily-swept names (TLRY 16.0%, SNAP 15.0%) — the exact tier measurement 1.7 is
  about. `prints` is retained unchanged because it is pre-registered. Do **not** sum `orders` across
  buckets for a session total: a sweep whose legs disagree on side is counted in each bucket it
  touches, so the sum runs 0.13% above the true group count. Correct per bucket, which is the grain
  every statistic uses.
- **⏳ CONFIRMATORY, once the backfill lands:** the rule was chosen after seeing 2025-07-28, so that
  session is in-sample. Re-measure abstention across the ~258 unexamined sessions and report where it
  falls against the 20–35% band — **outside the band is a finding, not a reason to retune.** Report the
  three abstain states separately; no sub-bands are declared and none may be invented from 07-28.
- **⏳ Study round owns one follow-up:** wire `by_measure` calls into the idea ledger so grading can rank
  the three weightings ordinally — deliberately not built now (no consumer yet; a hook into nothing is
  dead code), but the calls are emitted from the start because the comparison cannot be run
  retroactively.
- **Minor, before the 260-session run:** line 123 `to_ndarray()` does **no chunking** — it materializes a
  whole session (~1.87 GB peak RSS) per iteration, and the 260-session run is unattended.
- **⏳ Rollup not yet run across the corpus** — verified on 2025-07-28 only (13s/session, 186,750 rows
  at `schema_version=4`). Run `tools/flow_corpus_rollup.py` once the files land, then re-run
  `tools/flow_corpus_bars.py` so the full universe (not just one session's) has spot.
  **The schema is FROZEN at v4** — 1.1/1.2/1.3 were the schema-affecting fixes and all three have
  landed, so this is one pass (~1 hour for 260 sessions), not two. Adding a column after this point
  costs a second full re-roll.
  **Watch `no_spot_$` in the log during the unattended run.** It counts only the `no_spot_missing`
  arm — ordinary names that should have had an equity bar. **Measured over 37 sessions: $54M–$574M,
  median $123M** (the one-session ~$65M figure was not representative). An index root missing from
  `INDEX_ROOTS` would read **billions** — index flow is ~$10B/session — so the alarm line is orders
  of magnitude clear of normal variation.
  ⚠️ **BOTH SENTENCES ABOVE WERE WRONG AND ARE CORRECTED — 2026-07-27 review.** Measured over 138
  sessions the bucket runs **$41.3M–$7,196.6M, mean $256.0M**, so the "billions ⇒ missed index root"
  discriminator is **already saturated and cannot identify a missed root**: the covered roots are
  VIX $89.8M / XSP $31.9M / RUT $94.9M / NDX $234M per session, all well inside the bucket's own swing,
  and only SPX ($9,891M) would clear it. The tail is **adjusted contracts** inflated by the hardcoded
  100 multiplier (roadmap 1.5) — WOLF1 alone is $7,082M on 2025-12-19.
  ⚠️ **It is also NOT a clean delisting list** — calling it a "corporate-action detector" was an
  overclaim. It mixes four unrelated causes: adjusted contracts (WOLF1, OPEN1, BABA2), ticker-format
  breaks (BRKB, $3.8B over 110 sessions), genuine corporate actions (WBA, PARA, SATS), and **plain
  equity-bar coverage gaps on live, actively traded names** — CMA, PSTG, MMC, CTRA, CFLT, MPW appear in
  **all 110** of their sessions and AZN has no bars in range at all. **Those live names currently get
  no moneyness whatsoever in the corpus**, which is a data gap to close, not a corporate action.
  **Roadmap 1.4 must read the per-root breakdown**, not treat this bucket as its list.
- **⏳ ONE KNOWN BACKFILL GAP: `2025-09-02`.** Checked 07-27 at 89/260 — every other processed
  session is on disk, and the two 0-byte files (`2025-09-01` Labor Day, `2025-11-27` Thanksgiving)
  are correct recorded holidays. **Remedy: re-run `tools/opra_tcbbo_backfill.py` over the same range
  after the current pass finishes** — the skip guard is `exists() and size > 0`, so a second pass
  fetches only what is absent. Cause was a leftover `.part` from a 502-storm process death: the
  Databento client validates the write path before contacting the server, so a stale partial made
  the day fail forever for a reason unrelated to the download. Fixed 07-27 (the `.part` is now
  cleared *before* the request, not only after a caught failure), but the running process predates
  the fix, **so verify the gap set again when it lands.**
- **⏳ THREE SESSIONS HAVE NO DEFINITION FILE: `2025-08-07`, `2026-04-28`, `2026-07-09`.** Found by
  the new gap check, 07-27 — the defs pass reported "258/260 done" and this was invisible behind it.
  **A session without definitions cannot be rolled at all** (`_load_definitions` returns None and the
  rollup logs *"no definitions — cannot attribute prints"*), so all three would have silently never
  entered the corpus. All three causes are **transient, not systematic**: a stale `.part`
  (2025-08-07, now fixed), `BentoError: Response ended prematurely` (2026-04-28) and a failed
  `get_cost` call (2026-07-09). **The backfill has no retry — one blip drops a session permanently**,
  3 lost out of ~520 pulls. Remedy is a second pass (definitions are L0/free, $0.00, and unlike
  tcbbo they carry **no rolling deadline**); deliberately NOT run concurrently with the in-flight
  tcbbo pass, because two simultaneous ALL_SYMBOLS streams are what swamped the box on 07-26.
- **✅ ROADMAP 0.1 SHIPPED 07-27 — the live lane can now tell a dead feed from a quiet market.**
  `FeedLiveness` in `dbn_stream.py`, judged on **records, not `is_connected()`** — the failure that
  matters is a socket that still reports healthy while nothing arrives. Three faults kept separate
  because they need different responses: **subscription never acked** (raises; the only check valid
  outside RTH, since Databento sends symbol mappings on subscribe regardless of market activity),
  **transport dead** (no records for 90s during RTH → raises `FeedDead`, well inside the roadmap's
  2-minute budget), and **pipeline dead** (records arriving, no prints for 300s → logs loudly but
  does **not** raise, because tearing down a healthy socket is the wrong fix for a symbology/meta
  problem). Plus a prints/min counter so a feed dying is visible before any threshold trips. RTH
  comes from the existing holiday- and half-day-aware `trading.market_hours.is_market_open` (15 tests).
  ⚠️ A closed-market tick **resets** the silence clocks — without that the first tick after the
  opening bell compares against last session's timestamp and reports a dead feed on a healthy one.
  ⚠️ **Corrected same day by review, and the correction matters more than the feature.** The first
  version judged silence on `msgs`, which databento increments for **gateway heartbeats too** (every
  30s), so it could never fire — a watchdog that manufactured confidence. Liveness now runs on
  `data_msgs` (records carrying a book level). Also: a hard socket kill is already caught at ~41s by
  databento's own monitor flipping `is_connected()`, so this clock's unique value is narrower —
  gateway-side data stalls and wedged callback threads.
  ⚠️ **The ack check raises only during RTH.** Off-hours it logs. `OPTIONS_FLOW_EXTERNAL_COLLECTOR=1`
  means `hmt-flow-collector` owns the stream and awaits legs `FIRST_COMPLETED`, so **any raise exits
  the process and systemd restarts it in 3s** — and the premise that symbol mappings arrive off-hours
  is still UNVERIFIED, so raising then would have been an unbounded weekend restart loop.
  ⏳ Still unproven against a real socket: the arming check remains verifying the provider against a
  session whose ground truth we already hold. **Verify the off-hours ack behaviour at the same time.**
- **The Databento lane has no quote store at all any more** (fixed 07-27, see the BUILD_LOG row) — the
  NBBO rides on the print as `nbbo`, and `sweep_loop` reads `p["nbbo"] or stream.quote_at(...)`. Keep it
  that way: `dbn_stream.quote_at` returns `None` by design and re-growing a store re-opens a thread race,
  a false `quote_basis="dbn_nbbo"` label on same-millisecond prints, and an unevicted ring. The Alpaca
  lane is unchanged and still classifies through `quote_at`.
- **⚠️ Two known holes in the new guards, both deliberate and both worth closing later.** (1) The
  16:00–16:15 ET index-settlement window is **unmonitored** — `is_market_open()` is the equity session
  and flips False at 16:00, so `idle()` resets the clocks, yet the backfill deliberately captures to
  16:15 because index options settle there and carry ~31% of corpus premium. (2) Truncation detection
  is size-only and **has a hard ceiling**: real median 264.8 MB, smallest legitimate session Christmas
  Eve at 0.469×, so the 0.35 fraction cannot rise past ~0.44 and the 0.35–0.47 band is invisible at any
  threshold. The durable fix is temporal — assert the file's last record reaches ~16:15 ET.
- **▶ `tools/flow_corpus_gaps.py` (roadmap 0.2, shipped 07-27) is how you check any of this.**
  Exits 1 on any gap so it can be a cron. Three checks: absent files, files present but not rolled at
  the current `SCHEMA_VERSION`, and **truncated pulls** — a partial download passes the backfill's
  `exists() and size > 0` skip guard and is skipped forever, which has now bitten this project twice
  (the eight 7 KB definition files from the UTC-window bug, and 2025-09-02's 64.5 MB against a
  ~270 MB median). The trading calendar is derived from `equity_bars`, not hardcoded, so it cannot
  drift out of sync with the corpus it checks.
- **✅ CLOSED — the unresolved prints are one index instrument.** 0.35% of prints (24,298 on 2025-07-28)
  are all `instrument_id` 5930, `raw_symbol` BBX, `instrument_class` 'K' (INDEX), UNDEF strike/expiry.
  `_load_definitions` keeps only 'C'/'P', so they never reach `d_ids` and the join drops them. Correct —
  they are not options. ⚠️ They also carry `size == UINT32_MAX`, so the C/P filter is the **only** thing
  stopping `premium = price/PX * size * 100` from inflating a session ~918,000,000x ($26.3B → $24.2
  quintillion). Do **not** relax the join to "recover" unresolved prints without a size-sentinel guard.
- **Index roots have no spot by construction** (SPX/SPXW/NDX/VIX/RUT = 30.8% of premium, `mny_bucket`
  = 'unknown'). The prereg's secondary arm excludes index roots, so this is acceptable — but it is a
  decision, not an oversight, and a SPY×10-style proxy is the option if that changes.
- **Do NOT build** the anchor-seeding / hot-pool / promotion tuning for the 1,000-sub cap. It was queued
  before the subscription and is now dead work.

## ▶ 2026-07-26 audit round — four Opus investigators; findings are ON THE BOARD, not here

Four parallel audits (perception layer, idea engine + fusion, AI chat, all 64 non-done cards) ran against
**live serv state**, not just code. Every load-bearing claim below was re-verified by the orchestrator
before being written down. **Nine new cards carry the findings** — this section exists only so the
threads are not lost; read the cards for detail, and delete this section once they are worked.

**★ The structural finding — the wall is down; ⏳ the live proof is Monday.** REASON/DECIDE had never
executed once (`reason_runs` = 0 all-time) because a dead gate ran ahead of its replacement, not because
of `REASON_EMIT_ENABLED`. Removed 07-26 (see the BUILD_LOG row); the offline replay puts **1–3
investigations/session against a budget of 5**. **Open:** confirm on the Monday **2026-07-27** tape that
`fusion_investigations` takes new rows and the `fusion_would_investigate` counter moves — the fix ships
on unit tests plus a historical replay, and the tape closed Friday 16:04 ET. **Then it is the founder's
call whether to flip `REASON_EMIT_ENABLED`** (a queue with members is the precondition, not the
decision). The four dead subsystems downstream (dossier — `idea_dossiers` does not exist live; the REASON
lane; idea arming — `lifecycle_state` NULL on all 1,400 rows; attention idea-tiers) stay dark until that
flip.

✅ **DEPLOYED 2026-07-26** — `BeerBelly69/clear-the-board` ff-merged to `main` at `9e39914`, frontend
rebuilt in the main checkout (`BUILD_ID` mtime moved, served `/api/version` buildId matches), all four
units restarted with new MainPIDs, backend booted with zero tracebacks. Live-verified on the new
process: SearXNG → 8586, pacing 1.5s, and `fetch_url` extracting the article ("Record quarterly revenue
of $22.1 billion…") on a content page while correctly falling back to the tag strip on a link-list page.
So the Monday checks below now exercise the new code.

**Live bugs still open, all verified, each with a card:**
`chat-dead-ts-surface` (dead TS brain, incl. an endpoint that runs `propose_order` with no approval
ledger) · `copilot-memory-store-empty` (P0–P3 shipped, **0 active memories**, embeddings backfill never
ran) · `fusion-unminted-dots` (4 registered components minted by nothing). *(Six of the nine are closed
— snapshot account mismatch, the win% honesty leak, setup-scan idempotency, the contested wall, the
frozen flow baseline, and the web-blind copilot; see the 2026-07-26 BUILD_LOG rows.)*

✅ **The copilot's web arm is live again** — our own `hmt-searxng` on **8586** (`deploy/searxng/`),
backend restarted, `.env` *and* `data/settings.json` both repointed, end-to-end verified: 30 results
across startpage/bing/duckduckgo/mojeek. Odysseus's dead 8585 was deliberately left untouched — that
project waits on its healthcheck. **Open, founder's call only:** the `braveapi` engine is the
sanctioned path to brave (it throttles this IP and always will), but it needs a signup and attaches
queries to an account — likely unnecessary at 30 results without it.

⏳ **Monday 2026-07-27, flow-EOD:** confirm the 16:xx ET run lands an `options_flow_daily` row for
07-27 and that no 00:1x ET run appears. The 5 missing sessions are backfilled, but backfilled rows are
a **different measurement** than native ones (~7% of native summed volume; `close_oi IS NULL` marks
them) — the baseline window self-heals to all-native ~20 sessions after deploy.

**Chat safety invariant re-verified INTACT** — no broker toolset in the registry, the loop returns
without executing on `APPROVAL_TOOLS`, web executes only after an atomic single-consume hash-bound
claim, mobile diverts to a PENDING phone proposal, chat cannot arm a trade rule. The one side door is
the dead endpoint on `chat-dead-ts-surface`.

⚠️ **Two memories were corrected by this round** — pushes are NOT dead (75 sent in 3 days; the budget
gates one lane, not the phone), and SearXNG has a third failure mode (container DNS).

⏳ **Watch, do not yet conclude:** `fusion_mech` measures **11 won / 107 lost** vs a 33.3% breakeven,
`llm_conviction` 0/8. n=118 resolved against **977 still open**, over one rough week for longs — which
is exactly the tape that produces "0 of 107 losses reached target". Re-read on a fuller sample.

---

## ▶ Fusion Horizon Redesign — Rounds 1–4 live dark; verifies + Round 5 open

Direction blessed 07-21, Rounds 1–4 shipped 07-22 (`FUSION_HORIZON_REDESIGN_2026-07-21.md` — header
amendments override its body; detail: BUILD_LOG 2026-07-21/22 rows). Lane stays DARK
(`REASON_EMIT_ENABLED=False`) until Round 5. **Open:**
- ⏳ next trading session (07-23): Round-3 migration rebuilds the live table clean on the
  deploy-restart (legacy episodes → closed/migrated; `fusion_lifecycle` + `fusion_lifecycle_eod` in
  /health; first sweep closes nothing wrongly) · Desk shows Z+Net, radar ranks by z · collector polls
  exactly the flow list + hot lane (pre-pickup stragglers CBOE/NTSK washed out of day-state overnight;
  SPY/TSLA carry real intraday flow all session) · weekly PWH/PWL present in the levels cache
  (spot-check, 07-21 producer add)
- ⏳ ≈2026-08-05: T=3.5 revisit — recount the T tables on print-price-era + flow-list-era dots before
  moving the dial (`FUSION_Z_GATE_T`)
- founder dials open: thesis/trigger half-lives, conviction bar, trigger TTLs — every change lands as
  a founder-visible diff, never silent
- NEXT round: **position-watch** (entry-snapshot divergence alerts — reversal / contested / evaporated /
  continuation — surfacing in Desk needs-you; notify-never-act) → then **Round 5 ARM** (ARMING
  CHECKLIST rewrite, T-gate rewiring, push-restore = founder decision there)
- ⚠ the full pytest suite live-fires outbound HTTPS from tests (incl. Akamai-fronted hosts) — the
  suite-hang class AND a WAF risk; needs a network-marking round
- REASON v2 A/B stays founder-gated (multi-angle, below)
- acceptance drills when convenient: rejected proposal → `user_action='skipped'` + still grades;
  broken parser pages within one cycle; sub-gate idea renders "unproven" (no number) on phone

## ▶ Chart load perf — PR #102 DEPLOYED + machine-verified; founder eyeballs open

Merged (double adversarial pass, 3 review fixes) + deployed 07-23 16:07 ET; machine verify green
(bars_prewarm lag 5.5s on the 15s interval, WAF journal quiet, futures 1Week + SPY 1Day zero dupes).
Detail: BUILD_LOG 2026-07-23 rows; deferred LOWs on card `chart-bars-incremental-refresh`. **Open:**
- founder verifies charts in-browser (timeframe switches on not-yet-viewed symbols should paint warm)
- ⏳ next few sessions: `/health` bars_prewarm `lag_s` stays well under the shortest TTL (storm
  invariant, `chart-bars-prewarm-storms`); `_BARS_HOT_SPECS_MAX` / `_BARS_PREWARM_MAX_ENTRIES` are the dials.
- ETH-off charts still paint pre/post (4am–8pm) — `extended=false` only skips the boats leg, so an
  ETH-off chart mimics ETH-on until it freezes at 7:55pm (misled the founder 07-23 as "no overnight
  candles"). Make ext=false actually RTH-only in `_build_bars_response` (mind half-days); next UI round.

## ▶ Per-symbol vol model — shipped disabled; ARMING is the open thread

v1 EWMA estimator + vol-targeted sizing landed 07-25 (BUILD_LOG row; card `per-symbol-volatility`).
Double-gated off: global `volatility_enabled` **and** per-strategy `strategies.vol_target`, resizing
needs both. **Open:**
- ⚠ the mandated **two split-mandate Opus reviewers** are owed **before arming**, not before commit —
  the multiplier lands in `signals/strategy_scan.py` on the notional before `trading/` sees it, so no
  code under `trading/` changed this round and nothing can move an order until two switches flip.
  Point them at the clamp/fail-soft paths and the halt guard, not at the estimator math (measured).
- rollout order is fixed: shadow (master ON, every strategy OFF → logs `vol_regime`/`size_multiplier`,
  resizes nothing) → paper opt-in on ONE strategy → perception dot in the UI (own card, not this one).
- shadow output is calibration fuel — grade the logged multipliers against outcomes before arming
  anything for real (`calibration-counts-everything`).

## ▶ LLM paper books — ARMED + LIVE (07-23 late); shorts unlock open

Spec + amendments: `LLM_BOOKS_PROGRAM_SPEC_2026-07-23.md` (header amendments override body — see
amendment 4, the arming-night execution round); card `llm-books-program`; detail in BUILD_LOG
2026-07-23 rows. Founder ordered all three books armed + analysts rerun + Lab wired 07-23 evening.
Live now: after-hours `entry_style="moo"` proposals (TTL = next session close), **paper MOOs use a
queued market DAY order** (Alpaca paper can't fill `opg` — no opening auction; fixed 07-24, see that
BUILD_LOG row; live still uses opg), `trading/stop_guard.py` resting GTC stops after entry fills
(advanced picks REQUIRE numeric stop + self-sized notional), Lab book pages (lanes + per-seat + report).
**Open:**
- ⏳ VERIFY at the NEXT close→open (paper-TIF fix unproven end-to-end): the 07-24 opening MOOs
  (GOOGL/deepseek, MU/fable) both **expired unfilled** — sent `opg`, which Alpaca paper never fills.
  Fix routes paper MOOs to market `day`; tests assert only the TIF string, so confirm a real overnight
  book MOO actually **fills at the open** and stop_guard then places the resting GTC stop within a poll
  tick (`journalctl -u hmt-backend | grep "stop guard"`). (Book accounts are cap-exempt now — the old
  "$65k GOOGL should risk_block" expectation is dead; founder removed per-trade caps for books 07-23.)
  PR #106 merged + backend deployed 07-25 01:15 UTC. A **controlled probe is already resting** for
  the Monday open, so the answer does not depend on a book proposing anything: `exec_e22a1bd20a39`
  (F, 1 sh, STRAT1, broker `34a1b38c`) fired through the real chokepoint with `entry_style="moo"` and
  sits `type=market, tif=day, status=accepted`. Filled at the open ⇒ the queued-DAY assumption holds;
  expired ⇒ the fix is wrong and paper MOO needs submit-at-the-open instead of overnight queueing.
  No history proved this before: all 19 prior paper fills were submitted *during* RTH.
- ⏳ tomorrow 17:05/17:20/17:40 ET: first fully-armed nightly cycles end-to-end (proposals fire
  straight from the cycle, no replay)
- increment 5 (shorts unlock — own adversarially-reviewed round; `allow_short_selling` toggle is
  ON but `_propose_advanced` hard-blocks shorts until the round ships); session-book research
  depth v2 (read-only API access beyond the pack); Lab book positions section still honest-empty
  (per-account positions endpoint — consider mobile agent's `source=snapshots` history for charts)
- ⚠ NEXT INCREMENT (before analysts lean on GTC styles): **order-management verbs** — the
  decision block needs cancel/replace for standing GTC entry orders AND resting stops (stop
  lifecycle v2 folds in here). Until it ships: analysts request cancels in `holds_review`,
  founder acts manually; a GTC limit nobody re-affirms can fill days later on a stale thesis —
  the nightly pack's open-orders section is the review surface
- entry-type choice live 07-24 (moo | limit GTC | ext GTC+extended_hours incl. 24/5 overnight);
  ext fills can land at 3am — stop guard places the resting stop then, but stops only EXECUTE
  in regular hours (gap exposure stated in the prompts; the analysts own that trade-off)

## ▶ Waiting on founder

- ⚠ **global `allow_short_selling=true` in prod settings** (surfaced by the 07-23 arming-night
  review): predates tonight and is BROADER than the per-strategy book authorization — any approved
  sell with no held long can short-to-open on paper (books + protective stops are now excluded;
  chat/manual proposals are not). Confirm it stays, or narrow to the per-strategy flag when
  increment 5 builds it
- **Multi-angle reasoning A/B go/no-go** — design ready + double-audited
  (`MULTI_ANGLE_REASONING_AB_2026-07-19.md`, board `multi-angle-reasoning-ab`). On go: one focused
  build round + review
- ⚠ **subscribe to the ntfy dead-man topic** (topic string in crontab): the check itself is proven —
  canary receipts "Idea Engine alive" 07-20/21/22 in `data/deadman.log` — but a failure page goes
  nowhere until the phone subscribes
- **Overnight 07-18 harvest** (`research/overnight_2026-07-18/`, map `NIGHT_LEDGER.md`): Strategy Lab
  duel synthesis + roadmap designs await judgment; that round's tree edits are parked UNREVIEWED on
  draft **PR #100** — judge together
- **Classification-lab addenda sign-off**: proposed DTE-cut addendum + F3 direction-semantics stamp

## ▶ Options IV corpus — sampler shipped; first live run + the IV-rank study are open

Forward arm built 07-25 (BUILD_LOG row; `dolthub-options-history` card). Cron installed on serv at
20:50 UTC weekdays, file-guarded so it no-ops until this branch lands in the main tree. **Open:**
- ⏳ **first live run Mon 07-27**: confirm `data/options_chain_history.db` gets an `iv_daily` row per
  universe symbol and a sane `bars_current` (a sweep firing before the vendor rolls its daily bars
  shows volume collapsing to zero everywhere — that's what the counter is there to distinguish).
  Then ⏳ **07-28**: confirm the OI backfill lands on 07-27's rows, since OI publishes a session late.
- SPX/SPXW will never carry OI here (index roots aren't tradable on Alpaca ⇒ no contracts API). The
  Databento OPRA statistics purchase covers them; don't "fix" it by inventing a substitute.
**Studies A, B and C all RAN 07-25 and are closed** — three BUILD_LOG rows, three preregs in
`References/` (`IV_RANK_BASELINE_*`, `IV_LEVEL_*`, `IV_HORIZON_BREADTH_*`). Settled, do not
re-litigate: an **IV-rank entry gate is not supported** (rank is a level effect in costume; holding
level fixed it vanishes and *reverses* in low-vol names); the **level** effect is real and survives
every control thrown at it (Spearman +1.000, band +0.0445 at the corrected horizon); and on the
**37 names we would actually trade there is no VRP at all** (−0.0012, unchanged across both
horizons). What is still OPEN from that work:
- **Two gates stand between us and any premium selling. One is now measured, one is untouched.**
  - ⏳ **The spread gate — probed 07-25, but NOT yet a finding, and the rule that says so is
    pre-registered.** `OPTION_SPREAD_GATE_PREREG_2026-07-25.md` + BUILD_LOG row. Band names cost
    **0.0870 vol pts** to cross once — **195% of the +0.0445 edge** — against **0.0292** for our own
    names; i.e. the premium-carrying names are **~3.2× more expensive to trade** (31.2% vs 9.8% of
    premium), confirming the competition hypothesis in direction. **But it ran Saturday on Friday's
    closing book, and the prereg fixed in advance that CHEAP would be decisive while PROHIBITIVE
    would not.** ⏳ **Re-run `tools/option_spread_probe.py` intraday (Mon 07-27+)** before recording
    anything. The *ratio* between cohorts survives the caveat better than the absolute levels — both
    were measured in the same instant.
  - **The capped payoff — untouched by any of this.** A seller keeps at most the premium while the
    left tail is uncapped, so mean-VRP in vol points overstates capture however it is trimmed. In
    the C4 band 29.7% of observations are negative with p05 −0.3105. Nothing measured so far speaks
    to this, and a tight spread would not have opened the lane either.
- ⚠️ Do NOT reuse Study B's **B5** as written — it removed 31.6% of the top decile, not 5%, because
  VRP correlates with the sort axis; outlier-robustness is **untested, not disproven** (a symmetric
  trim leaves +0.0586).
- ⚠️ Study C's **C6 flag is recorded but hollow** — its premise ("a live bar API cannot serve a
  delisted ticker") is false; BBBY/WOLF/FRC/SIVB all have full bar history. It rests on 5 symbols
  and points the *safe* way. Don't spend effort "fixing" survivorship on the strength of that flag.

## ▶ Options flow — classification scorer unbuilt; gates clocking

Prereg = LAW (`CLASSIFICATION_LAB_PREREG_2026-07-16.md` — never improvise metrics on the data). The
chain sampler's per-expiry fix first runs on the 07-23 cron (BUILD_LOG 2026-07-22) — ⏳ verify 07-23:
per-root composition sane, SPXW dailies actually captured. Gate clocks: SPY/QQQ/IWM legs reach ≥5
ordinary sessions ≈07-24; SPX complex restarted at zero ≈07-29. Scorer gets built once gates clear.
Deferred audit findings F3/F4/F8 ride the `index-0dte-concentration-study` / gauge cards.

## ▶ Backend composition — Rank-2 open

Audit + step-1 fixes: BUILD_LOG 2026-07-18. R1a extraction live/inert; the R1b worker split was
CANCELLED by founder 07-23 (BUILD_LOG row) — backend keeps owning the jobs. **Open:** Rank-2
**exit-engine own cadence** — in-process, off the `_poll_once` tail; `trading/`-adjacent → max depth +
adversarial review; NOTE the audit argues AGAINST extracting the order path out-of-process (conflicts
with the old rung-3 "extract trading loops" note — resolve at design time). Schwab jobs stay
in-process behind config gates (Rank-3, no action).

## ▶ Exit engine — first real proof 2026-07-29

Fix detail: BUILD_LOG 2026-07-16. **Open:** 7 earnings_drift positions must auto-sell on 2026-07-29;
sibling unguarded fallbacks tracked on `data-correctness-m-tail`. P1 (07-22 half-day review —
pre-existing, no regression): the amc `td==1` docstring claims the after-close day-TIF order "queues
to the report-day open", but RiskEngine's no-live-quote gate REJECTS after-close submissions — the
fill really lands via next-cycle retries once the open has a quote. Same destination, different
mechanism; verify against the 07-29 proof (throttle marks at `exit_engine.py:390` must not delay the
open fill), then fix the docstring to match reality.
