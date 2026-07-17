# Idea Ledger Unification — spec (2026-07-11)

> **SUPERSEDED BY `IDEA_LEDGER_SPEC_2026-07-15.md` → `IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md` + `IDEA_ENGINE_PHASE1_SPEC_2026-07-17.md`** — kept for the record; do not build from this.

**Goal:** one calibration ledger where EVERY suggested trade idea — from any source, taken
or not — is registered at birth and outcome-tracked mechanically. This is the
"calibration counts everything" rule made concrete: win% is only honest when the
denominator includes the ideas nobody acted on.

**Founder's question this answers:** "I want to see what happens to every sort of
suggested trade."

---

## 1. Current fragmentation (why the widgets looked dead)

| Store | What it tracks | Writer | Resolver | Status |
|---|---|---|---|---|
| `idea_outcomes` | Setup-scan ideas (entry zone/stop/targets), shadow-tracked | `run_setup_scan` only | `_idea_tracker_loop` (30 min) | ✅ works; **now fed nightly** (2026-07-11) |
| `decision_journal` | Formal order proposals → decision → fill → realized R | `create_phone_proposal` / regime sleeves | `exit_engine.close_position` side-effect only | works; empty because no strategy is armed |
| `options_flow_events.ret_*` | Price outcomes after flow events (facts, not plans) | flow detectors | `fill_outcomes` after EOD scan | ✅ new (2026-07-11) |
| RVOL alerts, price alerts, chat suggestions, report levels | nothing | — | — | ❌ unregistered |

Three outcome systems, none cross-referenced, most idea sources register nowhere.

## 2. Design principles

1. **An idea is a plan, an event is a fact.** An *idea* has a direction, an entry
   zone, a stop, and at least one target — it can be mechanically graded won/lost.
   An *event* (flow burst, RVOL spike, price-level break) is a fact with no plan;
   it gets return-horizon stamps (ret_15m/1h/1d…) in its own store, never forced
   into the idea shape. `options_flow_events` stays separate **by design**.
2. **`idea_outcomes` is THE idea ledger.** Any source producing a plan registers
   into it. `origin` (already exists) is the source tag; per-origin win rates
   already ship in `get_summary().by_origin`.
3. **`decision_journal` is THE orders ledger.** Proposals/fills/realized-R are
   execution truth — do not merge the tables. Instead **bridge** them (§4).
4. **Registration is append-only and deduped.** Same (symbol, zone, stop, origin)
   while still open = same idea, one row (shipped 2026-07-11). Nothing is ever
   deleted; wrong registrations get `invalidated` + note.
5. **Win% is never fabricated.** Under-sample cells surface as "accruing", same as
   the flow-outcomes report (n≥20 units before a rate is shown).

## 3. Phase B — widen registration (the build after this one)

**B1. Generalized registration surface.** A thin helper (new `research/idea_registry.py`
or a promoted `db.idea_outcomes.register_idea`) with the narrow-write contract:

```
register_idea(origin, symbol, direction, entry_zone{low,high}, stop, targets[],
              thesis, as_of, source_ref{kind,id})  # e.g. {"artifact": 178}, {"chat": msg_id}
```

Validation identical to setup-scan ideas (zone sane vs last price, stop on the
correct side). Sources to wire, in order of value:
- **Chat-agent suggestions**: when the chat agent emits a trade idea with a plan
  (it already produces this shape for proposals), register with `origin="chat"`.
  No plan → no registration (don't manufacture stops the agent never stated).
- **Decision Engine ranked ideas** (as they ship): `origin="decision_engine"` —
  this is the calibration substrate the win%-honesty work requires.
- **Morning/evening report levels**: only if a report states an actionable plan;
  levels alone are events.

**B2. Short-side support.** `idea_outcomes` assumes long (stop below zone, target
above). Add `direction TEXT NOT NULL DEFAULT 'long'`; the tracker inverts
`_stop_hit`/`_target_hit`/excursion math for shorts. Small, mechanical, tested
with mirrored fixtures. Prerequisite for chat/decision-engine registration
(both can be short).

**B3. Ideas-taken bridge.** When a `decision_journal` proposal originates from a
tracked idea, stamp `idea_id` on the journal row (nullable column). This yields
the founder's core comparison *mechanically*: ideas taken vs ideas passed,
same-origin, same-period — did acting on them beat the shadow book?

## 4. Phase C — reporting

- Widget: origin filter (plain lowercase colored words, not pills) on both tabs.
- Weekly calibration digest (existing reports scheduler): per-origin resolved
  counts, win rate, median MFE/MAE, taken-vs-passed delta once B3 lands. Cells
  gate at n≥20 resolved; below that print "accruing (n)".
- 5b grading gate unchanged — founder grades only resolved, recommended-lane ideas.

## 5. Non-goals

- No auto-execution from the idea ledger, ever — it is measurement, not a signal
  path. The execution chokepoint and RiskEngine are untouched by all phases.
- No merging of the options-flow facts ledger into idea_outcomes.
- No backfilled or synthetic outcomes: ideas registered late start tracking from
  registration, flagged `notes="late registration"`.

## 6. Shipped with this spec (2026-07-11)

- Nightly scheduled setup scan (`setup_scan_nightly` job: weekdays ≥17:30 ET,
  once/day, restart-safe via artifact-topic marker; env gate
  `SETUP_SCAN_NIGHTLY_ENABLED`, default on).
- Cross-artifact dedup of identical open zones in `register_ideas`.
- Ranker-failure no longer aborts the scan (ideas survive unranked).
- Sweep stamps `last_price`/`last_price_at`; API adds `expires_on` +
  `zone_distance_pct`; widget open tab shows Last (distance from zone) and
  Expires — a pending row now explains itself.
