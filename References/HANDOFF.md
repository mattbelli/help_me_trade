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
- ▶ **Phase C build OPENED 07-21** (founder go; board `fusion-phase-c-investigation`, detail:
  BUILD_LOG 2026-07-21): C1 shadow investigation gate SHIPPED (`signals/fusion/investigate.py`
  — archetype `options_actor`+`structure`, 5/day budget + opening reserve, counts only).
  C1b SHIPPED same night (structure state dots: `price_alert` component fully live;
  `session_level` wired end-to-end reading new `fusion_session_levels` cache).
  C1c producer SHIPPED same night (`fusion_levels_refresh` job, twin of ATR's; hybrid
  provider verified equities→Alpaca). **OPEN:** (1) ⏳ session-2 verify Tue 07-21: shadow
  gate counts (`fusion_investigations` / `fusion_would_investigate`), first levels drain
  (`levels_stamped` vs `levels_refresh_failed` — watch Alpaca rate pressure), storm
  behavior at the open; (2) C2 dossier + C3 T1/REASON v1 + C4 minute grading + **C5 delivery/arming ALL
  SHIPPED** (detail: BUILD_LOG 2026-07-21; C5 passed line-by-line + two split-mandate
  adversarial Opus reviews, 5 confirmed findings fixed pre-commit incl. the
  spend-anchor crash window and the arming-checklist KeyError). The ENTIRE lane is
  still DARK (`REASON_EMIT_ENABLED=False`) — **C6 = the flip**: founder ARM = the
  4-edit ARMING CHECKLIST beside `REASON_EMIT_ENABLED` in `config/idea_engine.py`
  + deploy (flags are import-time), plus `FUSION_CARDS_ENABLED` for the inbox
  cards; legacy convergence pushes retire only at ARM (no-blackout rule). Gate:
  sessions 2–3 shadow-verify clean + founder eyeballs the widgets. Deferred to
  post-ARM: `_stamp_last_price` on the minute path; push-slot TOCTOU dies with
  convergence retirement. REASON v2 A/B stays founder-gated (multi-angle card
  below)
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
- ▶ **Rank-1 nightly research worker — APPROVED 07-19; R1a LIVE, R1b open (founder-held)**
  (`References/RESEARCH_WORKER_DESIGN_2026-07-18.md`, board `rank1-research-worker-design`): R1a
  went live Mon 06:00Z — the Schwab-reauth cron's backend restart picked up the 07-19 commits
  before the planned with-verdict deploy (inert as designed: flag off, backend owns all five
  jobs; detail: BUILD_LOG 2026-07-21). R1b (worker shell + lease ticker + unit + flag flip +
  dead-man receipts + /health block) is spec'd + recon-complete, ON HOLD per founder 07-20
  ("No need to start now")
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
