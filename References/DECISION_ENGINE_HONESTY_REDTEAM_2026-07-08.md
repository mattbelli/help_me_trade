# Decision Engine — Honesty Red-Team (2026-07-08)

*Adversarial workflow audit of every path where a **score / win% / EV / rank / target** reaches the user.
Question: can an **unearned number** (fabricated, miscalibrated, look-ahead-poisoned, or stripped of its
honesty label) reach the user as if it were validated conviction — the "single most dangerous thing this app
could show" (CLAUDE.md ★ Decision Engine guardrail)?*

- **Workflow:** `decision-engine-honesty-redteam` (run `wf_0d531860-d36`). 6 finder lenses (Opus) → 3 adversarial
  skeptics per finding, majority-refute kills it (Opus) → synthesis (Opus). 79 agents, 0 errors, ~2.9M tokens, ~17 min.
- **Result:** **20 confirmed findings → 13 distinct defects. 2 CRITICAL, 4 HIGH.** The honesty contract is
  **broken at the two highest-stakes surfaces**: the phone approve-while-away card and the chat/mobile LLM.
- **Orchestrator verification:** I personally re-read every cited `file:line` for the two criticals and the
  highs. **All confirmed against real code** — see notes inline. The numbers are **not LLM-fabricated** (they
  are genuine Platt-scaled scorer outputs); the defect is that the honesty flags the codebase *itself computes*
  (`low_confidence`, `sufficient`, `n_samples`) are **stripped at every user-facing transport**.

---

## The one decision I need from you (product voice) — everything else is mechanical

When a score is `low_confidence` / `!sufficient` (i.e. the edgeless generic scorer, which is *every* score
today at n=447), on each surface do we **SUPPRESS** the number, or **SHOW IT WITH A BADGE**
("provisional · n=X · not edge-validated")?

**My recommendation — split by surface:**
- **SUPPRESS on the glanceable action surface** (phone Approve/Reject card): there's no room to caveat a bare
  "56% win" beside an Approve button; a number you can't label there is worse than no number. Show the trade +
  an "unvalidated setup" tag, no %.
- **SHOW-WITH-BADGE on analytical surfaces** (chat prose, ticker widget, DecisionCard, journal): the info is
  useful when it's honestly framed as heuristic/provisional.

Pick one treatment (or "suppress everywhere" / "badge everywhere") and Fix Group A + B below execute immediately.

---

## Verified findings (most-dangerous-first)

*Orchestrator-confirmed against source. `[✓ code-verified]` = I re-read it myself this session.*

