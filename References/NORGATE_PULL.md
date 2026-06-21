# Norgate pull → real decision-engine artifacts (runbook)

*One-time job. Produces the **survivorship-free** training artifacts that make the live scorer's win%/EV
trustworthy (vs the gitignored Alpaca dev artifacts, which are survivor-biased). Runs **on the Windows PC**
because the Norgate Data Updater (NDU) + the `norgatedata` package are Windows-only. The repo/runtime home is
`serv` (Linux); only this pull happens on Windows, then the artifacts are pushed and `serv` pulls them.*

## Subscription
- **Tier must be Platinum** (or higher). Delisted securities + historical index constituents — i.e. the
  survivorship-free data this whole pull exists for — are **Platinum-only**. Silver/Gold silently drop delisted
  names from "Current & Past" lists and reintroduce the bias.
- **6-month term is enough** — bars are cached locally and persist after the subscription lapses. You only need
  NDU live during the download.
- Indices (US + world) come bundled with the equity package; nothing extra to buy.

## Prereqs on the Windows PC
1. **NDU running** and signed in (Platinum active), data updated.
2. **Repo present** with latest `main`: `git pull`.
3. **Python env** (one-time):
   ```cmd
   cd <repo>
   py -m venv .venv-win
   .venv-win\Scripts\activate
   pip install -r requirements.txt
   pip install norgatedata
   ```
   (`norgatedata` pulls in pandas; scikit-learn pulls numpy — that covers the pipeline.)

## Step 1 — Ingest survivorship-free bars → `bars.db`
Use a **"Current & Past"** watchlist (the exact name as it appears in your NDU watchlists). Start with the S&P
500 list — smaller/faster — before scaling to Russell 3000. **Include `SPY` explicitly**: it's the benchmark
for the relative-strength feature and isn't a member of the index list itself.

```cmd
python -m research.norgate_loader --watchlist "S&P 500 Current & Past" --symbols SPY --years 15
```
- Writes `data/research/bars.db` with `source="norgate"` (gitignored — stays local).
- `--years 15` gives a deep sample (Platinum goes back to 1990; bump it if you want more).
- One bad symbol won't kill the run; it logs a final summary (`symbols_cached`, `bars_cached`).

## Step 2 — Build the labeled dataset → `labeled.db`
```cmd
python -m research.dataset --symbols-from-cache --source norgate --years 15 --stride 5
```
- `--symbols-from-cache --source norgate` labels every symbol you just ingested (triple-barrier, vol-scaled).
- `--stride 5` subsamples entries (every 5th bar) to keep row count sane. Lower stride = more rows = slower.
- Writes `data/research/labeled.db` (gitignored). Logs `rows / wins / losses / timeouts`.
- Aim for **≥1,000 labeled setups** — below that the artifacts carry the `low_confidence` flag. S&P 500
  Current & Past over 15y at stride 5 clears that comfortably.

## Step 3 — Train + export artifacts
```cmd
python -m research.train
```
- Purged-K-fold CV → out-of-fold preds → Platt calibration → EV table → JSON.
- Writes 4 files to `research/artifacts/`: **`model.json`, `calibrator.json`, `ev_table.json`, `meta.json`**.
- Prints a summary: samples, base rate, **Brier score**, and the per-bucket EV/win%/CI table. Lower Brier than
  the base-rate baseline = real edge; `low_confidence: False` once n ≥ 1,000.

## Step 4 — Commit the artifacts (force-add past .gitignore)
`research/artifacts/*.json` is gitignored (so dev/Alpaca junk never lands). The **real** ones get force-added:
```cmd
git add -f research/artifacts/model.json research/artifacts/calibrator.json research/artifacts/ev_table.json research/artifacts/meta.json
git commit -m "feat(decision-engine): real Norgate survivorship-free artifacts"
git push
```
- **Only commit the 4 JSON artifacts.** Never commit `data/` (raw bars / labeled.db — gitignored, and it's
  real financial-adjacent data).

## Step 5 — Activate on serv
```bash
ssh serv 'cd ~/dev/help_me_trade && git pull'
# restart backend so the scorer reloads artifacts:
ssh serv 'sudo systemctl restart hmt-backend'   # or the kill+systemd path if no sudo
```
`signals/decision_scorer.py` loads `research/artifacts/` on startup → every BUY proposal now carries a
calibrated win%/EV/CI sourced from survivorship-free data, on desktop + phone.

## Sanity checks
- `meta.json` → `data_source` points at `labeled.db`, `n_samples` is what you expect, `low_confidence` is
  `False` (or you accept the flag), `date_range` spans your `--years`.
- A live proposal on serv shows a win%/EV that moved off the dev numbers.

## If you later want fresh data
Re-subscribe (NDU live), re-run Steps 1–4. Cached bars persist, so a refresh only ingests the new tail.
