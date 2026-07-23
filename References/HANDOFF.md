# Handoff — open threads (2026-07-17)

*Bootstrap for a fresh session: read this, then `CLAUDE.md` (auto-loaded), `AGENTS.md` (module map),
`References/INDEX.md` (which doc is current).*

*Contract (tense rule, adopted 2026-07-17): this file holds ONLY what is **open right now** — pending
verifies, armed experiments, decisions in flight. PAST tense lives in `BUILD_LOG.md` (one dense row per
round — grep it) and git; don't re-narrate shipped work here beyond one line + a pointer. When a thread
closes, **delete its entry** — there is no archive (pre-07-17 narrative: git history of
`HANDOFF_ARCHIVE.md`, removed same day).*

---

## ▶ Idea Engine + fusion build — Round 0 shipped, Phase A next (2026-07-18)

07-17: two external gpt-5.6-sol audits (see INDEX; 8/8 verified) reopened the Phase 1 gate —
its spec promises hadn't all shipped. **Round 0 fixes committed (9a15594/46d3d98/7f44ccc) and
DEPLOYED 07-18 ~00:08 ET** (detail: BUILD_LOG 2026-07-17/18). Build order + coder routing
founder-approved (gpt-5.6-sol mains / orchestrator surgical / Opus simple — this push only).
**Open:**
- ⏳ founder in-browser check: web Approve two-step confirm UX (workspace proposal card)
- ⏳ Mon 07-20 (next trading day): first fully-automatic 16:45 ET dead-man run (exec bit now in
  git); 15:00 canary + median/never-worked alarm rules run live for the first time
- ⚠️ founder must SUBSCRIBE to the ntfy dead-man topic (in crontab) or pages go nowhere
- ▶ **Phase A SHIPPED + DEPLOYED 07-18; hardened same night** (A1 `8984aac` + A2 `2a5e71d`;
  overnight audit → F1/F2/F7 ledger-hole fixes `4ac6377`; detail: BUILD_LOG 2026-07-18 ×3).
  **Observe window opens Mon 07-20** — verify: intake runs market-hours, `fusion_mech` rows
  appear, conservation balances, nothing user-visible (`FUSION_CARDS_ENABLED=False`).
  Follow-ups on `fusion-phase-a-substrate` (A1b restamp/dirty queues, retention, contested
  scoring F10). **Phase B widgets SHIPPED** (Idea Ledger + Evidence Stack, card
  `fusion-phase-b-widgets`) — founder in-browser check pending after deploy
