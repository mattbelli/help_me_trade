# Handoff — open threads (2026-07-17)

*Bootstrap for a fresh session: read this, then `CLAUDE.md` (auto-loaded), `AGENTS.md` (module map),
`References/INDEX.md` (which doc is current).*

*Contract (tense rule, adopted 2026-07-17): this file holds ONLY what is **open right now** — pending
verifies, armed experiments, decisions in flight. PAST tense lives in `BUILD_LOG.md` (one dense row per
round — grep it) and git; don't re-narrate shipped work here beyond one line + a pointer. When a thread
closes, **delete its entry** — there is no archive (pre-07-17 narrative: git history of
`HANDOFF_ARCHIVE.md`, removed same day).*

---

## ▶ Idea Engine Phase 1 — verify gate TODAY (2026-07-17)

Deployed 07-16 ~23:20 ET (PRs #95–97) — detail: BUILD_LOG rows 2026-07-16/17; spec =
`IDEA_ENGINE_PHASE1_SPEC_2026-07-17.md` (gate + acceptance drills in §Deploy).
**Open:**
- ✅ this morning: lane meters flowing; launch canary sent + receipt row; dead-man `--check-only` green
- ⏳ 15:00 ET canary lands on founder's phone → ⏳ 16:45 ET first armed dead-man cron run → ⏳ tonight's
  post-close nightly scan banner shows real structure/conviction counts
- ⚠️ founder must SUBSCRIBE to the ntfy dead-man topic (in crontab) or pages go nowhere
- acceptance drills when convenient: rejected proposal → `user_action='skipped'` + still grades; broken
  parser pages within one cycle; sub-gate idea renders "unproven" (no number) on phone
- **Phase 2 spec only after the gate passes**, informed by the first live week of lane meters

## ▶ Index-pressure gauge — first OpEx verify TODAY (2026-07-17)

Gauge (open+close windows) + chain sampler + print lab deployed 07-16 — detail: BUILD_LOG 2026-07-16.
**Open:** today is OpEx = the verify day; metrics are pre-registered in
`CLASSIFICATION_LAB_PREREG_2026-07-16.md` (LAW — never improvise on the data); deferred audit findings
F3/F4/F8 remain on the `index-0dte-concentration-study` / gauge cards.

## ▶ Workspaces + 07-14 polish batch — founder in-browser review pending

Shipped 07-14 (PRs #65–68) — detail: BUILD_LOG 2026-07-14. **Open:** founder hasn't eyeballed the batch
(workspaces switcher, hover previews, theme backfill) in his own browser yet.

## ▶ Exit engine — first real proof 2026-07-29

Fixed #91 (dead since D3) — detail: BUILD_LOG 2026-07-16. **Open:** 7 earnings_drift positions must
auto-sell on 2026-07-29; sibling unguarded fallbacks recorded on `data-correctness-m-tail`.

## ▶ Docs freshness cloud agent — blocked on founder GitHub connect

Weekly Opus auditor is specced and ready (Mon 10:00 UTC). **Open:** founder installs the Claude GitHub
App (https://claude.ai/code/onboarding?magic=github-app-setup), then any session re-runs the create.
