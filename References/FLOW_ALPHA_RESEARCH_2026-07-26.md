<!-- Source: multi-agent workflow `flow-alpha-identification`, 2026-07-26.
     6 independent Opus lenses (commercial platforms / academic literature / zero-context ideator /
     cross-sectional breadth / failure modes / self-audit of our own prereg), each attacked by its own
     adversarial Opus critic, synthesized by Fable. 13 agents, 0 errors, ~1.08M tokens.
     Founder asked: now that the sub cap is gone, is our method wrong? Is the play drift-from-normal
     PLUS big one-off sweeps? What is actual best practice vs what is merely published? -->

# Whole-market OPRA — where the alpha is (and is not)

> **ORCHESTRATOR VERIFICATION PASS — read this before trusting any claim below.**
> Per CLAUDE.md rule 4, agent assertions are hypotheses until checked against code or live state.
> Checked 2026-07-26 immediately after the run:
>
> **CONFIRMED (verified in code):**
> - `tools/flow_corpus_rollup.py:215` passes the session's own date to `bars_for_date`, so moneyness
>   is bucketed against the SAME session's close. `FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_B.md`
>   line 22 pre-commits verbatim to the **prior** session's close. Real registered-rule violation.
> - Baseline #4 does not describe production. Prereg says "$500k premium, DTE<=30". Production is
>   `MIN_PREMIUM_USD = 25_000` / `MIN_PREMIUM_INDEX_USD = 250_000`, `SWEEP_WINDOW_MS = 500`,
>   `SWEEP_MIN_EXCHANGES = 3`, and **no DTE gate** (`signals/options_flow/constants.py`).
>
> **REFUTED (do not act on these):**
> - "Use `unit_of_measure_qty` instead of the hardcoded x100." The field is `UNDEF`
>   (9223372036854775807) on all 1,592,395 C/P contracts — OPRA never populates it. The
>   adjusted-contract concern may still be real; this fix is not available.
> - "`data/flow_corpus.db` does not exist yet." It exists (197 MB, rolled 2026-07-26 21:07). Does not
>   change the conclusion — nothing is graded, so a re-roll is still free.
> - "We lack OI" / "start an OCC cron today on urgency grounds." We hold 27,221,980 daily OI rows
>   (`data/oi_walls.db::oi_daily`, 2013-04-01 -> 2026-07-17) but on only **3 roots**, so it is a
>   validation sample, not market-wide. The better route the workflow missed: **whole-market OPRA
>   `statistics` prices at $0.00** (verified by `get_cost`: 96,468,746 records for 2025-07-28,
>   72,044,113 for 2024-01-02). It is L0-tier, so unlike `tcbbo` there is **no rolling deadline** and
>   no urgency. Still needs one free pull to confirm open interest is in the payload.
>
> **UNVERIFIED — the synthesis flags these itself; they are listed again at the end of the document.**
> Notably the 12x liquidity-null figure is a SIMULATION, not a measurement, and the 12/208/91
> drift-sweep overlap is one in-sample session.

---

# The Verdict: Both Lanes, Neither Validated — and the Real Work Is Underneath Them

**Yes: run drift-from-normal and big one-off sweeps as two separate lanes, fused as angles, never summed as confirmation.** They are different objects — a state estimator over a day of flow versus an event detector over one order — and on the one session measured they fire on mostly different names (12 overlapping events out of 208 drift / 91 sweep). The prereg's framing of the sweep as a *rival baseline the z must beat* is a category error, and worse, the registered sweep baseline is a strawman: production actually runs $25k/$250k floors with a 500ms/3-exchange cluster and **no** DTE≤30 gate (verified in `signals/options_flow/constants.py`, `sweeps.py`) — not the "$500k, DTE≤30" the prereg wrote down. But the sharper answer is this: **neither lane is where the highest expected value sits right now.** Six adversarial reviews killed nearly every new detector proposed; what survived, with independent corroboration, is a short list of verified corpus bugs, four cheap decisive measurements, and a statistical spine that — as registered — cannot fail. Fix those three things and both of your instincts become answerable. Skip them and every result you generate, drift or whale, is unreadable.

---

## Rivals vs complements: adjudicated

Same population or different? **Different on liquid names, the same event on thin names — and that is measurable, not arguable.** A $500k sweep is ~0.2% of a mega-cap's option day and cannot move a premium share; on a $300k-premium name that same sweep *is* the day and pins tilt to the boundary by construction. So:

- **Fuse as separate angles on liquid names** — genuinely orthogonal reads.
- **On thin names, co-firing is one participant counted twice.** Never grant a confirmation bonus. This is the identical error Addendum D already caught in the three-weightings "consensus."
- **The registered mechanism**: a 2×2 per name-day (`z_event` × `sweep_event`), graded as four independent cohorts (both / drift-only / sweep-only / neither), with the pre-registered prediction *phi > 0.5 in the bottom liquidity quintile, phi < 0.15 in the top*. This came from the self-audit lens and was the only detector-adjacent proposal its critic endorsed outright ("the only proposal whose economic claim is not 'this number is unusual' in a costume").