- ▶ **Phase C: C1–C5 ALL SHIPPED dark 07-21; then the PIVOT — C6-as-specced is SUPERSEDED**
  (detail: BUILD_LOG 2026-07-21/22; C5 passed two split-mandate Opus reviews). Founder called
  the contested disease on the first live shadow session ("everything goes contested… you
  can't accept everything into one idea window") → the 3-pass converged **Fusion Horizon
  Redesign is BLESSED** (`FUSION_HORIZON_REDESIGN_2026-07-21.md`: decayed bull/bear masses +
  conviction ratio, thesis-vs-trigger evidence roles, swing-only product lane,
  armed→triggered→expired/invalidated lifecycle; Desk+Ledger two-widget UI target §7).
  Build = 5 rounds; arming (old C6) is Round 5. The lane stays DARK
  (`REASON_EMIT_ENABLED=False`) throughout. **OPEN:**
  (a) ⏳ this round's commit + deploy (noise-kill: convergence/solo notification rows dead at
  the writer + `fusion_mech` bell-leak gate; scorer v2 `score_decayed` + replay harness;
  specs) — full-suite gate in flight. Post-deploy verify: notifications widget actually
  quiet; first levels drain (`levels_stamped` vs `levels_refresh_failed`) + weekly PWH/PWL
  enter the cache at the next pre-market;
  (b) founder T pick — fresh cut-lines exist (`research/fusion_rescore/`) but are ONE-day
  thin; re-run `tools/fusion_rescore_replay.py` after a few shadow days under the new scorer,
  then present the table;
  (c) founder dials: thesis/trigger half-lives, conviction bar, trigger TTLs — every change
  lands as a diff, never silent;
  (d) intake must stamp DTE onto option dots at mint time — live role-routing is blind
  without it (replay recovers DTE via the `options_flow_events` join; live can't);
  (e) **Round 3 SHIPPED 07-22** (lifecycle + armed-idea machine; 2 split-mandate Opus reviews,
  both CONFIRMED ledger-corruption classes fixed pre-commit — as-built amendments live in the
  spec header, detail BUILD_LOG 2026-07-22). ⏳ verify next trading session: migration rebuilds
  the live table clean on deploy-restart (legacy rows → closed/migrated), `fusion_lifecycle` +
  `fusion_lifecycle_eod` appear in /health, first sweep closes nothing wrongly (episodes are
  all fresh). **Scoring-normalization Stage 1 + Stage-2 backfill BUILT 07-22** (z on
  radar/Desk, dual-gate T tables, `fusion_z_baseline` 3,310 rows — as-built header of
  `FUSION_SCORING_NORMALIZATION_2026-07-22.md`). ⏳ post-deploy live check: Desk shows Z+Net,
  radar ranks by z, QQQ-contested/XLE-armable behavior holds on the next session. T PICKED 07-22 = **3.5** ("start it with 3.5, revisit after ~10 days") — dual z-gate LIVE
  in the investigate cut-line (`FUSION_Z_GATE_T`, BUILD_LOG row); ⏳ **≈2026-08-05 revisit**:
  recount the T tables on print-price-era + flow-list-era dots before moving the dial. Unstampable tape blind spot FIXED 07-22 (print-price stamping — BUILD_LOG; ✅ verified live 14:10 ET — off-watchlist names stamp `price_source='print'`; ASTS itself joined a watchlist ~13:30 so it stamps from cache now). **Round 4 SHIPPED + DEPLOYED 07-22** (attention tiers + flow hot lane — founder directive 1 built; spec + BUILD_LOG; verified live: radar rows carry tier, `flow_hot_lane` exists empty — lane stays EMPTY until ideas arm). Flow-underlyings cap overflow RESOLVED 07-22 (founder game plan → dedicated slot-capped "flow" watchlist, BUILD_LOG row) — ⏳ verify at next open: collector polls exactly the flow list + hot lane (pre-pickup stragglers CBOE/NTSK wash out of day-state overnight), SPY/TSLA carry real intraday flow all session. NEXT: **position-watch round** (founder directive 2: entry-snapshot divergence alerts — reversal/contested/evaporated/continuation — surfacing in Desk needs-you; phone rides Round-5 push restore) → Round 5 ARM (ARMING CHECKLIST rewrite + T-pick gate rewiring + push-restore decision live there);
  (f) ⚠️ the full pytest suite live-fires outbound HTTPS from tests (incl. Akamai-fronted
  hosts) — the suite-hang class AND a WAF risk; needs a network-marking round.
  Deferred to post-ARM: `_stamp_last_price` on the minute path. REASON v2 A/B stays
  founder-gated (multi-angle card below)
- ▶ **Multi-angle reasoning A/B — design ready, awaiting founder go** (2026-07-19):
  `References/MULTI_ANGLE_REASONING_AB_2026-07-19.md`, board `multi-angle-reasoning-ab`
  (founder lane). Bull/bear/judge panel vs the single veto-grade voice; paired shadow +
  alternating control; alpha-adjudicated prereg metrics; Opus-reviewed + Grok cross-audit
  folded (B1 nights[] pair capture on dedup-skip; episode-unit adjudication; paper-prompt
  mandate). On go: one focused build round + review
- ▶ **Overnight 07-18 doc harvest awaiting founder**: `research/overnight_2026-07-18/`
  (map: `NIGHT_LEDGER.md`) — Strategy Lab design duel + synthesis (orchestrator judgment
  next), roadmap designs + critiques (news dots / nightly wrap / watchlist v3), perception
  ideation. Remaining Cursor quota burns by ~midday Sat on founder-directed rounds.
  The overnight round's TREE edits (strategy-lab entry-source fallback + chart axis work)
  are parked UNREVIEWED on **draft PR #100** (`lab/strategy-lab-overnight-0719`, parked
  07-19 to unblock the frontend deploy) — judge together with the duel synthesis
