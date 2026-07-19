# Multi-Angle Reasoning A/B — bull/bear/judge panel vs the single veto-grade voice

*Status: DESIGN — capture only. Build gate: founder go (his framing 07-19: "keep 1 but have
another lane of multi angle so we can see if one outperforms the other… if its just bloat then
tell me"). Process: drafted → Opus adversarial review (SHIP-WITH-FIXES; all three blocking
findings + MED set folded below, review claims re-verified first-hand) → founder. Board:
`multi-angle-reasoning-ab`.*

---

## 1. The question, made falsifiable

Does a three-voice panel (bull advocate → bear advocate → judge) make better **accept/veto
calls** than today's single veto-grade pass — on identical candidates, over identical immutable
plans? "Better" is defined by pre-registered metrics (§5) with kill criteria committed in
advance. **"It's bloat" is a real, pre-defined outcome of this experiment** (§5 rule K1), not a
failure of it — if the panel just agrees with the single voice at ~4x the reasoning cost, the
A/B ends and says so.

## 2. Current state (anchors verified against code 2026-07-19; review-corrected)

- `CANDIDATE_CAP = 12` — `ai/research/setup_scan.py:38`. Structure candidates that pass
  deterministic detection get the veto-grade LLM pass; no-structure candidates can still reach
  the LLM via the separate conviction pass (`CONVICTION_SYSTEM_PROMPT`, ≤2 nominations) — the
  panel touches only the structure lane.
- Plans are authored deterministically (`detect_structures` → levels). The LLM never writes
  levels. The single reasoning pass is `_assess_candidate` (`setup_scan.py:346`): it FETCHES
  (technicals/news/earnings/fundamentals/web, :367-403) and assesses in one function — one
  specialists-role call per structure candidate, `VETO_GRADE_SYSTEM_PROMPT` (:43): accept or
  veto + thesis/confidence/risks, levels IMMUTABLE, no numeric win%, untrusted-data line.
- **The veto half already gets a report card**: a vetoed structure registers as
  `origin="structure_vetoed"` (`setup_scan.py:1088`) — journal lane, excluded from headline
  win-rate, still swept and graded mechanically (`list_sweepable` filters by status only;
  `structure_vetoed` is not in the fusion-only `NEXT_SESSION_ORIGINS` set). Accepted ideas
  register as `origin="structure"` (:1003).
- The trade-path invariant — only `origin == "structure"` may ever reach a proposal — is
  documented at `setup_scan.py:1052-1053` but currently enforced **by absence of path**: the
  scan registers ledger rows and writes an artifact; it creates no proposals and sends no
  pushes. Any future auto-propose work must add the active origin gate first.
  `reasoning_meta` (§4) must never become a trade-path input.
- `_run_ranker` (:516) is reorder-only. Conviction lane: `origin="llm_conviction"` (:796).
- The Grading Law sweep stamps market-relative outcomes at resolution:
  `ret_pct`/`spy_ret_pct`/`alpha_pct` (`research/idea_tracker.py:534` `_stamp_benchmark`,
  applied :679, persisted :746) — M1 adjudicates on this, not raw won/lost (§5).
- Honesty wall today: `validate_ideas` screens thesis/confidence_reason via
  `_has_fabricated_probability` (`ai/research/ideas.py:43,:103`); the narrow `register_idea`
  path screens thesis/bear_case (`db/idea_outcomes.py:528`); **the artifact-path
  `register_ideas` (:403) has NO screen** — closed by this build (§4).
- `register_ideas` dedupes vs still-OPEN rows on (symbol, entry zone, stop, **origin**) and
  does not currently pass `bear_case` through. `bear_case` is a persisted-but-never-rendered
  column today (zero frontend references) — there is NO phone surface for it yet.

## 3. Design — the panel lane

The fetch/detect half of `_assess_candidate` is split out and runs ONCE per candidate; the
shared payload then fans out to the assessments (no duplicated web/news/earnings I/O — the
fetch path stays at today's volume):

1. **Bull advocate** — the strongest honest case FOR taking the detected setup, from the
   shared payload.
2. **Bear advocate** — the strongest honest case AGAINST: tape, earnings proximity, structure
   quality, crowding, thesis fragility.
3. **Judge** — receives the data summary plus both advocate outputs **as a constrained
   projection ({stance, argument} blocks explicitly framed with the untrusted-data line —
   advocate text is derived from untrusted sources and must not be treated as trusted analyst
   instruction; the injection-laundering channel is closed at this seam)**. Outputs the same
   verdict schema as today's single pass (decision/structure_type/veto_reason/thesis/
   confidence/confidence_reason/key_risks/evidence_urls), so everything downstream is
   schema-identical — including flowing through `validate_ideas` and its probability screen
   exactly as the single verdict does today.

