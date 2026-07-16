# earnings_drift — is the extra day worth anything? (PC experiment brief)

*Written 2026-07-16. Self-contained; you don't need the conversation that produced it. Read all of it —
the framing decides whether the output means anything. Companion:
`References/EARNINGS_DRIFT_SIZING_EXPERIMENT.md` (the sizing question, already settled).*

## The strategy, in one paragraph

`earnings_drift`: if a company's PRIOR earnings produced a blowout-UP price reaction
(`close[j+1]/close[j-1]-1 >= 5%`), buy it **exactly `entry_lead_td=10` trading days before its NEXT
earnings** and sell **`exit_lead_td=1` trading day before** — so the hold is **always 9 td** and it
**never holds through the announcement**. Validated on Norgate survivorship-free 2010–2026:
**n=6,624 / 2,044 distinct dates / 57.2% win / +0.89% per trade / CI [+0.43, +1.28] / 16 of 17 years
positive.** Builder: `research/earnings_strategy.py` (`_symbol_setups`, `:134-198`).

## The question

**`exit_lead_td=1` exists ONLY because we could not tell whether earnings printed before the open (BMO)
or after the close (AMC).** Selling on the day *of* would sometimes land after the report — the one thing
this strategy must never do.

But if a company reports **AMC**, the report lands hours *after* that day's close. So we could hold
through day D and sell at **D's close**, still safely ahead of the print — **one extra day of drift on a
9-day hold (~+11% more exposure to the thing the strategy harvests).**

**Does that extra day actually add anything?** Nobody knows. It has never been tested.

## Live feasibility: ALREADY CONFIRMED — do not re-litigate it

This was checked before writing the brief, so the experiment is worth running:
- **Finnhub/AlphaVantage supply the FORWARD session** in `earnings_calendar.hour`. Measured on the live
  DB right now: of **957 future rows, 544 (57%) have a known session and 247 (26%) are `amc`.**
- That source is *authoritative*, not inferred: **companies pre-announce their session weeks ahead**
  ("we will report after market close on July 30"). Finnhub's forward hour IS that schedule.
- Live design (already decided): take the extra day **only** where the forward hour says `amc`; **fail
  closed** on `bmo` and on the 43% that are null → those keep `exit_lead_td=1`.

⇒ **If this experiment says the extra day pays, we can ship it. That's why it's worth your time.**

## ⚠️⚠️ THE TRAP — read before touching the data

