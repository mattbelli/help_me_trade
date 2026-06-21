# Decision Engine — Research Findings (Round 1: fusion · calibration · LLM role · validation)

*Deep-research pass 2026-06-21 (110 agents, 27 sources, 25 claims adversarially verified → 23 confirmed,
2 killed). Companion to BUILD.md "★ The Decision Engine". This is the evidence base for HOW to build the
ranked-trade-idea engine. Confidence tags + sources are per-finding; read the caveats.*

## TL;DR — what the evidence says to build
1. **Pipeline shape (high confidence):** heterogeneous signals → a single **conviction score** → a separate
   **calibration layer** → an honest **win-probability**. The **LLM only reads unstructured text + writes the
   narrative/thesis; deterministic code computes every number** (score, prices, probability). This is exactly
   the architecture we committed to — now evidence-backed.
2. **Start SIMPLE — a transparent weighted-composite scorecard, NOT an ML ensemble.** The stronger claim that
   an IC-weighted ML ensemble beats simple models was **REFUTED 0-3**. For a solo dev with limited history, a
   transparent scorecard is the right start; add rolling-**IC** weighting later if it earns its keep.
3. **Win% must come from a calibration layer (Platt scaling), never a raw score** — and is only "honest" with
   enough samples + out-of-sample reliability/Brier validation + multiple-testing controls. Until then, show a
   confidence interval or "insufficient data," not a precise single number. (Backs our calibrated-not-fabricated
   guardrail with method.)

## 1. Fusion / scoring (which method to start with)
- **Best starting point = transparent rule-based / weighted-composite scorecard.** *(medium)* Optionally set
  weights by **rolling Information Coefficient** — IC = cross-sectional Spearman rank corr of predicted vs
  realized next-period returns over a rolling window; **ICRatio = ICmean/std(IC)** weights for stability
  (Grinold & Kahn standard). The IC *mechanism* is sound (3-0); the claim that an **IC-weighted ML ensemble
  beats simpler models was REFUTED (0-3)** → do NOT reach for gradient-boosted-tree ensembles early.
  [arXiv 2508.18592]
- Implication: v1 = explicit, inspectable factor scorecard (the founder can read *why* a 10/10 is a 10/10).
  ML only if/when data + measured IC justify it.

## 2. Calibration (turning a score into an honest win%)
- **Raw model scores are NOT probabilities.** Max-margin methods (boosted trees, SVMs) push scores away from
  0/1 → sigmoid distortion on the reliability diagram; must be corrected by a calibration stage. *(high,
  ICML 2005 Niculescu-Mizil & Caruana)*
- **Use Platt scaling (sigmoid fit), not isotonic, when calibration data is scarce.** Under ~200–1000 labeled
  cases Platt beats isotonic (isotonic overfits); isotonic only wins at 1000+. A solo trader accrues few
  labeled outcomes → **Platt is the correct default.** *(high, same paper)*
- **Validate calibration out-of-sample with reliability diagrams + Brier score.** *(high)*
- **Honest "65% win" requires:** adequate calibration sample + OOS reliability/Brier + multiple-testing
  controls (below). With small samples, **report wide confidence intervals / "insufficient data" — never a
  precise single win-rate.** *(caveat, high importance)*

## 3. Validation (trust before showing numbers on real money)
- **Standard holdout/CV is unreliable for backtests** — the same dataset is effectively tested billions of
  times → false positives near-certain. Quantify with **Probability of Backtest Overfitting (PBO)** via
  **Combinatorially Symmetric Cross-Validation (CSCV)** (installable Python/R `pbo`). *(high, Bailey/Borwein/
  Lopez de Prado/Zhu, J. Computational Finance)*
- **Overfitting is measurable degradation:** higher in-sample Sharpe → lower (often negative) OOS Sharpe.
  Need **strict chronological walk-forward splits** PLUS a multiple-testing correction — the **Deflated Sharpe
  Ratio (DSR)** (Bailey & Lopez de Prado, JPM 2014). Both are needed: DSR can't fix biases hitting every
  config (e.g. look-ahead in factor construction); only a proper chronological split prevents those. *(high)*