Bull and bear run in parallel; the judge is one sequential stage after them — per-candidate
wall-clock adds roughly one LLM stage vs today under the scan's `Semaphore(4)`. Contract
inherited verbatim into all three prompts: levels immutable, no new setups, no numeric win%
(qualitative confidence only), `_UNTRUSTED_LINE` in every prompt. **"Shrink" is defined as:
the judge may downgrade confidence, never edit a plan.**

Scope: structure lane only in v1; the conviction pass stays single-voice. Ships behind
`MULTI_ANGLE_AB_ENABLED` (default **False** — off-by-default law), read once per scan run;
flag off or unreadable → single-voice only, exactly today's behavior (fail-closed to the
status quo).

## 4. A/B mechanics — paired shadow, alternating control

- **Paired, not split-sample.** Every structure candidate is assessed by BOTH lanes: direct
  per-candidate disagreement (the fast metric), no sample halving.
- **Control alternates deterministically**: `sha1(f"{symbol}:{et_date}") % 2` picks which
  lane's verdict *ships* — registration origin, the nightly paper, ranker input. No
  cherry-picking; balanced over time; reproducible from the row itself.
- **Origin never forks.** Registration is `structure` / `structure_vetoed` per the CONTROL
  verdict — the same two origins as today. Trade-path invariant, dedup key, journal-lane
  exclusions, and the 5b grading queue are all untouched.
- **Both verdicts are stamped** in a new `reasoning_meta` TEXT (JSON) column on
  `idea_outcomes` (existing `_add_column_if_missing` migration pattern):
  `{"control": "single"|"panel", "single": {decision, confidence, veto_reason?},
  "panel": {decision, confidence, veto_reason?}}` — an errored shadow lane stamps
  `{"decision": "error"}`. Also carried in the artifact payload. Aggregation is
  `json_extract`; no new tables. **M1 reads `reasoning_meta` on the single registered row —
  never row pairs** (the cross-day dual-row case in §6 is irrelevant to M1).
- **Honesty wall extended (blocking fix F2):** `register_ideas` gains the
  `_has_fabricated_probability` screen over `bear_case` and any free-text `reasoning_meta`
  fields before insert, mirroring `register_idea`'s existing guard (`db/idea_outcomes.py:528`)
  — a bear advocate writing "~20% chance this holds" must never persist, let alone render.
- **Bear-case surface, honestly scoped (fix F3):** the bear advocate's compressed argument
  (2-3 sentences, screened) is persisted to `bear_case` on accepted rows and rendered in the
  **nightly scan paper's per-idea section** — a surface that exists today. The phone idea
  card is a small follow-up, NOT part of this build; no "day-one phone value" is claimed.

## 5. Pre-registered metrics — LAW (classification-lab prereg discipline)

Committed before the first night of data; never improvise new metrics on this data after the
fact (K3).

- **M1 — primary: veto disagreements, adjudicated market-relative (blocking fix F1).**
  Cohort = paired assessments where the lanes disagree on accept/veto (read from
  `reasoning_meta` on the registered row). The row grades mechanically whichever way control
  went (accepted → `structure`; vetoed → `structure_vetoed` shadow). Adjudication per
  resolved disagreement uses the sweep's stamped **`alpha_pct`** (never raw won/lost — a
  long-only book in an up-tape credits the permissive lane on raw hit-rate regardless of
  judgment; alpha strips the tape): **`alpha_pct > 0` → the accepting lane was right;
  `alpha_pct < 0` → the vetoing lane was right; expired-untriggered / invalidated / data_gap
  / missing alpha → no point.** Report the ordinal record (panel-right W, single-right L)
  with a Wilson interval.