For the BACKTEST you must derive the historical session from **SEC EDGAR 8-K `acceptanceDateTime`**
(Finnhub's free calendar only serves a rolling ~30-day window — it has no history). **That derived hour
is DIRECTIONALLY BIASED and you must not treat it as ground truth.**

**Why (structural, not statistical): you cannot file an 8-K before you release the earnings.** So
`acceptanceDateTime` is a **lower bound on lateness** — filing lag can push a BMO release into looking
like `dmh` or `amc`, but **never the reverse**.

Measured 2026-07-16, EDGAR-derived hour vs Finnhub's on 64 shared events:
```
AGREE 59/64 (92%)     bmo->bmo 33 ✓    amc->amc 26 ✓
                      bmo->amc  2 ✗ (HRL, PEP)
                      amc->dmh  2 ✗ (UAL, JBHT)
                      bmo->dmh  1 ✗ (C)
```
**Every error runs one way.** ⇒ EDGAR saying `bmo` is certain; **EDGAR saying `amc` is ~7% wrong (2 of
28) — and those are really BMO releases filed late.**

**Consequence for this experiment:** ~7% of your "AMC" bucket are actually BMO reporters. In the sim,
those get held **through** their print, so their extra-day return contains a **real earnings gap**.

**This is CONTAMINATION, and it runs in the CONSERVATIVE direction** — stray earnings gaps add variance
and (mostly) noise to the AMC bucket, so **the measured extra-day value is a FLOOR.** Live will use
Finnhub's clean forward hour. **Say this explicitly in the writeup**; do not "correct" for it silently.

Also note **`dmh`** (filed during market hours) is a real bucket — mostly filing lag, not genuine
intraday announcements. **Treat `dmh` as UNKNOWN → excluded from the AMC arm** (fail closed).

## Task 1 — capture the raw timestamp (small)

`research/earnings.py` already walks every 8-K Item 2.02 for the Norgate universe **including the older
`filings.files[]` shards** (`_fetch_submission_blocks`, `:84-106`) — it just **discards
`acceptanceDateTime`** and stores only `filingDate` (`_parse_earnings_from_submissions`, `:47-62`).

- Add **`accepted_at TEXT`** to the `earnings` table (`:27-34`) and populate it from
  `recent["acceptanceDateTime"][i]`, column-aligned with `form`/`filingDate`/`items` (same index).
- ★ **Store the RAW ISO timestamp, NOT a derived `bmo`/`amc` label.** The label bakes in a threshold
  guess; the raw stamp lets you re-derive under stricter rules later (e.g. "only trust amc if filed
  16:00–17:30 ET *and* this company has never filed pre-open") **without a refetch**.
- Derive the session with the **same rule the live code uses** so the two can't drift —
  `signals/earnings_edgar.py::session_hour`: parse the `…Z` stamp, **convert UTC → America/New_York
  FIRST** (⚠️ load-bearing: compared raw, every before-open filer reads as intraday — JPM files 10:30Z =
  06:30 ET = bmo), then `< 09:30 → bmo`, `>= 16:00 → amc`, else `dmh`.
- This is a one-time refetch (~910 symbols + shards). SEC fair use: **10 req/s and a contact User-Agent
  is required**; `_REQUEST_INTERVAL_SEC = 0.13` is already correct — don't raise it.

## Task 2 — the variant arm

In `research/earnings_strategy.py::_symbol_setups`, today:
```python
entry_idx = jn - entry_lead_td      # exactly 10 td before the next earnings
exit_idx  = jn - exit_lead_td       # 1 td before  -> holding_days always 9
```
where `jn` is the bar index of the NEXT earnings date.

**The `exit_on_amc_day` arm:** `exit_idx = jn` when that event's session is `amc`, else
`jn - exit_lead_td` unchanged.

⚠️ **Two guards — both are correctness, not polish:**
1. **Date alignment.** `jn = bisect_left(bar_dates, next_e)` is the first bar **>= `next_e`**. If the
   earnings date is a market holiday, `bars[jn]["d"] != next_e` and `exit_idx = jn` would sell **AFTER**
   the report. **Only apply the extra day when `bars[jn]["d"] == next_e` exactly**; otherwise fall back
   to `jn - exit_lead_td`.
2. Keep the existing bounds guard working (`:158`): `exit_idx <= entry_idx or exit_idx >= len(bars)`.

`holding_days` becomes 10 for extra-day trades and stays 9 otherwise — the portfolio sim consumes
`holding_days`, so this must be written correctly, not assumed.

## Task 3 — pre-registered arms. Run ALL. Report ALL.

**`n_trials = 4.** Sweeping and reporting the winner is multiple testing (`research/overfit.py` has
DSR/PBO).

| Arm | exit rule | why |
|---|---|---|
| **A — control (MANDATORY)** | current, `exit_idx = jn - 1` for everything | the validated baseline |
| B — extra day on AMC | `jn` when amc, else `jn - 1` | the proposal |
| C — extra day on AMC, **agreement subset** | as B, but restrict to events whose session is corroborated (see below) | contamination-cleaned |
| D — extra day on **everything** | `jn` always | ⚠️ deliberately WRONG (holds BMO reporters through their print) — the **negative control** that proves the sim is actually capturing earnings gaps. If D isn't visibly worse than A, your session labelling or exit indexing is broken. |

**Arm D is not optional.** Two ideas already died this month because someone insisted on the boring
control arm (`EARNINGS_DRIFT_SIZING_EXPERIMENT.md` §Round 3: a fixed-fraction control killed the whole
rebalancing proposal; the apparent gain was a deployment effect). **Run the control.**

For **Arm C**, corroboration options (pick one, state which): restrict to events where the company's own
EDGAR history is **100% session-consistent** across its other quarters (measured: **78% of companies
are** — but ABBV 4amc/4bmo and AMP 4/4 are coin flips), or require the filing to land in a tight window
(e.g. 16:00–17:30 ET) that filing lag is unlikely to explain.

## Task 4 — the contamination diagnostic (this is the point, not a footnote)

**Compute the extra day's return in isolation** — `close[jn] / close[jn-1] - 1` — and report its
distribution **split by EDGAR label** (`amc` / `bmo` / `dmh`):

- A **true** AMC event's extra day is pure pre-report drift → **tight, modest** distribution.
- A **false** AMC (really BMO, filed late) contains the **earnings gap** → **fat tails, likely bimodal**.

⇒ **The AMC bucket's own distribution reveals the contamination.** Report mean, median, stdev, and the
1st/99th percentiles per bucket. If the amc bucket's tails look like the bmo bucket's *day-after* returns,
you're seeing the 7%.

Sanity anchor: the `bmo` bucket's `close[jn]/close[jn-1]-1` **is** a post-report return by construction
(a BMO report lands before day D's open) — so it gives you a free reference for what an earnings-gap day
looks like in this data. **The amc bucket should look nothing like it.** If it does, the labels are junk
and Arm B is meaningless.

## Standing rules (these have already bitten us)

- **`git pull` on the PC first.** The last run rediscovered a `cost_pct=0.30` double-count that main had
  already fixed (PR #28, `research/pipeline-honesty-fixes`: CV leakage + next-open fills + embargo +
  cost single-sourcing).
- **Costs:** `earnings_strategy.py:166` stores returns **already NET** (`net_ret_pct = gross_ret_pct -
  cost_pct`). If you run `research/portfolio.py`, **pass `--cost-pct 0.0`** or you double-count. Confirm
  what cost is actually baked into the rows (the builder records it in its summary, `:227/:249`).
  ⚠️ The extra-day arm **does not add a trade** — it moves the exit one day later. **Cost per trade is
  unchanged.** Do not add a round trip for it.
- ⛔ **NEVER use `data/research/bars_validation_slice.db`** — verified survivor-biased (0 of 496 symbols
  stop trading before 2025-06; SIVB/FRC/ATVI/VMW absent). It's a current-constituents fixture for
  `momentum_validate.py` correctness only. No number from it may be published.
- **The levels are fiction; only rankings are valid.** The portfolio sim marks open trades by **linear
  accrual**, which suppresses daily vol ⇒ inflates Sharpe and shallows every trough. A Sharpe of 3.07 is
  not a real number. Report **A-vs-B deltas**, not absolute Sharpes.
- Known pre-existing fidelity gap: the builder **fills at signal close**; live fires intraday at market.
  Unrelated to this arm, but it applies to every number here.

## Deliverable

1. The 4 arms: per-trade mean/median, win rate, **block-bootstrap CI on the A→B delta**
   (`research/significance.py` — it clusters overlapping trades by entry date; the naive IID CI is
   labelled OVERSTATED for a reason), n per arm.
2. The extra-day return distribution table split by label (Task 4) + your read on whether the labels are
   clean.
3. **Arm D's result** — confirmation the sim actually sees earnings gaps.
4. A one-line verdict: **does the extra day pay, and by how much per trade?** Remember it's a **FLOOR** —
   live uses Finnhub's clean forward hour, so ~7% of your AMC bucket is noise that live won't have.
5. If it does not pay: say so plainly. That's a clean, cheap kill and `exit_lead_td=1` stands on evidence
   rather than on an argument. **Three ideas have already died this way this month** (blowout-magnitude
   ranking, mid-flight rebalancing, and — nearly — this one). That's the harness working.

---

# RESULTS (run 2026-07-15 on the PC, post main-merge honest pipeline)

**VERDICT: the extra day PAYS. +0.23%/trade on AMC trades (a ~25% EV lift on that half of the book),
+0.11%/trade portfolio-wide, block-CI excludes zero, and it survives the most conservative fill. Ship it
(fail-closed live design as specified above). And per the contamination logic, these numbers are a FLOOR.**

Runner: `research/_edgehunt/extra_day_experiment.py` (committed). Task 1 landed in `research/earnings.py`
(`accepted_at` column, raw ISO stamp, earliest-acceptance-wins on same-date refilings); full universe
refetched — **53,315 events, 100% with `accepted_at`** (654/909 symbols; the 255 unresolved are the known
delisted-ticker gap, unchanged from the base dataset). Sessions derived with the LIVE rule
(`signals/earnings_edgar.py::session_hour`, imported — not reimplemented). Labels: **3,235 amc / 3,054
bmo / 335 dmh**, zero unknown; 6,622/6,624 next-earnings days bar-aligned (guard 1 bit twice).

**Equivalence check: MATCH** — arm A reproduced the stored validated dataset exactly (n=6,624, mean
+0.8900%), so the arm deltas sit on the real baseline, not a re-derivation.

## The four arms (per-trade net %, cost unchanged — the extra day adds no round trip)

| Arm | n extra-day | mean | win% | Δ vs A (block-bootstrap) |
|---|---|---|---|---|
| A control (`jn-1`) | 0 | +0.890 | 57.2% | — |
| **B amc extra day** | 3,217 | **+1.000** | 57.8% | **+0.110 [+0.025, +0.198] p=0.007**; conditional +0.227 [+0.058, +0.402] p=0.005 |
| C corroborated amc (16:00–17:30 ET) | 2,999 | +0.991 | 57.7% | +0.101 [+0.025, +0.161] p=0.004 |
| D everything (NEGATIVE CONTROL) | 6,595 | +1.065 | 56.3% | +0.175 [−0.142, +0.291] p=0.25 — **not significant** |

**Robustness cut (not a registered arm): B with a day-D OPEN exit** (market-on-open — overnight drift
only, zero same-session pre-print exposure): Δ +0.098 [+0.065, +0.169], **p<0.0001**, conditional +0.206.
Most of the extra day's value arrives in the overnight gap into day D and survives the most conservative
executable fill. The MOC close captures slightly more but is not load-bearing.

## Negative control (arm D) — the sim sees earnings gaps, as required

D's mean is *higher* than B's (post-blowout names carry positive average surprises — PEAD-world, not
free money) but its **significance dies** (CI spans zero, p=0.25), its **win rate drops** (56.3% vs
57.8%), and its variance explodes — the lottery-ticket profile of holding through prints. Combined with
the Task-4 table this confirms the labelling + exit indexing actually capture announcement risk.

## Task 4 — extra-day return `close[jn]/close[jn-1]-1` by EDGAR label

| label | n | mean | median | stdev | p1 | p99 |
|---|---|---|---|---|---|---|
| amc | 3,234 | +0.223% | +0.162% | **3.19** | −8.65% | +9.63% |
| bmo (gap-day reference) | 3,053 | +0.083% | +0.284% | **7.15** | −21.04% | +18.19% |
| dmh (excluded, fail closed) | 335 | +0.603% | +0.469% | 5.34 | −12.22% | +17.20% |

**The amc bucket looks nothing like the bmo gap-day reference** — tight and modest (stdev 3.19 vs 7.15,
tails ±9% vs ±20%) → the labels are clean enough to act on. The amc tails are slightly fatter than a pure
drift day, consistent with the known ~7% late-filed-BMO contamination; arm C (contamination-cleaned)
matching arm B says that noise doesn't drive the result. **Live uses Finnhub's authoritative forward
hour, so the shipped version should do modestly better than measured.**

## Caveats (unchanged from the brief's standing rules)
Per-trade deltas only in the tables above — no portfolio-sim levels quoted there. Entry fills at signal
close (known pre-existing fidelity gap, applies equally to every arm). EDGAR-derived history is the
biased-but-conservative proxy; the live gate is Finnhub `amc` + fail-closed on `bmo`/`dmh`/null, exactly
as pre-specified.

## SHIPPING SPEC (founder-approved 2026-07-15): sell at day-D OPEN, not close

Portfolio-level check (accrual-marked sim, fixed 10%/position max 10 concurrent — the sizing policy chosen
in `EDGE_HUNT_FINDINGS.md` Round 6; levels are model-marked and optimistic, the ORDERING is the result):

| exit on AMC day | CAGR | maxDD | Sharpe |
|---|---|---|---|
| baseline (day before, `jn-1` close) | 20.43% | 21.12% | 3.28 |
| extra day, sell at CLOSE (MOC) | 21.62% | 24.28% | 3.35 |
| **extra day, sell at OPEN (MOO)** | **22.27%** | **22.87%** | **3.54** |

The overnight gap into report day carries nearly all the drift (+0.206 of the +0.227 per-trade delta);
the report-day session adds ~2bps at the cost of much higher variance and the deeper drawdown. The OPEN
exit wins on every axis AND is operationally safer: market-on-open, flat ~6.5h before the earliest AMC
print, and the ~7% mislabeled-BMO contamination hurts less. Selection honesty: the open-exit was a
robustness cut (specified before results, motivated by conservatism), not a pre-registered arm — but both
variants clear significance independently and the pick is the MORE conservative mechanic.

**Live rule: enter 10td before earnings; exit at the NEXT OPEN after the `jn-1` close when Finnhub's
forward hour says `amc` (i.e. hold overnight into report day, MOO exit), else exit at the `jn-1` close.
Fail closed on `bmo`/`dmh`/null.**
