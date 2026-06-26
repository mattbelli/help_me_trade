# research/fixtures — Residual-Momentum Reference Artifacts

These files are the PC-side data + spec that **serv's production port must reproduce**.
They are committed (public EDGAR/price-structure data only — no user financial data).

---

## Files

### `momentum_sectors.json`

**Format**
```json
{
  "_meta": { "universe_count": 503, "coverage_count": 498, "missing_symbols": [...] },
  "symbols": {
    "AAPL": { "sector": "Manufacturing", "sic": "3571", "sic_desc": "Electronic Computers" },
    ...
  }
}
```

**What it is**: S&P 500 symbol → sector/SIC mapping sourced from
`data/research/fundamentals.db → companies(symbol, cik, sic, sic_desc, sector)`.
Sectors drawn from EDGAR SIC-based classification.

**Filtering applied** (matches validated research code):
- `sector IS NULL` → excluded
- `sector = ''` → excluded
- `sector = 'Unknown'` → excluded
- `sector = 'Agriculture'` → excluded

**Coverage**: 498 of 503 S&P 500 symbols have usable sector data (99.0%).
Five symbols have no qualifying sector entry: `BF.B BRK.B CTVA FLEX MRVL`.

**How serv uses it**: load as `{symbol: sector_string}` and pass to the target-set
computation. These are the only symbols eligible for the residual-momentum portfolio.

---

### `momentum_targets.json`

**Format**
```json
{
  "_meta": { "parameters": { "MOM_LONG": 252, "MOM_SKIP": 21, ... } },
  "target_sets": [
    {
      "as_of_date": "2024-12-31",
      "month": "2024-12",
      "n_selected": 98,
      "target_symbols": ["AAPL", "APP", ...],        // sorted alphabetically
      "residual_momentum": { "AAPL": 0.123, ... }     // score for each symbol
    },
    ...
  ]
}
```

**What it is**: Golden target sets computed by `research/momentum_reference.py` on
four fixed historical month-end dates:

| as_of_date   | n_selected | top 5 (by residual momentum)          |
|:-------------|:----------:|:--------------------------------------|
| 2023-12-29   | 98         | CVNA, COIN, APP, SMCI, NVDA           |
| 2024-06-28   | 98         | VRT, VST, CVNA, SMCI, DELL            |
| 2024-12-31   | 98         | APP, CVNA, VST, PLTR, TPL             |
| 2025-06-30   | 99         | PLTR, APP, HOOD, GEV, AXON            |

`target_symbols` is alphabetically sorted for stable diffing. `residual_momentum`
gives the exact score for each selected symbol.

**How serv validates its port**:
1. Ingest `momentum_sectors.json` and `data/research/bars.db` (or the equivalent
   production data store).
2. For each `as_of_date` in `target_sets`, run the production target-set computation.
3. Sort the resulting symbols alphabetically.
4. Diff against `target_symbols` — the lists must be identical.
5. For each symbol, compare `residual_momentum` scores. Floating-point differences
   are expected; scores should agree to at least 6 significant figures. Any
   disagreement indicates a look-ahead leak, wrong lookback window, or a
   sector-average computation bug.

---

## Algorithm Reference

The complete algorithm lives in `research/momentum_reference.py`. Summary:

```
Parameters
  MOM_LONG          = 252   # trading days back — start of 12-month window
  MOM_SKIP          = 21    # trading days back — end of 12-month window ("12-1")
  QUINTILE_FRAC     = 0.20  # top 20% of eligible stocks selected
  MIN_SECTOR_STOCKS = 3     # minimum sector members for a valid sector average

For rank_date d (last trading day of month T):

  Step 1 — raw 12-1 momentum
    ri       = index of d in symbol's date array
    mom_s    = (close[ri - 21] / close[ri - 252]) - 1
    gates: ri exists, idx_long >= 0, both closes > 0, symbol not delisted

  Step 2 — sector average (equal-weight)
    sec_avg[sector] = mean({mom_s}) for sectors with >= 3 members

  Step 3 — residual momentum
    residual_s = mom_s - sec_avg[sector_of_s]

  Step 4 — top quintile
    n_top = max(1, int(N_eligible * 0.20))
    top_syms = top n_top by residual descending (equal-weight portfolio)
```

**No look-ahead**: only bars with `date <= as_of` can influence the result. The
index lookup on `rank_date` enforces this naturally (if the bar doesn't exist, the
stock is skipped).

**Delist filtering**: symbols matching `TICKER-YYYYMM` are skipped when
`rank_date[:7] >= YYYY-MM` (survivorship-free universe encoding).

---

## Provenance

- Validated research script: `data/research/_edgehunt/sector_idio_mom_verify.py`
- Portfolio sleeve source:   `data/research/_edgehunt/combined_book_nav.py::run_mom_sleeve()`
- Reference implementation:  `research/momentum_reference.py`
- Universe:                  `signals/universes/sp500.json` (503 symbols, datahub source)
- Bars (survivorship-free):  `data/research/bars.db` (`daily_bars` table, 2010-01-04 onward)
- Fundamentals:              `data/research/fundamentals.db` (`companies` table, EDGAR SIC)
