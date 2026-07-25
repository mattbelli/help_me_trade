# Handoff — open threads (2026-07-23)

*Bootstrap for a fresh session: read this, then `CLAUDE.md` (auto-loaded), `AGENTS.md` (module map),
`References/INDEX.md` (which doc is current).*

*Contract (tense rule, adopted 2026-07-17): this file holds ONLY what is **open right now** — pending
verifies, armed experiments, decisions in flight. PAST tense lives in `BUILD_LOG.md` (one dense row per
round — grep it) and git; don't re-narrate shipped work here beyond one line + a pointer. When a thread
closes, **delete its entry** — there is no archive. In-flight build work lives on `board.json`, not here.*

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

## ▶ Kronos path-prior probe — NIGHTLY leg dead (07-25); 5-min leg still pending

**The 1h/next-session variant is answered and closed — see the BUILD_LOG 2026-07-25 row and
`NIGHTLY_README.md`. Both legs FAILED** (path: Kronos ~14% *worse* log-loss than the bootstrap,
CI [+0.115,+0.197]; vol: beats neither EWMA nor GARCH). Leakage window deliberately not run —
pre-registered as conditional on a path PASS. Do **not** re-run it with a fresh calibration; that
is the laundering the pre-registration exists to prevent. Two carry-forward facts: **`calibrate.py`
samples one symbol and that did not generalise** (T=0.7 read 1.00x on AAPL, universe median 0.741) —
calibrate across symbols if this harness is reused; and cost is **1.08 s/eval point on the 4090**,
so a nightly job would be ~4 GPU-minutes — cost was never the blocker, decision quality was.
Independent of Kronos: **GARCH beat EWMA at 1h/next-session** (+0.1338, CI [+0.0339,+0.2405]),
which does *not* settle `per-symbol-volatility` — that card defers on **daily** bars.

**Still open below: the original 5-minute probe.**

Isolated experiment in `research/kronos_probe/` (pre-registration + removal instructions in its
README; card `kronos-path-prior-probe`; build detail in the BUILD_LOG 2026-07-24 row). Asks one
**two** questions off the same rollouts, scored and reported independently: (1) does Kronos's
*conditional* path distribution beat a vol-matched block bootstrap at P(target before stop)?
(2) does its implied σ beat EWMA **and** GARCH(1,1) on QLIKE? **Open:**
- run `probe --window primary` then `--window leakage`, then apply the two pre-registered rules:
  path PASS iff lower log-loss AND 95% paired CI excludes 0; vol PASS iff it beats **both** baselines
  (beating only EWMA is a FAIL — the cheap baseline is the real competitor). Both FAIL → `rm -rf`.
  **Do not re-run with different barriers/horizons/symbols to find a win**, and do not treat a
  vol-win + path-FAIL as a pass — that demotes to a bake-off input on `per-symbol-volatility`.
- ⚠ runtime: ~13.5s per evaluation point (upstream re-encodes the full 512-bar context every
  generated token — no KV cache), so the full primary window is an overnight-scale run, not minutes.
  `PATH_BATCH` in `config.py` is the dial worth testing before committing to a long run.
  (The nightly leg measured 1.08 s/point at context 360 on a 4090 — most of that gap is the
  512-bar context taking upstream's `torch.roll` branch rather than the append-only one.)
- the leakage window is the diagnostic, not a second experiment: a large primary-vs-2023 gap is a
  memorization estimate, and would argue against the model even if the primary window passes.

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