| # | Sev | file:line | Seam (where the user sees it) | The wrong number shown | Fix |
|---|-----|-----------|--------|--------|-----|
| 1 | **CRITICAL** `[✓]` | `mobile/widgets/trade-proposal.tsx:39` (+`signals/push.py:630-643`) | iOS lock-screen + Dynamic Island **Approve/Reject** card — the tap-to-approve-real-money surface | Edgeless "56% win" beside Approve; `TradeProposalState` (l.19-30) has **no honesty field**, and the one flag push.py forwards (`validated`) isn't even a key the scorer emits → always dropped | Add honesty field to `TradeProposalState`; forward `low_confidence`/`sufficient` in `_la_content_state`; **suppress win% unless validated** |
| 2 | **CRITICAL** `[✓]` | `ai/perception/context.py:185-197` | `get_context` → chat/mobile **LLM prose**; ticker widget (`company-info-sections.tsx:276-277`); LLM reports | Bare `win_prob≈0.54`/EV with `low_confidence`/`sufficient`/`n_samples` **deleted** — and the tool tells the LLM it's "calibrated" | Carry the 3 flags through `_compact_decision_score`; badge/suppress downstream |
| 3 | **HIGH** `[✓]` | `signals/decision_scorer.py:140` (+`ev_table.json` [0.3,0.4)) | proposal.score → phone / chat / journal | **EV +3.93%/trade from n=8** paired with a **38.8% (sub-coinflip) win%**, `sufficient:false` — shaped identically to validated buckets | `null` `ev_pct`/`ci_low`/`ci_high` when matched bucket `sufficient` is False |
| 4 | **HIGH** `[✓ shape]` | `research/idea_tracker.py:127` | Idea Outcomes widget headline + **calibration ledger** | Creation-day bar (`< as_of_date` includes it) lets an intraday idea "trigger" off price action that printed **before it existed** → win% inflation (the Fable-caught class) | `<= as_of_date` to skip creation-day bar — **but confirm same-day entry isn't a valid trigger first (your call, #4 below)** |
| 5 | **HIGH** | `performance-stats-widget.tsx:117` (+`db/analytics.py:746`) | Cockpit "Win Rate" tile, beside net-cash Realized P&L | Unreliable **FIFO** win-rate shown authoritative; honest net-cash `win_rate` (`analytics.py:745`) sits **unused in the same payload** | Bind to `dashboard.win_rate` (net-cash) or label "approx (FIFO)" |
| 6 | **HIGH** `[✓]` | `ai/chat/tools.py:108` | `get_context` schema the model reads **every turn** | Edgeless scorer advertised as "**the calibrated decision score**" — primes every LLM rendering (all 3 skeptics raised severity) | Reword: "provisional / low-confidence score (edgeless until ~1k setups)" |
| 7 | MED `[✓]` | `signals/decision_scorer.py:141` (+`proposal-score.tsx`) | Proposal/DecisionCard "Win N% · CI x–y%" | "Win 39% · CI 41–93%" — CI is the *empirical-win* Wilson band, **excludes its own point estimate**; 93% upper bound dresses a loser | Don't caption the empirical CI as the win_prob interval; suppress when `!sufficient` |
| 8 | MED | `ai/chat/context.py:184-185` | Per-turn "track record" block → LLM prose | "`· win% 58`" per past decision, no label, framed as earned | Suppress/annotate when `low_confidence` |
| 9 | MED | `performance-stats-widget.tsx:116` | Cockpit P&L/Win tiles under a Schwab account | On failed scope resolution, aggregate **ALL-REAL** P&L (`scope_fallback=true`) shown as one account's | Read `scope_fallback`, show the banner already at `performance-page.tsx:395` |
| 10 | MED | `ai/perception/resources.py:166` | Chat `query('journal')` → LLM prose | Per-row win%/EV with flags dropped; edgeless rows look like validated conviction | Include `low_confidence`+`sufficient` in `_compact_decision` |
| 11 | MED | `frontend/components/trades/trades-page.tsx:329` | Trades page headline "Win rate" | FIFO headline, no caveat — same field is tagged `unreliable_fifo` before it reaches the LLM | Carry `unreliable_fifo` to UI / "approx (FIFO)" |
| 12 | LOW | `performance-page.tsx:541` | /performance realized headline caption | Net-cash source-of-truth $ **mislabeled** "FIFO closed trades" (value correct, provenance inverted) | Relabel "net-cash realized (last-flat boundary)" |
| 13 | LOW | `settings-trades-panel.tsx:372` | Settings → Trades headline | Unlabeled FIFO win-rate; sub "stock round-trips" mislabels a stocks+**options** count (`db/trades.py:492`) | FIFO caveat + "round-trips (stocks + options)" |

---

## Remediation plan (sequenced — ready to execute on your go)

