# Handoff — "you are here" (2026-07-17)

*Bootstrap for a fresh session. Read this first, then `CLAUDE.md` (mission + working agreements, auto-loaded),
`AGENTS.md` (codebase map), and per-topic refs: `BUILD.md` (roadmap + dated history — **grep it, don't read it
whole**), `ARCHITECTURE.md`, `SERVER_SETUP.md`, `STARTUP.md`. Deep server specifics: the `serv-runtime` memory.*

*Keep this file LEAN: current focus + the last few entries only (~4–5 sections max). On a superseding rewrite,
append the old full version to `HANDOFF_ARCHIVE.md` as a new dated section (oldest first). Older narrative =
`HANDOFF_ARCHIVE.md` + BUILD.md history rows.*

---

## ▶ START HERE (2026-07-17) — Idea Engine Phase 1 LIVE; today = the verify gate

> **Deployed 2026-07-16 ~23:20 ET** (PRs #95–97, dual-Opus split-mandate reviewed; spec =
> `IDEA_ENGINE_PHASE1_SPEC_2026-07-17.md`, design = `IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md`): unified
> idea ledger (direction-symmetric [ADR-6], fabricated-probability wall, fail-soft law — ledger can NEVER
> block trading), Path A registration + `user_action` stamping via `_journal_decision`, Grading Law v1
> (SPY alpha leg, tie-break=stop-wins, `no_fill` first-class), `lane_stats` + `pipeline_audit`
> rate-vs-baseline alarms ("silence made illegal"), daily **15:00-ET canary push** asserting a
> `push_deliveries` receipt, external **dead-man cron 16:45 ET weekdays** (ntfy topic in crontab — **founder
> must subscribe**), honesty fix (win_prob=None → "unproven" end-to-end), and the 3 nightly-scan bugs
> (banner "0 idea(s)" key, dead conviction LLM lane → max_tokens 6000 + retry + tail telemetry, duplicate-run
> guard → exact-match topic lookup).
> **Verify gate (spec §Deploy) — status as of 07-17 10:35 ET:** ✅ lane meters flowing (idea_tracker
> swept=10/examined=110/graded=2, path_a registered=1 this morning); ✅ launch canary sent 23:51 ET +
> receipt row `canary:2026-07-16|ops_canary|sent`; ✅ dead-man `--check-only` dry-run green (run with
> `bash tools/idea_engine_deadman.sh --check-only` + the NTFY_TOPIC from crontab). **Remaining today:**
> 15:00 ET canary lands on phone → 16:45 ET first armed dead-man run → tonight's post-close nightly scan
> banner shows real structure/conviction counts. Acceptance drills: rejected proposal →
> `user_action='skipped'` and still grades; broken parser pages within one audit cycle; sub-gate idea
> renders "unproven" with no number. **Phase 2 spec only after the gate passes — and informed by the first
> live week of lane meters** (parse-fail rates, no-fill rates), so don't draft it on day one.

## ▶ EARLIER (2026-07-15/16) — Flow collector split out + opening index-pressure gauge shipped

> **hmt-flow-collector** owns the OPRA socket since 07-15 (own systemd unit — stop THAT unit to free the
> socket, not hmt-backend). Gauge lane #93 guarantees coverage (OI-seeded subs; NO production wildcard
> trades lane). **Index pressure**: opening+closing gauge + chain sampler + print classification lab
> deployed 07-16; metrics are pre-registered in `CLASSIFICATION_LAB_PREREG` (never improvise new ones on
> the data). ⏳ 07-17 is the OpEx verify day. SPXW reachable via SPX root; NDX unavailable — QQQ carries it.

## ▶ EARLIER (2026-07-14 overnight) — Workspaces shipped + 6-item polish batch

> Founder pre-bed batch, all shipped + deployed (PRs #65–#68; BUILD.md 2026-07-14 rows): options-chain call
> side mirrored, company-info card centered, tickers uppercase app-wide, undefined-var fixes, custom themes
> backfill from BASE, AI-chat prose polish, ticker hover preview card, and **WORKSPACES**
> (`References/WORKSPACES_SPEC.md`): named groups of pages, per-device active, switcher in the bottom strip.
> The 07-14 "⚠ backend restart pending" for `/api/workspace/workspaces` is stale — the backend has been
> restarted by the 07-15/16 deploys, so server-side workspaces are live.

## ▶ EARLIER (2026-07-13 overnight) — Watchlist v3 round 1: column registry + settings + per-list config

> Shipped on `feat/watchlist-columns-v3` (since merged): 19-column registry in 5 categories, gear → settings
> overlay (density, drag-reorder, toggles), header-edge width drag, sticky ticker column, per-list `settings`
> blob validated in `config/settings.py` and synced cross-device. Default order = founder's ToS pref.
> Deferred columns (52w dist, RSI, SMA dist, bid/ask, IV rank, decision score) — the registry absorbs them
> later.