- acceptance drills when convenient: rejected proposal → `user_action='skipped'` + still grades;
  broken parser pages within one cycle; sub-gate idea renders "unproven" (no number) on phone

## ▶ Backend composition — audit verified + step 1 shipped; splits + measurement open (2026-07-18)

Zero-context composition audit of hmt-backend verified claim-by-claim and archived
(`References/BACKEND_COMPOSITION_AUDIT_2026-07-18.md` — read its header first). Step-1 fixes SHIPPED
(blind jobs registered; EOD scan / fill_outcomes / fusion ATR off-loop — BUILD_LOG 2026-07-18). **Open:**
- ▶ **Rank-2 exit-engine own cadence** — in-process, off the `_poll_once` tail; `trading/`-adjacent →
  max depth + adversarial review. NOTE: audit argues AGAINST extracting the order path out-of-process
  (conflicts with the old rung-3 "extract trading loops" note — resolve at design time)
- ✅ 07-21 CLOSED: the week of "unexplained backend TERMs" = CI's full pytest suite live-firing
  the reauth script's backend restart on every push (strace-confirmed culprit
  `test_reauth_no_skip_if_fresh_runs_flow`; fix `f47e0df` = deny-by-default `--restart-backend`
  flag + crontab + test mocks + CI un-redded). End-to-end verified: CI run for `f47e0df` GREEN
  with backend PID unchanged through it — first clean push since the runner landed. Detail:
  BUILD_LOG 2026-07-21 morning row + memory `script-mains-deny-by-default`
- Schwab jobs stay in-process behind config gates (founder leaning: Schwab is personal, not product —
  Rank-3 tier, no action now)

## ▶ Options flow — gauge verified 07-17; decomposition report + classification scoring open

07-17 OpEx verify PASSED (both windows finalized clean, single writer; print lab 599k prints
64% stream-quote truth) — detail: BUILD_LOG 2026-07-17/18. **Open:** (1) Flow Decomposition
daily report MERGED (`4392443`, Opus SHIP review) + in the 07-18 backend deploy — ⏳ verify
its first scheduled run Mon 07-20 post-close (~16:1x ET); (2) classification-lab SCORER not yet
built — metrics pre-registered in `CLASSIFICATION_LAB_PREREG_2026-07-16.md` (LAW — never
improvise on the data); proposed DTE-cut addendum + F3 direction-semantics stamp await founder
sign-off; deferred audit findings F3/F4/F8 remain on the `index-0dte-concentration-study` /
gauge cards.

## ▶ 07-17 UI round — founder in-browser check pending

Deployed live (detail: BUILD_LOG 2026-07-17): (1) positions widget Entered+Entry columns, new column
order + one alignment rule; (2) hairline dropout killed app-wide — PR #87's stranded fix landed + the
whole `--fs-title` bordered-row class swept (11 widgets), pixel-verified PASS via new
`tools/ui_hairline_probe.py`. **Open:** founder eyeballs both; rip out `70e7097`'s known non-fix
`[will-change:auto]!` in watchlist-widget.tsx (needs its own drag-compositing verify); close PR #87 as
superseded (branch `worktree-fix+watchlist-hairline-pitch`).

## ▶ Workspaces + 07-14 polish batch — founder in-browser review pending

Shipped 07-14 (PRs #65–68) — detail: BUILD_LOG 2026-07-14. **Open:** founder hasn't eyeballed the batch
(workspaces switcher, hover previews, theme backfill) in his own browser yet.

## ▶ Exit engine — first real proof 2026-07-29

Fixed #91 (dead since D3) — detail: BUILD_LOG 2026-07-16. **Open:** 7 earnings_drift positions must
auto-sell on 2026-07-29; sibling unguarded fallbacks recorded on `data-correctness-m-tail`.
Also open (07-22 half-day review, P1 — pre-existing, no regression): the amc `td==1` exit's docstring
says the after-close day-TIF order "queues to the report-day open", but RiskEngine's no-live-quote gate
REJECTS after-close submissions — the fill actually happens via next-cycle retries once the report-day
open has a quote. Same destination, different mechanism; verify against the 07-29 proof (throttle marks
at exit_engine.py:390 must not delay the open fill) and fix the docstring to match reality.