**Fix Group A — honesty-flag transport** *(closes #1, #2, #8, #10; defuses #6)*. The flags exist at
`decision_scorer.py:143-148`; just stop dropping them:
- `ai/perception/context.py` `_compact_decision_score` — carry `low_confidence`, `sufficient`, `n_samples`.
- `ai/perception/resources.py` `_compact_decision` — same.
- `ai/chat/context.py:184-185` — suppress/annotate win% when `low_confidence`.
- `ai/chat/tools.py:108` — reword the schema description.
- `signals/push.py` `_la_content_state` — forward `low_confidence`/`sufficient` (drop the dead `validated` map).
- `mobile/widgets/trade-proposal.tsx` — add honesty field; suppress `winProb` unless validated. **(code lives
  here but the build+deploy is the Mac agent's — cross-repo coordination note.)**
- ticker `company-info-sections.tsx:276-277` + `proposal-score.tsx` — badge/suppress per your decision above.

**Fix Group B — gate the broken numbers** *(closes #3, #7)*: at `decision_scorer.py:129-143`, when the matched
bucket's `sufficient` is False, `null` out `ev_pct`/`ci_low`/`ci_high`; never caption the empirical-win Wilson
CI as the `win_prob`'s interval.

**Fix Group C — FIFO/net-cash discipline** *(closes #5, #9, #11, #12, #13)*: this is just implementing the
existing CLAUDE.md policy ("lead with net-cash, flag FIFO"). Prefer the net-cash field already in each payload;
tag any FIFO stat "approx (FIFO)"; wire the `scope_fallback` banner. Low-controversy — I can knock these out.

**Needs your judgment — #4 idea-tracker look-ahead:** flip `< as_of_date` → `<= as_of_date` (skip the
creation-day bar) **only if** same-day intraday entry isn't a legitimate trigger for these ideas. This poisons
the *calibration ledger* (the ★ "calibrated not fabricated" backbone), so it matters — but the fix depends on
intended entry semantics. Confirm.

---

## Seams the 6 lenses did NOT cover — audited in the follow-up run

All six lenses pointed at the **edgeless generic scorer**. They left these unexamined (a part-2 workflow was
launched overnight to cover them — see `DECISION_ENGINE_HONESTY_REDTEAM_PART2_*` if present):

1. **★ The VALIDATED strategy-stats path itself** (`trading/proposals.py:636-641` →
   `signals/strategy_stats.strategy_conviction` / `strategy_stats.json`). *The entire honesty model above rests
   on "real conviction rides per-strategy VALIDATED backtest stats" — yet no lens checked whether those numbers
   are genuinely out-of-sample, look-ahead-free, and fresh, or whether `validated=true` is asserted rather than
   earned.* **If validated conviction is itself in-sample/overfit, every fix above defends a distinction that
   doesn't exist. Highest-value audit.**
2. The **`calibration` perception resource** (`resources.py:541-547,660-667`) — handed to the LLM directly with
   "Quote this when stating confidence"; audit n-gating + base-rate-vs-edge.
3. **Staleness/freshness** of the score's input quote/features + the `model`/`ev_table` artifacts — a confident
   score computed from a stale quote reaches every surface (cf. the RTH price-freeze commit `b41731b`).
4. **Unrealized P&L marks** (`performance-stats-widget.tsx:166` → `account.unrealized_pl`) — MTM on possibly
   after-hours/stale quotes, shown beside the audited numbers.
5. **Other score-rendering widgets** (`journal-widget.tsx` grep hit, scans/screener, agent-written
   research/memory notes) — confirm none re-surface a stripped score.
6. **Proposal PLAN numbers** — entry/stop/scale-out targets + R-multiple/$EV: derived vs LLM-fabricated was
   never actually confirmed either way.

---

## Part-2 verdict (2026-07-08) — the "validated" lane is real research but hollow at the seam

Part-2 (run `wf_305bdf8a-f99`, 53 agents) audited whether "trust validated" is safe. **Verdict: NOT as a
blank-check lane.** The backtests are genuine (survivorship-free Norgate, block-bootstrap, PBO/DSR — "above-
retail research"), but the tier label that separates *earned* from *asserted* is stripped/dropped/never-rendered
on every surface except the web DecisionCard. **8 confirmed, 5 critical.** Orchestrator-verified against code:

1. **★ THE WIRE IS CUT (verified).** `signals/strategy_scan.py:498` creates every live dip_buy/earnings_drift
   candidate with `rule_id=None` → `trading/proposals.py:639 (if rule_id:)` fails → falls through to `:654`
   `score_symbol()`, the **edgeless** generic scorer. So proposals/phone show the Brier≈base-rate number, NOT
   the validated stat. The validated numbers reach the user today essentially only via chat `get_agent_status`
   (`ai/chat/tools.py:586`). **Fix: pass `rule_id=strategy_id` — but only AFTER 2–5 below, or false confidence
   detonates on the phone.**
2. **No tier gate at the number level (verified).** `signals/strategy_stats.py:186` returns bare
   `win_prob = s.get("win_rate")` for EVERY tier — so `strong-unverified` (dip_refined, steepyc_dip = 0.558,
   same as validated dip_buy) and `pilot` (vix_spike, n=10) emit the same confident number as a real edge. Only
   the sibling `validated` string distinguishes them.
3. **The tier string is stripped on machine-read surfaces** (`resources.py:166` `_compact_decision` drops
   `validated`/`note`/`basis`) — same Part-1 failure, reproduced on the "trusted" path.
4. **earnings_drift's "validated" +0.89% is a survivorship upper bound** — the team's OWN Fable memo
   (`research/fable/2026-07-02_backtesting-strategy.md:36,55`) flagged it + wrote the caveat; never applied
   (artifact untouched since `feaad84`, 2026-06-26). dip_buy's note also omits that the unconditional dip ≈ties SPY.
5. **No freshness gate** (`strategy_stats.py:131` gates on file existence only; `as_of` never rendered; already
   stale: 2026-06-26). **Param-drift:** win% keyed to `strategy_id`, never re-checked vs user-editable
   universe/stops — edit the universe, keep the "validated 56%".

**The one honest surface = the web DecisionCard** (`proposal-score.tsx`): renders the validated/strong-unverified/
exploratory badge, the caveat note, a "no validated edge" fallback, and thin-data warnings. **It is the
remediation template — every other surface (phone, chat, perception, ticker) must copy it.**

## Consolidated remediation — ONE ordered pass (part-1 + part-2)

**Sequencing principle: label / gate / caveat at every delivery seam FIRST, reconnect the `rule_id` wire LAST.**
Flowing real validated numbers into surfaces that still strip the caveats just moves the false confidence onto
the phone. Default = **suppress**: a win%/EV renders only when `validated=="validated"` AND fresh AND the plan
still matches the backtested config.

1. **Carry honesty context through the compactors** (part-1 #2/#8/#10, part-2 #2/#3).
   `ai/perception/context.py` `_compact_decision_score` → carry `low_confidence`/`sufficient`/`n_samples`, drop
   the number when `low_confidence`/`!sufficient`. `ai/perception/resources.py` `_compact_decision` → carry
   `validated`/`note`/`as_of`, drop win%/EV unless `validated=="validated"`. `ai/chat/context.py:184` → same.
   `ai/chat/tools.py:108` → reword ("provisional/low-confidence").
2. **Gate at the number source** (part-2 #1/#2, part-1 #3/#7). `signals/strategy_stats.py:186` → emit
   `win_prob`/`ev_pct` ONLY for `validated=="validated"`; lower tiers get `win_prob=None` + keep `note` (mirror
   the regime-local pattern already in that file). `signals/decision_scorer.py:140` → null `ev_pct`/`ci` when
   the bucket is `!sufficient`; never caption the empirical-win CI as the win_prob's interval.
3. **Apply the caveats the team already wrote** (part-2 #4). Add the earnings_drift survivorship caveat (from
   `research/fable/2026-07-02_backtesting-strategy.md`) + the dip_buy ≈ties-SPY note to `strategy_stats.json`;
   re-stamp `as_of`. (Ideally regenerate on the PC, but the caveat text can land now.)
4. **Freshness + param-drift guards** (part-2 #5). Age-gate `load_strategy_stats`; render `as_of` on the card;
   stale → downgrade to unvalidated. Flag when a proposal's universe/stops differ from the backtested config.
5. **Propagate the tier label everywhere** — copy the honest web DecisionCard (`proposal-score.tsx`) to
   `signals/push.py` `_la_content_state` (forward tier, suppress winProb unless validated) + the mobile widget
   (mac coordination) + confirm the ticker panel matches.
6. **Reconnect the wire — LAST** (part-2 #1). `signals/strategy_scan.py:498` → pass `rule_id=strategy_id` so
   validated strategies attach their real stats. Safe ONLY after 1–5.
7. **FIFO/net-cash discipline** (part-1 Group C, part-2 #8) — independent, anytime.

*Forward (Decision Engine v2, shaped by the 07-08 founder discussion — NOT part of this remediation):*
*conditional calibration (per-strategy backtest sliced by regime/RVOL/trend → setup-specific win%, n-gated) and*
*overlap handling (confluence via backtested CONJUNCTIONS, EV-not-win% selection, independence check, single*
*position + primary owner for attribution/caps). Both are deep-research, not display tricks.*

## Status

- [x] Part-1 + Part-2 audits run + orchestrator-verified. Reports written.
- [x] **Founder decision: SUPPRESS** (number does not render until validated — applies to BOTH scorers).
- [ ] **Consolidated remediation** (part-1 + part-2 → one ordered fix list) — offered; execute on founder go.
  Order: fix the compactors/tier-gate/caveats/freshness FIRST, THEN reconnect the `rule_id` wire.
- [ ] #4 idea-tracker same-day-entry semantics — still needs founder judgment.
- [ ] Board card + BUILD row when fixes ship (board.json still has another agent's uncommitted changes).