- **"Mask-first" tradability filter:** filter for execution feasibility *before* computing signals; don't rank
  ideas on prices you couldn't have traded. Omitting it inflates apparent IC while real OOS Sharpe drops.
  *(medium — principle is standard; the cited magnitude is A-share-specific, single preprint)* [arXiv 2507.07107]
- Related practitioner tooling worth knowing: **triple-barrier labeling + meta-labeling** (Lopez de Prado) for
  turning a signal into labeled win/loss outcomes; **purged/embargoed CV** to prevent leakage.

## 4. LLM role — where it belongs and where it does NOT
- **LLM = narrative/thesis + reading text. Deterministic code = all numbers.** LLMs are specifically weak at
  numerical/comparative reasoning over financial text (removing explicit numeric prompting sharply drops
  accuracy). *(high)* [Sentic; arXiv 2510.05533]
- **Do NOT trust LLM output as a numeric source:** run-to-run sentiment classification is volatile/
  non-deterministic; live LLM trading does **not** consistently beat naive/S&P benchmarks and risk-adjusted
  returns decline over longer horizons. *(high)* [PMC12421730 — Yu 2023; Perlin 2025]
- **Pretraining leakage is real:** backtesting LLM-sentiment is biased when the window overlaps the model's
  training data — via look-ahead AND a "distraction" effect (anonymizing company names *improves* in-sample,
  proving it's not just future-return memorization). Mitigate: anonymize entities, restrict to post-cutoff
  text, per-fold publication cutoffs / embargo windows, rationales citing only pre-decision evidence. *(high)*
  [Glasserman & Lin 2309.17322; 2510.05533]
- **Keep LLM signal generation architecturally separate from risk/portfolio; never alter risk on an
  unverifiable rationale; human review for high-impact actions + regime changes.** *(high)* — **This maps
  exactly onto our existing "agent proposes → human approves → chokepoint executes" model.** [2510.05533]
- For the **sentiment SCORE specifically**, a specialized model (FinBERT) beat vanilla zero-shot LLMs — but
  *conditional* (2-1): capable models with structured prompting can match/beat it. Treat as guidance: consider
  a specialized sentiment classifier for the *number*, LLM for the *story*.

## Refuted / cautions (don't build on these)
- ❌ "IC-weighted ML ensemble substantially beats single models / static weighting" — **refuted 0-3.**
- ❌ "Hybrid LLM-insights-into-quant-models outperform" — **not supported (1-2).** Don't overclaim LLM value.
- LLM-finance papers (2023-26) are fast-moving and leakage-contaminated; several "LLM beats market" results
  dissolve under leakage-controlled testing → **weight the cautionary findings over the optimistic ones.**

## Open questions → design judgment + NEXT research
The verified sources did **not** settle these (treat as design decisions / next research, not cited fact):
1. **Concrete starting weights / aggregation** for fusing technical + sentiment + fundamentals + regime in a
   low-data setup (equal-weight vs rolling-IC vs small logistic regression), and the sample size where each
   becomes trustworthy.
2. **Highest-ROI paid data sources** for retail (social sentiment, SEC Form 4/insider, fundamentals,
   intraday) vs free — **under-evidenced here; deserves its own dedicated research pass.** (Pricing pages seen:
   QuiverQuant, FMP, Tiingo — not independently verified.)
3. **Minimum labeled-trade count** before a Platt-calibrated win% is honest, and how to show uncertainty in
   the cockpit (CI / "insufficient data").
4. **How regime/breadth (VIX, index trend, risk-on/off) should GATE or down-weight** the score vs being just
   another additive factor (regime change explicitly flagged as needing human review).

## Key sources (primary)
- Niculescu-Mizil & Caruana, *Predicting Good Probabilities With Supervised Learning*, ICML 2005 (calibration).
- Bailey, Borwein, López de Prado, Zhu, *The Probability of Backtest Overfitting*, J. Computational Finance
  (PBO/CSCV) + *Deflated Sharpe Ratio*, JPM 2014.
- Glasserman & Lin, *Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT*, ACM ICAIF
  (arXiv 2309.17322).
- *The New Quant: LLMs in Quantitative Finance* survey (arXiv 2510.05533); Sentic LLM-FSA reasoning paper;
  PMC12421730 review (Yu 2023; Perlin 2025). Stefan Jansen ML4T (multiple-testing workflow).
