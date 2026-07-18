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
- ▶ **Phase A IN FLIGHT** (spec v3.1 signed off 07-18, incl. cohort scorecards §2.4b + Idea
  Ledger table §2.8): A1 substrate dispatched to gpt-5.6-sol
  (`briefs/fusion-phase-a1-substrate-2026-07-18.md`); A2 = orchestrator after A1 review
  (registrar seam: fusion_mech + JOURNAL_ORIGINS, batch grader, rank_in_batch stamp both
  lanes). Board: `fusion-phase-a-substrate`. Observe-only; legacy pushes stay
- acceptance drills when convenient: rejected proposal → `user_action='skipped'` + still grades;
  broken parser pages within one cycle; sub-gate idea renders "unproven" (no number) on phone

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