- **M2 — confirmatory, slow: per-lane batting average** on control-accepted ideas, ordinal
  only. **Known confound, stated up front:** the control hash makes M2 compare disjoint
  symbol-day sets — a few big movers in one bucket skew short windows. M2 is directional
  color over a long horizon, never the decider; M1 (genuinely paired) decides.
- **M3 — guard: behavioral drift.** Per-lane veto rate and confidence distribution, weekly.
  Catches a judge rubber-stamping one advocate, and a panel converging to the single voice
  (feeds K1).

**Pre-committed decision rules (timeline honesty — two different clocks):**
- **K1 (bloat kill — the fast clock, ~6 weeks):** after 30 trading days, if lanes disagreed
  on <10% of paired assessments AND fewer than 10 total disagreements accrued, the panel is
  bloat — same answers at ~4x reasoning cost. Kill the A/B, keep single-voice, record the
  verdict. (30 days ≈ 60-150 pairs at 2-5 structure candidates/night; the 20-day version was
  a coin-flip at its own boundary — under-powered.)
- **K2 (winner — the slow clock, realistically 2-6 months):** at ≥20 alpha-adjudicated
  disagreements, if one lane's Wilson lower bound clears 50%, it takes the veto seat; the
  loser retires (or stays as shadow — founder's call). Many structure ideas expire
  un-triggered, so adjudicated disagreements accrue at a fraction of the disagreement rate —
  no "verdict in weeks" claim on K2.
- **K3:** no mid-experiment metric changes; any addendum needs founder sign-off before it
  touches the data (prereg precedent: `CLASSIFICATION_LAB_PREREG_2026-07-16.md`).

## 6. Failure & edge cases

- **Control lane errors** → the candidate drops exactly as an `_assess_candidate` error drops
  it today; **no row registers, so the pair (including the shadow's spent verdict) is lost to
  M1** — never silently substitute the shadow's verdict (biases the samples).
- **Shadow lane errors** → `{"decision": "error"}` in `reasoning_meta`; candidate ships via
  control; pair excluded from M1.
- **Cross-day dedup interaction**: origin is part of the dedup key, so an accept-then-veto on
  the same still-open zone across days can already produce a `structure` +
  `structure_vetoed` row pair today (covered by existing tests) — pre-existing behavior, not
  a new hazard, and irrelevant to M1 (which reads single-row meta).
- **Cost bound**: worst case 12 candidates × 4 LLM calls (1 single + 3 panel) = 48
  specialist calls/night vs 12 today — but data fetches do NOT double (shared payload, §3),
  and in practice only structure-bearing candidates reach the panel (typically well under
  cap). Nightly-scan only; no standing infra.

## 7. Later — the selection committee (panel-picks-N), explicitly NOT this build

The founder's cross-pool picker ("panel picks the best 5") is a **different funnel stage**:
per-candidate accept/veto (this doc) vs choosing the best N across the whole pool. It becomes
valuable when fusion lanes arm and the pool outgrows the 12-cap scan (watchlists are dynamic —
never size to today's). It shares the committee machinery built here, and its skill is
measurable for free (picked-vs-unpicked ordinal outcomes). Phase C's dossier tier can reuse
the same shape. Deferred until the pool exists.

## 8. Build sketch (one focused round + review, on founder go)

- `ai/research/setup_scan.py`: split `_assess_candidate` into fetch/detect (once) +
  assessment (fanned); three panel prompts sharing the contract lines with
  `VETO_GRADE_SYSTEM_PROMPT`; bull∥bear then judge; pairing + control hash in the scan loop;
  `MULTI_ANGLE_AB_ENABLED` read once per run, fail-closed to single-voice;
  `reasoning_meta`/`bear_case` plumbing into registration + artifact payload; bear-case line
  in the paper's per-idea section; disagreements line in the paper.
- `db/idea_outcomes.py`: `reasoning_meta` column (migration-pattern add); `bear_case` +
  `reasoning_meta` passthrough in `register_ideas` **with the `_has_fabricated_probability`
  screen** (mirror of :528).
- Tests (targeted modules named in the brief): control-hash determinism + balance;
  origin-never-forks (both control cases); control-error parity with today's error path;
  shadow-error stamping; honesty screen on `bear_case`/`reasoning_meta`; meta round-trip;
  M1 disagreement query incl. alpha adjudication; flag-off = byte-identical behavior to
  today.
- First M3 drift report after week one; K1 checkpoint at 30 trading days.
