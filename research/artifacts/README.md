# Decision-engine model artifacts

The trained, committable outputs of the offline backtest+calibration harness (`research/train.py`). The live
scorer (`signals/`, increment 3) loads these to turn a setup's point-in-time features into a **calibrated
win-probability + Expected Value + confidence band**.

Files (produced by `python -m research.train --labeled-db data/research/labeled.db`):
- `model.json` — logistic score model: `intercept`, `coefficients`, `feature_names`, `preprocessing`
  (scaler means/stds + numeric medians). The transparent factor weights.
- `calibrator.json` — Platt (sigmoid) calibration `{A, B}` fit on out-of-fold predictions.
- `ev_table.json` — per calibrated-probability bucket: win-rate, Wilson 95% CI, avg win/loss, EV,
  and an `insufficient_data` flag.
- `meta.json` — `n_samples`, `base_rate`, `brier`, reliability table, `data_source`, and an `honesty`
  block (`low_confidence` until the sample reaches ~1,000–10,000 setups).

## Commit policy (important)
**`*.json` here is gitignored.** Dev artifacts (trained on free Alpaca IEX, which is survivor-biased + a few
symbols) are throwaway and must NOT be mistaken for the real model.

The **real** artifacts are produced OFFLINE on the founder's Windows PC against **Norgate** survivorship-free
data, then committed deliberately:
```
git add -f research/artifacts/model.json research/artifacts/calibrator.json \
            research/artifacts/ev_table.json research/artifacts/meta.json
```
Only commit artifacts whose `meta.json` `data_source` is the survivorship-free Norgate run.
