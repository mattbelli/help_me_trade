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
- ▶ **Overnight 07-18 doc harvest awaiting founder**: `research/overnight_2026-07-18/`
  (map: `NIGHT_LEDGER.md`) — Strategy Lab design duel + synthesis (orchestrator judgment
  next), roadmap designs + critiques (news dots / nightly wrap / watchlist v3), perception
  ideation. Remaining Cursor quota burns by ~midday Sat on founder-directed rounds
- acceptance drills when convenient: rejected proposal → `user_action='skipped'` + still grades;
  broken parser pages within one cycle; sub-gate idea renders "unproven" (no number) on phone

## ▶ Backend composition — audit verified + step 1 shipped; splits + measurement open (2026-07-18)

Zero-context composition audit of hmt-backend verified claim-by-claim and archived
(`References/BACKEND_COMPOSITION_AUDIT_2026-07-18.md` — read its header first). Step-1 fixes SHIPPED
(blind jobs registered; EOD scan / fill_outcomes / fusion ATR off-loop — BUILD_LOG 2026-07-18). **Open:**
- ▶ **Rank-1 nightly research worker** — new systemd unit via the collector seam (env flag +
  `tools/research_worker.py` + WAL); moves setup_scan_nightly, options_flow EOD, volume_profile,
  momentum bars, reflection. Own design round; founder go needed
- ▶ **Rank-2 exit-engine own cadence** — in-process, off the `_poll_once` tail; `trading/`-adjacent →
  max depth + adversarial review. NOTE: audit argues AGAINST extracting the order path out-of-process
  (conflicts with the old rung-3 "extract trading loops" note — resolve at design time)
- ⏳ **loop-lag measurement**: fusion ATR shipped 07-16 so it cannot explain the 07-13 20–30s stalls —
  a suspect is still unmeasured; wrap remaining suspects with wall-clock or add a loop-lag probe
- Schwab jobs stay in-process behind config gates (founder leaning: Schwab is personal, not product —
  Rank-3 tier, no action now)

## ▶ Options flow — gauge verified 07-17; decomposition report + classification scoring open

07-17 OpEx verify PASSED (both windows finalized clean, single writer; print lab 599k prints
64% stream-quote truth) — detail: BUILD_LOG 2026-07-17/18. **Open:** (1) Flow Decomposition
daily report built 07-18 (board `flow-daily-decomposition-report`) — needs merge + deploy
before Mon 07-20 16:00 ET for its first scheduled run; (2) classification-lab SCORER not yet
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
