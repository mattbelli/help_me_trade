# BRIEF — `flow_history`: give the agent the 16 GB flow corpus

*Self-contained coder brief, written 2026-08-03. Board card: `flow-history-agent-reach`.
Not yet dispatched — no code exists. Measured figures below are as of 2026-08-03;
re-check the corpus end date before quoting staleness numbers.*

## Why this exists

`data/flow_corpus.db` is 16.2 GB / 86,938,743 rollup rows covering **255 sessions
(2025-07-28 → 2026-07-31) across 7,004 underlyings** — the whole market, not the
watchlist. `ai/` and `research/` reference it **zero times**. Only the nightly
`signals/flow_perception.py` scan reads it, and that ships a precomputed digest.

Consequence today: the agent sees a $2M NVDA sweep on the live lane and has **no
baseline** — `options_flow` reads `db/options_flow` (live events), which begins at
"now". It cannot say whether that print is a Tuesday or a yearly record.

This is a WIRE IT EVERYWHERE gap: the largest dataset we own, invisible to every
agent surface.

## Scope

ONE new perception resource, `flow_history`, registered in
`ai/perception/resources.py`. Two capabilities, one query path:

1. **Marginals** — where this name's flow sits, per dimension.
2. **Rarity** — how big a given print is versus comparable orders.

Nothing else. No new pipeline, no backfill, no UI.

---

## Part 1 — Marginals (the default response)

### The problem it solves
Filtering alone cannot make this readable. Symbol narrows 86.9M → 307,312 rows for
NVDA. **One session** of NVDA is 1,315 rows, because the grain is the cross-product
of right × dte_bucket × mny_bucket × side × strict_side × multileg × cond.

### The answer: return marginals, not rows
Collapse each dimension **separately** instead of crossed. Measured on NVDA:
**60 ms, 7.8 KB, 14 rows + a 255-point daily series.**

```
daily        [[date, premium_usd], ...]        # every bucket dim collapsed
right        C $… | P $…
dte_bucket   0-7 $9.16B | 90+ $6.17B | 31-90 $4.04B | 8-30 $3.31B
mny_bucket   atm | itm | otm2_10 | otm10p
side         sell $10.24B | buy $10.16B | unclassified $2.28B | wide_spread $…
```

The agent sees where weight sits on every axis at once, then makes ONE more call to
drill the axis that looked interesting.

### Filters
- `symbol` — **REQUIRED**. The only index is `idx_rollup_und_date (underlying, date)`.
  A market-wide group-by is a **65-second full table scan** (measured:
  `count(distinct underlying)` = 64.8 s). Reject a missing symbol with a message
  pointing at `flow_perception` for the market-wide question.
- `since` / `until` — ISO date, default trailing 60 sessions.
- `group_by` — optional, ONE of `right|dte_bucket|mny_bucket|side|strict_side|cond|multileg`.
  Returns that single projection. **Never accept a list** — the cross-product is the
  thing we are avoiding.
- `limit` — caps drill rows.

### Vocabulary must be handed over, not guessed
Exact values, verified against the corpus:

| dimension | values |
|---|---|
| `right` | `C`, `P` |
| `dte_bucket` | `0-7`, `8-30`, `31-90`, `90+` |
| `mny_bucket` | `atm`, `itm`, `otm2_10`, `otm10p` |
| `side` / `strict_side` | `buy`, `sell`, `unclassified`, `wide_spread` |
| `multileg` | `0`, `1` |

Put these **in the registry description**, and make an invalid filter value return
the valid set rather than an empty row list. This is the measured `market_regime`
failure mode documented in `_RESOURCE_ALIASES`: the stronger web model recovers off
a returned `available` list, the weaker mobile model does not. An empty result is
indistinguishable from "nothing happened" and the mobile model reports FAILED.

---

## Part 2 — Rarity ("how unusual is this print")

### The line that governs the whole feature
**Rarity is measurable. Significance is not.**

`signals/flow_perception.py` already states the measured split:
- BACKWARDS (1.13/1.13b/1.14b): big-move windows carry the flow fingerprint
  **1.4–1.7×** more often than liquidity-matched peers. Real.
- FORWARD (1.14c/1.14d): **pre-registered NULL, twice.** *"The profile DESCRIBES
  big-move windows without SELECTING them."*

