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

## ▶ Chart load perf — fixes #1+#2 in draft PR; #3 open

From the chart-perf audit (watchlist charts loaded slowly on non-default timeframes: only the 1Day view
was prewarmed; other timeframes were cold on first view). Draft PR (card `chart-bars-incremental-refresh`;
BUILD_LOG 2026-07-23 rows) ships **#1 incremental fetch-since-last-bar refresh** (warm refreshes merge a
small trailing window, no full re-walk) and **#2 broadened prewarm** (broadcast the K most-recent
timeframes across the whole watchlist — bounded by K + a `_BARS_PREWARM_MAX_ENTRIES` cap; safe only
because #1 made warm refreshes cheap). ⏳ deploy (`tools/serv-deploy.sh backend`) + founder verify; watch
`/health` bars_prewarm `lag_s` stays well under the shortest TTL (storm invariant, `chart-bars-prewarm-storms`);
`_BARS_HOT_SPECS_MAX` / `_BARS_PREWARM_MAX_ENTRIES` are the tuning dials. **Still open:**
- #3 — persist the frontend `bars-cache` (sessionStorage/IndexedDB) so a full page reload doesn't drop
  every chart back to cold (frontend-only; independent of #1/#2).

## ▶ Waiting on founder

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
