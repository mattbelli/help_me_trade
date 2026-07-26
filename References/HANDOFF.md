# Handoff — open threads (2026-07-26)

*Bootstrap for a fresh session: read this, then `CLAUDE.md` (auto-loaded), `AGENTS.md` (module map),
`References/INDEX.md` (which doc is current).*

*Contract (tense rule, adopted 2026-07-17): this file holds ONLY what is **open right now** — pending
verifies, armed experiments, decisions in flight. PAST tense lives in `BUILD_LOG.md` (one dense row per
round — grep it) and git; don't re-narrate shipped work here beyond one line + a pointer. When a thread
closes, **delete its entry** — there is no archive. In-flight build work lives on `board.json`, not here.*

---

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

⚠️ **NOT DEPLOYED — both Monday-dated items above and below depend on this.** serv runs
`/home/mbelli/dev/help_me_trade` on `main` (`7e97aa1`); the 07-26 audit fixes are on
`BeerBelly69/clear-the-board`. Until that branch is merged and deployed, Monday 07-27 runs the old
gate and the old flow-EOD schedule, and neither Monday check means anything.

**Live bugs still open, all verified, each with a card:**
`chat-dead-ts-surface` (dead TS brain, incl. an endpoint that runs `propose_order` with no approval
ledger) · `copilot-memory-store-empty` (P0–P3 shipped, **0 active memories**, embeddings backfill never
ran) · `fusion-unminted-dots` (4 registered components minted by nothing). *(Six of the nine are closed
— snapshot account mismatch, the win% honesty leak, setup-scan idempotency, the contested wall, the
frozen flow baseline, and the web-blind copilot; see the 2026-07-26 BUILD_LOG rows.)*

⏳ **One command, needs an interactive sudo — the copilot is still web-blind until it runs:**
`sudo systemctl restart hmt-backend`. We now run our own SearXNG on **8586** (`hmt-searxng`, up and
returning 17 results; odysseus's dead 8585 was deliberately left untouched — that project waits on its
healthcheck) and serv's `.env` already points at it, but `get_searxng_url()` reads `os.environ` first,
so the live process keeps using 8585 until it restarts.

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