So: ship the description, never the prediction. Allowed output is a rank with its
denominator attached. Forbidden: any direction word, `significance`, `score`,
`smart money`, any probability or win rate, and any ordering by interestingness
(sorting by unusualness IS an edge claim). Order by date or by raw premium only.

### ⚠️⚠️ The footgun — grain pairing
The rollup stores TWO second moments deliberately, so neither grain can be chosen
after seeing the answer:

- `premium_sq` pairs with `prints` (per-print grain, as pre-registered)
- `premium_sq_orders` pairs with `orders` (order grain — "one sweep is one observation")

**Use `premium_sq_orders` with `orders`.** Mixing them breaks the Cauchy-Schwarz
bound and **1.55% of rows go negative-variance → NaN sd** (measured; caught in
review 2026-07-27). This is called out in the `db/flow_corpus.py` DDL comments with
a ⚠️⚠️ — read them before writing the query.

### Reference class
Rank inside the bucket — `(underlying, right, dte_bucket, mny_bucket)` — never
against the name's whole distribution. A $2M 0DTE ATM NVDA call is ordinary; a $2M
90+DTE OTM put may be a record. Ranking against the blob lets the biggest bucket
drown the one being asked about.

### Report the empirical rank, NOT a z-score
Option premium is fat-tailed; a "5-sigma" print is not remarkable, so sigma
silently assumes a shape the data does not have. Emit a count-based rank:

```
premium_rank   larger than 99.6% of NVDA 90+DTE OTM put orders
reference_n    4,182 orders over 255 sessions
biggest_seen   $8.4M on 2026-03-14
```

### Multiple comparisons
Scanning 7,004 names daily makes a "one-in-a-thousand" event fire ~7×/day, ~1,800×/year
by construction. If any threshold-based surfacing is added later, it must account for
how many places were looked. **Not in this scope** — `flow_history` answers on demand;
it does not push. Record it as a follow-up, do not build it.

---

## Constraints

- Read-only. Open the corpus with `mode=ro` URI; never write, never migrate it.
- The corpus lives at `db/flow_corpus.DEFAULT_DB_PATH`, NOT the app DB. Do not thread
  it through the `db_path` param the registry passes (that is `trading.db`) — follow
  the `_read_earnings_release` precedent (`del db_path`).
- Fail-soft: a missing/locked corpus returns a row explaining it is unavailable,
  never an exception. `query()` already wraps readers, but the "no corpus" case must
  read as "unavailable", never as "nothing unusual".
- **Staleness is mandatory output.** The corpus advances via
  `tools/flow_corpus_rollup.py`; it currently ends 2026-07-31. Every response carries
  the latest session it holds and how stale that is. A corpus that stopped advancing
  must never read as "nothing happened".
- All 255 sessions are `schema_version 7` and DO carry `cond` (Alpaca condition-code
  join, per-session ledger `data/alpaca_conditions/join_ledger.json`; 07-31 =
  85.5% exact join, heuristic precision 0.978). Corpus and live both have codes but
  derive them differently — do not assert they agree; do not claim they conflict.
- `side` is not sentiment. Packages are ~53% of premium and multi-leg flips the sign.
  Say so in the description.

## Files
- `ai/perception/resources.py` — `_read_flow_history` + registry entry + aliases
  (`flow_corpus`, `flow_hist`, `historical_flow`, `flow_baseline`).
- `db/flow_corpus.py` — add the read helpers here (keep SQL out of the resource layer,
  matching `db/options_flow.recent_events`).
- `tests/test_perception_resources.py` — add `flow_history` to the pinned catalog set
  (`test_list_resources_catalog` WILL fail otherwise — that guard is working as designed).
- New `tests/test_flow_history.py`.

## Verify
1. `symbol` omitted → error naming `flow_perception`, not a scan.
2. Default NVDA response < 20 KB and < 500 ms.
3. Bad `group_by`/`dte_bucket` value → returns the valid set.
4. Rarity query uses `premium_sq_orders`/`orders`; assert no NaN and no negative
   variance across a full symbol-year.
5. Response contains no direction word, no probability, no `score` key.
6. Staleness field present and correct when the corpus is behind.
7. Whole repo: `ruff check .` + `python -m pytest -q`
   (venv: `/home/mbelli/dev/help_me_trade/.venv/bin/python`).

## Out of scope (follow-ups, do not build)
News FTS5 search (10,845 rows, no index today); IV history wiring
(`volatility_history` 1.88M rows — but live `iv_daily` has only 5 sessions);
threshold-based push surfacing; any UI.