One more thing the analysts nearly lost and you should not: **your original instinct was temporal** — learn a name's baseline over days, fire on *change*. Five of six lenses quietly replaced that with same-day cross-sectional statistics. The persistence arm (|z|≥1.5, two consecutive same-sign days) is the only registered object with any story for a 3–45 day horizon rather than a 1-day one. Keep it. It is closer to your hypothesis than anything the analysts proposed.

## What the published industry actually gets wrong

Your suspicion is confirmed, and it's worse than suspected:

1. **No vendor has ever published a forward test.** Checked across eight platforms: Tradytics' "80%" has no construction behind it; Market Chameleon's "win rate" backtests option *structure* payoffs, not signal quality; OptionStrat's tracking is MFE on a self-selected set. Zero outcome studies exist. Published practice is display logic.
2. **The universal vendor construct — at/above-ask aggressor flow — is the anti-predictive variable at your horizon.** Pan & Poteshman (RFS 2006), Table 5: the publicly-computable signed-flow signal is price pressure at d+1 and turns significantly *positive* (reversing) at d+2 through d+10. The variable that carries information is the open-vs-close split — which OPRA does not disseminate and no retail vendor has. Building a sharper version of aggressor signing buys precision on the wrong variable.
3. **Row-per-print aggregation double-counts every sweep.** 99.4% of multi-print groups carry one identical BBO snapshot: N prints are one order, one quote observation. Every vendor inflates fragmented orders' "aggression" by their fragmentation, which is a routing artifact. Their 100–400ms sweep windows are guesses at a grouping our tape identifies exactly.
4. **Display floors ($25k/150 contracts) permanently discard the below-floor tape.** We retain it. Alert feeds also mostly don't filter multi-leg legs, even though the sophisticated end of the vendor space (UW's own presets) knows they poison the sign.

Honest counterweight: **our own six analyses mostly failed adversarial review too.** The edge today is not a known signal — it is measurement infrastructure nobody else retains (every raw print + its pre-trade NBBO, whole market, re-rollable for free).

## The one big convergent negative

Four lenses independently proposed recovering direction from the multi-leg 33% of premium ("packages are where institutional intent hides"). **All four died, killed by four independent critics on the same point:** package *geometry* is recoverable from strikes/expiries; package *orientation* is not. A bull call spread and a bear call spread are the same two instruments — only per-leg buy/sell separates them, and that is precisely the quantity leg-allocation destroys. "The allocation cancels in the sum" requires already knowing the signs. When four independent adversaries converge on one kill, treat it as settled: **packages stay a fenced grain until labeled data exists.** The path to labels: a purchased condition-code day (below) and, later, Cboe Open-Close as a validation sample.

## Build list (you can do two or three well — do 1, 2, 3)

**1. Corpus integrity round + schema v4 re-roll — do first, this week.** *Build-as-infrastructure; no prereg needed (it restores registered rules).* The window rolls daily; every day of delay converts free fixes into re-purchases or permanent gaps.
- **Verified look-ahead bug:** `flow_corpus_rollup.py:215` buckets moneyness against the *same session's* close; Addendum B pre-committed to the prior close. Two critics confirmed it independently in code. This contaminates the SHORT_OTM arm — the arm the academic prior lives in — with the day's realized return.
- Hardcoded ×100 multiplier ignoring `unit_of_measure_qty` (adjusted contracts mis-scale premium — the floor, the weight, and the quintile variable).
- Split `mny_bucket='unknown'`: it currently pools index roots with every name lacking a bar, silently deleting the thin tier your mid-cap hypothesis is about.
- Add a `groups` column (distinct ts_event count) and adopt **one sign per ns-group** — the sweep-dedup fix, free, obviously right.
- Declare the delisting/acquisition terminal-value rule *now* — acquired names are the archetypal winners and their bars stop; without the rule, survivorship eats your best cohort.

**2. The measurement round — four numbers, days of work, they decide everything downstream.** *Build-as-measurement.*
- **(a)** Empirical permutation null of the registered z rule, per liquidity quintile, on train. The claimed 12× liquidity-dependent false-positive rate is currently a *synthetic simulation* (single lens, flagged). One script settles whether |z|≥2 needs replacing. Do not touch the threshold before this.
- **(b)** Re-measure the +$553.6M → −$219.3M multileg sign flip on single names, ex-index/ex-ETF. If it's an SPX-condor artifact, most of the contamination panic evaporates.
- **(c)** Validate the ns-collision heuristic for free from data already on disk: the full DBN files retain `ts_recv` (reporting latency separates auto-ex from manually-reported/complex prints), quote sizes, and price granularity. The claim that validation requires buying condition codes was false — the repo held the ingredients.
- **(d)** The 2×2 drift×sweep overlap, with the sweep lane rebuilt to *production's actual parameters*, per liquidity quintile. This is the direct, quantitative answer to your question.

**3. Prereg amendment — the statistical spine.** *Costs a prereg change; worth it because the current rule is near-unfalsifiable, and an unfalsifiable prereg has negative integrity value.* Details next section.

