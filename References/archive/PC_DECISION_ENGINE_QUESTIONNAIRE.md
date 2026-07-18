# PC Agent Questionnaire — Decision Engine state (answer, don't build)

*You are a coding agent on the founder's **Windows PC** (where Norgate/NDU runs). We need to know **exactly
what already exists** for the Decision Engine so we don't redo work. **This is a fact-finding task — do NOT
train, pull, or change anything.** Just investigate and answer every question below with concrete evidence
(file paths, row counts, dates, numbers, command output). If something is unknown, say "unknown" + what you'd
need to check — never guess.*

## How to answer
1. `git pull origin main` (gets this questionnaire + the probe script).
2. Run the probe and paste its FULL output at the top of your answer:
   ```
   python tools/pc_decision_engine_status.py
   ```
3. Then answer A–H below, each with evidence. Keep it tight; numbers over prose.

Context you can assume (already verified on serv): the LIVE serv scorer is the **447-sample Alpaca toy**
(`research/artifacts/meta.json` → n_samples 447, 2023–2026, not survivorship-free). A real Norgate *pull*
(~909 sym / 2.9M bars / 16y) + *strategy* validation were done previously; the runbook is `References/NORGATE_PULL.md`.
A regime layer (`research/regime.py`) exists on branch `research/decision-engine-data-layer` but NOT on main.
What's unclear is the state of the **decision-SCORER** model on Norgate.

---

## A. Git / branch state
1. What branch is the repo on right now, and is the working tree clean or dirty? (paste `git status`.)
2. Is there local/unpushed decision-engine work? `git log --oneline origin/main..HEAD` and
   `git branch -vv` — list any branches with uncommitted or unpushed commits relevant to the engine/research.
3. Does branch `research/decision-engine-data-layer` exist locally, and is your local copy ahead of origin?

## B. The Norgate bars pull (`data/research/bars.db`)
4. Does it exist? Size, and per the probe: which `source` values, how many distinct symbols, total rows?
5. What is the **date range** of the bars (min/max)? Did it actually go back toward **1990**, or stop at ~16y?
6. Which **universe** was pulled (which Norgate "Current & Past" watchlist — S&P 500? Russell 3000?), and is it
   **survivorship-free** (Platinum, includes delisted)? Is SPY present (the benchmark)?

## C. The labeled dataset (`data/research/labeled.db`)
7. Does it exist? Total labeled setups (rows), and the `source` (norgate vs alpaca)?
8. Date range and outcome breakdown (wins / losses / timeouts)? Is it **≥1,000 setups** (the threshold below
   which artifacts stay `low_confidence`)?
9. What `--stride`/labeling params were used (if recorded)? Is it the SAME triple-barrier labeling as `research/labeling.py`?

## D. Trained scorer artifacts + did the model show EDGE
10. Do real artifacts exist locally in `research/artifacts/` (model/calibrator/ev_table/meta)? Per `meta.json`:
    `data_source`, `n_samples`, `survivorship_free`, `date_range`, `base_rate`, `brier`, `low_confidence`,
    `n_features` + `feature_names`.
11. **Are these the Norgate artifacts or the 447-Alpaca toy?** (Check `data_source`/`n_samples`.) Are they
    committed/tracked in git, or untracked/local-only?
12. **Did the Norgate model show edge?** i.e. is `brier` meaningfully below the base-rate baseline, and is the
    per-bucket EV/win% separated (not all ~0.5)? The prior note called the generic Norgate pull "**edgeless**" —
    do you have evidence confirming or refuting that (a training log, the printed EV table, a notes file)?
13. Were the overfitting gates (DSR / PBO from `research/overfit.py`) ever run on the scorer model? Results?

## E. Feature breadth
14. Does `research/features.py::compute_features` use ONLY the ~22 price/technical features, or were
    **regime / S-R-levels / fundamentals** features integrated into it? (The probe greps for these.)
15. Is `research/regime.py` wired INTO the scorer's feature vector, or is it a standalone module that was never
    fed to `compute_features`/`train.py`? Same question for `research/levels.py` and `research/fundamentals.py`.
16. If features were broadened anywhere, where, and was train/serve parity considered (would the LIVE
    `signals/decision_scorer.score_symbol` be able to compute the same features from just symbol+SPY bars)?

## F. Strategy backtests on Norgate (separate from the scorer)
17. Confirm what strategy validation was actually run on the Norgate data (dip / dip_refined / earnings / the
    combiner). Paste or point to the key numbers (setups, win%, CAGR/Sharpe, PBO) and the notes file/commit.
18. Is any of this newer/different from what main's HANDOFF records (dip 21,454 setups, combiner PBO 0.18)?

## G. Code reconciliation (branch vs main)
19. Which decision-engine modules exist ONLY on `research/decision-engine-data-layer` and never landed on main?
    (e.g. `research/regime.py`, `research/databento_loader.py`, the /ES profile-MR arc, `setstudy.py` …) —
    list them. Which are worth bringing to main?
20. Is there anything on the PC (a notes/results doc, a scratch script, a local-only DB) that captures findings
    NOT reflected anywhere in git? If so, summarize it.

## H. Your assessment
21. In one paragraph: what is **DONE** vs **OPEN** for getting a real, edge-bearing, survivorship-free decision
    **scorer** live on serv?
22. What is the **single biggest blocker** to a deployable Norgate scorer model — is it (a) the model is
    genuinely edgeless on current features → needs feature breadth, (b) artifacts exist but were never
    committed/activated, (c) the labeled set is too small/biased, or (d) something else?

---

**Deliverable:** the probe output + answers A–H, pasted back. **Change nothing, commit nothing, train nothing.**
Once we have this, we'll decide the minimal next step (activate existing artifacts vs broaden features + retrain)
— with zero redo.
