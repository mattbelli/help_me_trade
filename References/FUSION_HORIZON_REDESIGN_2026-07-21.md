# Fusion Horizon Redesign — converged direction (2026-07-21)

**Status: DIRECTION BLESSED by founder 2026-07-21 ("ok sounds good"); UI target locked same
day (§7). Migration sizing is the next step — no engine code changes committed yet.** This is
a delta on `FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md` §2 (scoring + lifecycle). That spec's
intake/group taxonomy, dossier, and LLM lane sections stand; its §2.7/§2.8 surfaces are
superseded by §7 below.

## 0. Provenance — three independent passes converged
Trigger: founder, 07-21, on live shadow data — "every single thing later goes contested…
you can't accept everything into one idea window," plus his horizon/DTE/attention questions.
Method (CLAUDE.md fresh-eyes doctrine): repo-anchored orchestrator take + a zero-context
Fable 5 pass + a zero-context Grok pass, all given the same problem statement. All three
converged on the skeleton below; divergences and how they resolved are in §5.

## 1. The disease (measured, not assumed)
- Episode = one symbol-DAY bucket (`UNIQUE(symbol, market_day)`); score = undecayed all-day
  sum; contested = absorbing bit (both directions EVER in the day → contested until midnight).
- Measured 07-21: all 12 gate-crossing stacks went contested by afternoon; SPCX accumulated
  1.91 → 12.30 across the day. Contested-if-ever measures *liquidity*, not disagreement —
  market-making is two-sided, so every liquid name trips it. The no-decay bug and the
  binary-contested bug are the same bug: direction treated as a set instead of a signed
  decayed sum.

## 2. The converged architecture

### 2.1 Evidence: signed decayed masses over one log
- Dots stored once (`evidence_dots` stands, append-only). Per symbol, per actor group, keep
  **bull mass** and **bear mass** — exponentially decayed sums, recomputable from the log.
- **conviction = |bull − bear| / (bull + bear)** on decayed weights. Contested = high gross
  mass + low conviction *contemporaneously*. Contested heals when one side's evidence ages out.
- Two decay classes by evidence ROLE (§2.2): thesis half-life (~2–3 trading days) and
  trigger half-life (~45–60 min). Both are founder dials.

### 2.2 Two evidence roles: thesis fuel vs trigger fuel
- **THESIS** (slow decay): 7–45 DTE flow, dark-pool/equity block accumulation, weekly levels
  (PWH/PWL), multi-day trend + volume patterns, earnings proximity.
- **TRIGGER** (fast decay): 0–2 DTE flow, sweeps, RVOL bursts, session-level touches.
- Rule (all three passes agreed): fast evidence may TIME an entry, never CARRY a thesis.
  DTE routes a print's role and weakly corroborates hold length; it is **never the trade's
  exit clock** — the idea's own plan (stop/target/time-stop) is the clock.

### 2.3 Product horizon: SWING ONLY (2–15 sessions)
- The armed product lane is swing. Independent ×3 rationale: (a) a phone-approval consent
  loop structurally can't beat a 0–1 DTE clock; (b) daily-bar grading is clean where intraday
  grading drowns in slippage/fill noise; (c) the contested pathology largely dissolves at
  swing timescales; (d) swing tolerates the attention cap.
- Repo fact that seals it: the registrar's frozen plans are ALREADY swing-shaped (daily-ATR
  stops/targets, daily-bar grading, multi-day resolution). Today's build is swing ideas
  triggered by undecayed intraday noise — this fixes the mismatch, not the ambition.
- The intraday lens keeps computing in SHADOW (counted, never pushed) — calibration data for
  a possible future intraday lane if/when bounded-auto (Rung 3) exists.
- **Not building:** a long-term/investment lane. Flow/tape/buzz are the wrong sensors for
  multi-month theses — that lane needs different inputs (fundamentals, Form 4), not longer
  windows.

### 2.4 Episode lifecycle (replaces the symbol-day bucket)
- Open on first material evidence; close on decay-to-noise, price invalidation, or hard TTL.
  The calendar day is no longer a boundary: an episode may span days, and a symbol may have
  sequential episodes. (Schema consequence: the symbol-day UNIQUE constraint dies.)
- Off-watchlist = **cold storage, not amnesia**: persist the episode summary (masses,
  direction, key levels, last-evidence timestamps); rehydrate when the name returns. Swing
  evidence is reconstructible from persisted EOD aggregates, so watchlist rotation stops
  destroying context. Only intraday genuinely needs standing live watch.