**Queued behind those (start the cheap capture now, defer the builds):**
- **OCC daily open-interest capture: start the cron today.** Open-vs-close is the largest information gap on the tape and the one place the literature's power actually lives. The signals proposed on it died (dOI is unsigned; the allocator formula literally cancelled) — but the *capture* survived every critique, costs ~nothing, and may not be backfillable (unverified — check OCC's archive before treating urgency as fact). Also verify what `db/options_chain_history.py` already holds: it has per-series OI with T+1 backfill; "we lack OI" was false.
- **Buy 2 labeled days of OPRA condition codes** (one quiet day AND one opex day — the pre-committed "normal weekday" choice was the worst possible one) after (c) runs, verifying the vendor passes raw letter codes and the join matches on 100 rows first.
- **Next detector candidates, in order:** RESID-FLOW (unusualness conditional on the name's own clientele — the only proposed detector that survived its critic, because it must beat a naive benchmark rather than a t-stat against zero) and the post-earnings retail-fade (best-evidenced post-2020 finding, two top-journal papers, two-week decay inside your window — **single-lens, uncritiqued; treat with care**). Both are *build-as-testable-hypothesis-with-a-prereg*, and both wait until 1–2 land.
- **Ship both existing lanes to the UI now as perception** — drift events and sweep events, labeled unvalidated, carrying the 2×2 cell tag. "No proven edge" is not a reason to withhold the angle; dressing it as scored would be.

**Do not build (adopted from critiques, all killed):** the OI open/close allocator signal, package direction recovery, the borrow-fee parity sensor (circular; repo already measured parity noise at 3–10× the signal), the liquidity-neutral event budget (its effective-N is built on the sweep double-count), the MAD baseline (inverts the liquidity confound), flow-RVOL as a gate, dealer-gamma state from accumulated aggressor flow, execution-fingerprint cohorts.

## The prereg: change vs freeze

**Change (each is a verified error or a pre-outcome specification fix — legitimate before the test split is touched):**
1. **Correct baseline #4 to production's real parameters.** Factual error, verified in code. "We beat production" under the current spec would be a false statement.
2. **Invert the falsification rule.** "Dead only if indistinguishable at ALL horizons across ALL quintiles" over the declared arm surface is close to unfalsifiable — a null pipeline almost surely produces a surviving cell. Declare **one primary cell** (recommend: midpoint sign, multileg-excluded arm — justified by the measured contamination, not by outcomes — pooled quintiles, terminal alpha vs SPY in ATR units); mechanism *alive only if it passes*. Everything else computed, published, labeled exploratory with FDR reported, no survival authority.
3. **Horizon of record = 10 trading days.** Say plainly, in advance: 12 months gives ~2–4 independent observations at 21/45 days; those horizons are reported but *cannot be confirmed or killed* — a null there is a non-result, not a kill. Anything else is theater.
4. **Apply the t>2.423 bar to time-block-clustered standard errors and require ≥20 independent event dates per cell.** Measured: 898 names = ~8 effective independent observations raw (~30 SPY-residualized). Naive √N is inflated ~10×. Report SPY-beta-residual grading as a co-arm (diagnostic, never the presentable number — residual MAE isn't the heat that stops you out).
5. **Fix the SHORT_OTM arm's moneyness**: |K/S−1|≥2% includes deep-ITM delta-1 substitutes — the opposite of the prior it cites. OTM only.
6. **Add the 2×2 sweep cross-tab and earnings/opex flags** (flags and covariates, never filters).
7. **State universe size on every table.** The top ~1,000 names carry 99.6% of premium; "4,419 names" is bookkeeping, not breadth.

**Freeze (integrity is worth more than any of these tweaks):** |z|≥2.0, the 20-day lookback, the $250k floor, the midpoint rule, tilt's definition, the persistence arm, the 3/10/21/45 grading grid, ATR units, the split dates. If measurement 2(a) empirically confirms the liquidity-dependent null, register a per-quintile amendment *then* — before the test split, on the measured number, not the simulation.

## Claims resting on one lens, no corroboration — verify before acting

- The 12× liquidity-null simulation (empirical check is build item 2a).
- OPRA's Pillar multi-leg condition taxonomy existing upstream and Databento dropping it (plausible, roadmap-cited; verify before purchase).
- The stock-tied T-family blind spot (one options leg + invisible equity leg).
- "OCC OI not freely backfillable" — the one claim being used to create urgency; the action (start a cron) is low-regret either way, but check the archive.
- The 6%/13% drift/sweep overlap — one session, in-sample. The 2×2 on the full corpus is the real number.
- The retail-fade construction — strong published basis, but our specific proxy went uncritiqued.

**Bottom line:** your two-lane instinct is right and cheap to formalize; the prereg needs seven surgical amendments now, before its test window burns on a spec with two verified bugs and an unfalsifiable kill rule; and the week's real alpha is not a new detector — it is making the corpus and the statistics capable of telling you the truth.