### 2.5 Idea lifecycle (extends the Grading Law; replaces one-shot registration)
- **born** (hypothesis): episode maturity crosses the bar → LLM dossier investigation
  (existing machinery).
- **armed**: the investigator writes the plan INCLUDING a trigger condition + trigger TTL.
  Two trigger classes: *impulse* (sharp one-sided fast cluster + level touch → TTL in hours)
  and *thesis-confirmation* (maturity + confirmation, e.g. level reclaim / RVOL → TTL in days).
- **triggered → live → closed**; armed-never-triggered → **expired**; decayed pressure flips
  hard against → **invalidated**. Every terminal state is outcome-graded — expired and
  invalidated are new graded outcomes in the ONE existing ledger, not a new ledger.
- "Accumulation alone must not equal propose-now": no idea reaches the user without its
  trigger gate firing.

### 2.6 Attention allocation (state-driven tiers on lanes we already run)
- **Tier 1 — always-on live** (existing ws subs): armed ideas near trigger, open positions,
  event-adjacent names.
- **Tier 2 — periodic poll** (existing poll lane): episodes accumulating thesis mass.
- **Tier 3 — nightly universe scan** (existing research jobs): promotes names into Tier 2.
- No attention optimizer — armed-idea count naturally self-limits Tier 1.

## 3. What survives unchanged
Intake/dot minting + actor-group taxonomy, collapse logic, dossier builder, LLM investigation
lane (budgets, alarms, crash recovery), registrar plan shape, unified idea ledger + Grading
Law, delivery seams (in-app only until the founder flips the phone back on), evidence-stack
widget (later gains mass/conviction display).

## 4. Founder dials (changes always surfaced as diffs, never silent)
- Thesis half-life (start 2–3 trading days) and trigger half-life (start ~60 min).
- Conviction bar ("one-directional enough").
- Maturity threshold T — **RESET**: the 07-21 cut-line table is void under decay+conviction
  scoring. Re-run shadow on stored dots, then present a fresh table for the founder's pick.
- Trigger TTLs per class (hours / days).

## 5. Divergences between the passes + resolution
- **One log + lenses (Fable) vs separate per-horizon ledgers, scores never mix (Grok).**
  Resolved: one log at storage, role-tagged decay at read — with NO cross-role score mixing,
  honoring Grok's isolation without duplicate ingestion pipelines.
- **Position/investing ledger someday (Grok) vs wrong-sensors-don't-build (Fable).**
  Resolved: not built; revisit only if new input classes (fundamentals, Form 4) are added.
- **Intraday shadow lens (orchestrator addition).** Neither zero-context pass knew shadow
  machinery exists; keeping the intraday lens computing-but-dark costs nothing and feeds
  calibration. Adopted.

## 6. Migration sketch (sized next)
1. Scorer: decayed masses + conviction ratio (pure function change, replayable on stored dots).
2. Shadow re-run over stored `evidence_dots` → fresh threshold table → founder picks T.
3. Episode lifecycle schema + registrar armed/trigger/TTL states + expired/invalidated grading.
4. Attention tiers mapped onto existing lanes.
5. ARM the swing lane — supersedes C6-as-specced; the ARMING CHECKLIST in
   `config/idea_engine.py` gets rewritten as part of that round, not before.

## 7. UI target — two workspace widgets, everything else consolidates (founder-locked 07-21)
Founder: "these widgets everywhere need to just be consolidated and refined into one or two
views." Decisions (07-21): **two views**, both **workspace widgets**.
- **Desk** (live; visited daily) — three zones by urgency: *Needs you* (triggered ideas /
  fired triggers, usually 0–2 rows), *Armed* (waiting on trigger: plan summary, trigger
  condition, conviction spark, TTL remaining), *Warming* (top ~10 radar by mass×conviction:
  bull/bear mass bars, conviction, warm evidence roles). Ticker click focuses chart (standing
  rule); row click drills into the full dossier (the evidence-stacks view becomes this
  drill-down, no longer a primary widget).
- **Ledger** (trust; visited weekly) — graded outcomes incl. expired/invalidated, hit-rate by
  idea-type ranked ordinally (prune-the-losers view), founder-visible dial/weight diff feed.
- **Retired as surfaces:** notifications widget as content (bell shrinks to pure transport:
  trigger/proposal/exit/ops), standalone idea-outcomes widget (absorbed into Ledger),
  evidence-stacks as top-level (becomes Desk drill-down). Convergence firehose: no surface.
- Sequencing: Desk needs the new engine's masses/conviction/armed states → built alongside
  the engine migration, not before. Mobile = same payloads, per-surface UI later (bottom-tab
  direction stands).
