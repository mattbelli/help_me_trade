# Channel audit 06 — Market-wide context

*Agent: Opus, read-only, 2026-08-11.*
*Components audited: `regime` (5,707 dots), `index_pressure` (5,707), `market_shock` (0),
`earnings_proximity` (879).*

**Independently verified by the orchestrator:** `regime` and `index_pressure` sum to **exactly 0.0
points** across all 5,713 rows each while `earnings_proximity` sums 268.47 (max 1.0); the raw_value /
detail_json mismatch (`index_pressure` **3,698 of 5,713**, `regime` **3,276 of 5,713**); and the dead
VIX arm — the literal string `"VIX"` appears in exactly one production bar-fetch call
(`api/main.py:1766`), and no VIX bars exist on disk (only `VIXY`, 550 rows).

---

## PUNCHLINE

**The suspicion is right about discrimination and wrong about score damage.** `regime` and
`index_pressure` land on **5,704 of 5,704 (100.00%)** covered episodes — they are literal constants, and
a constant cannot rank anything. But they are already registered `scoring_mode='annotate_only'`, and an
ablation over 400 real episodes changed the score on **0/400**, the decayed z on **0/400**, and
conviction on **0/400**. **There is no score-mass double-counting to quantify — it is exactly zero.**
The damage is in a different currency: 11,418 dead rows, 100% signature pollution on every card the
founder reads, and a **fabricated-number bug on 57–65% of those rows.**

`market_shock` is **half-broken, not well-calibrated**: the SPY arm works and genuinely never tripped
(max move 1.803% vs a 2.0% threshold); the VIX arm is **dead code** — `"VIX"` resolves to no data source
anywhere in the system — and VIX moved **+12.38% (07-23)** and **+13.45% (07-29)**, both far above its
8.0% threshold. **It should have fired twice.**

---

## 1. What each producer actually measures

| component | input | firing rule | scored? |
|---|---|---|---|
| `regime` | `db/regime.latest_observation` on `regime_macro` in trading.db (VIXCLS, vix3m, DGS2, DGS10) | fires if **any one** series is ≤ `FUSION_REGIME_STALE_DAYS=5` days old. No threshold, no comparison, no percentile. | no — `points=0.0` |
| `index_pressure` | `db/index_pressure.day_summary(market_day, window)` **summed across all 13 underlyings** | fires if the gauge has **any** minutes that day | no — `points=0.0` |
| `market_shock` | `episode.market_shock`, set by `api/main.py:_fusion_shock_active()` | `abs(spy_move) >= 2.0` **or** `vix_move >= 8.0`, latched 60 min | no — `display_only` |
| `earnings_proximity` | `db/earnings_calendar` — **confirmed dates only** (`estimated = 0`) | 1–5 weekdays ahead, or 0–2 weekdays after last report, or today. `points` = linear closeness in [0,1] | **YES** |

```python
# regime — signals/fusion/intake.py:1034
if not readings:
    return None          # ← the ONLY way regime does not fire
```
```python
# index_pressure — intake.py:1096-1101
for window in ("close", "open"):
    rows = day_summary(market_day, window=window, db_path=db_path)
    if not rows: continue
    bull = sum(r["bull_usd"] for r in rows)   # ← summed over ALL 13 underlyings
```
```python
# market_shock — api/main.py:1767-1772
if (spy_move is not None and abs(spy_move) >= FUSION_SPY_SHOCK_MOVE_PCT     # 2.0
    or vix_move is not None and vix_move >= FUSION_VIX_SHOCK_MOVE_PCT):     # 8.0
```

**Neither `regime` nor `index_pressure` has a firing *condition* at all.** They are unconditional
per-episode-day emissions of "today's reading, whatever it is."

## 2. ★ Are `regime` and `index_pressure` the same channel?

**Same event, different payloads.** Not the same *measurement* (macro VIX/curve vs the index-options
gauge — genuinely independent inputs), but perfectly co-triggered because **neither has a trigger.**

```sql
WITH r AS (SELECT episode_id FROM evidence_dots WHERE component='regime'),
     i AS (SELECT episode_id FROM evidence_dots WHERE component='index_pressure')
SELECT (SELECT COUNT(*) FROM r) reg, (SELECT COUNT(*) FROM i) ip,
       (SELECT COUNT(*) FROM r WHERE episode_id IN (SELECT episode_id FROM i)) both,
       (SELECT COUNT(*) FROM r WHERE episode_id NOT IN (SELECT episode_id FROM i)) reg_alone,
       (SELECT COUNT(*) FROM i WHERE episode_id NOT IN (SELECT episode_id FROM r)) ip_alone;
-- 5707 | 5707 | 5707 | 0 | 0
```

At symbol-day grain: `5587 | 5587 | 5587 | 0 | 0`. Both are called from the same
`if context_key not in context_done:` block (`intake.py:1851-1856`) with the same
`(episode_id, market_day)` key. **The prior "3,630 co-fires" is superseded — co-fire is total, solo
count is exactly zero.**

## 3. ★ Do they discriminate between names? No — 100.00% coverage

```sql
WITH ep AS (SELECT DISTINCT episode_id FROM evidence_dots
            WHERE actor_group IN ('options_actor','equity_actor') AND substr(ts,1,10)>='2026-07-28')
SELECT (SELECT COUNT(*) FROM ep),
 (SELECT COUNT(*) FROM ep WHERE episode_id IN (SELECT episode_id FROM evidence_dots WHERE component='regime')),
 (SELECT COUNT(*) FROM ep WHERE episode_id IN (SELECT episode_id FROM evidence_dots WHERE component='index_pressure')),
 (SELECT COUNT(*) FROM ep WHERE episode_id IN (SELECT episode_id FROM evidence_dots WHERE component='earnings_proximity'));
-- 5704 | 5704 | 5704 | 878
```

**Every episode that saw any actor evidence carries both dots. Zero information about which name to
look at.** 2,192/2,192 open episodes carry both tokens in `fusion_episodes.signature` — the string
printed in every card body. On 2026-08-11 all live `regime` dots carry the identical
`{"vix":15.46,"vix3m":18.98,"vix_term_ratio":0.8145,"curve_2s10s":0.46}`.

**A worse detail:** the index-pressure gauge covers exactly 13 underlyings (`AAPL AMZN DIA GOOGL IWM
META MSFT NVDA QQQ SPX SPXW SPY TSLA`). Only **34 of 5,709** index_pressure dots (0.6%) land on one of
them. **For 99.4% of dots the producer sums a 13-name aggregate and stamps it on a name the gauge has
no data for.** Meanwhile `signals/dossier.py:1256` already does it *correctly* —
`if str(entry["underlying"]).upper() == symbol`.

## 4. What market-wide state should be

**Recommendation: delete both dots; keep both reads, in roles they already have working
implementations for.**

- **The LLM already gets a strictly better version and doesn't read these dots.**
  `signals/dossier.py:_context_protos` calls `macro_substrate.regime_report` against **`data/macro.db`**
  and `day_summary` filtered to the symbol. It never touches `evidence_dots`.
- **The dot's store is the thin one.** The `regime` dot reads `regime_macro` in trading.db: **44–57
  rows, ~2 months, 4 series.** `macro.db` has **9,248 VIXCLS observations back to 1990**, plus credit
  (`BAMLH0A0HYM2`) and dollar (`DTWEXBGS`) axes `regime_macro` lacks entirely. `regime_report` already
  returns a **percentile against full history + a 33/80 bucket per axis**. The dot ships a raw VIX level
  with no percentile — and *cannot* compute one from its own store.
- **The state genuinely varies, so it is a real conditioner.** Daily index-pressure net swung −92.5M →
  +83.0M over 12 sessions; VIX 14.9 → 22 with two >12% single-day jumps. Informative *about the day*,
  worthless *about the name*.

Concretely:

1. **Delete the `regime` and `index_pressure` evidence-dot producers.** No score changes (measured).
   Removes 11,418 rows, two write commits per episode-day on the single-writer WAL that caused the
   07-27 lock-starve, and two dead tokens from every card signature.
2. **Promote market-wide state to a single day-scoped `market_context` record** — one row per market
   day, not per name — carrying `regime_report`'s percentile+bucket per axis (vol/credit/curve/dollar)
   plus the index-pressure day net. Attach it to the LLM's prompt **once per pass**, not once per name.
3. **Keep the per-name index-pressure read for the 13 gauge names only** — the dossier's existing filter
   is already the right shape.
4. **Use regime as a conditioner on dot thresholds, not as a dot.**

## 5. `earnings_proximity` on its own terms

Reconstructed structurally against the live universe (5 weekdays ahead + 2 behind + today):

| day | universe | in-window | % universe | % of calendar-covered names |
|---|---|---|---|---|
| 07-28 | 1373 | 440 | 32.0% | 48.1% |
| 07-30 | 1297 | 533 | **41.1%** | **62.4%** |
| 08-04 | 1692 | 345 | 20.4% | 35.8% |
| 08-11 | 1422 | 62 | 4.4% | 7.8% |
| **total** | **16,789** | **3,868** | **23.0%** | — |

Current table state: 880 rows, **460 zeroed** (`side="none"`, correctly walked down) and 420 live.
Sides: `today` 61 (points 1.0), `upcoming` 106, `reported` 253.

**A known-schedule context flag, not unusualness.** 100% predictable a quarter in advance; seasonally
sweeps 40% of the universe.

**Recommendation: conditioner, not a dot.** It should not add mass. It should (a) *re-weight* other
dots on the same name — unusual options volume 3 days pre-print is a different object from the same
volume on a random Tuesday — and (b) act as a **suppressor** for event-contaminated mechanisms.

**Caveat:** the calendar covers only **~55–60%** of the live universe (795–965 of 1,422–1,692 names with
actor dots), and `next_earnings_date` deliberately excludes the 2,992 `edgar_estimate` rows. **Absence
of the flag is not evidence of absence of earnings.**

## 6. Double-counting damage — measured at zero score mass

```
context-dot ablation over the 400 highest-scoring open episodes:
score changed on 0/400; decayed z changed on 0/400; conviction changed on 0/400
```

Mechanism: `scorer.py:16` `ANNOTATE_ONLY_GROUPS = frozenset({"background_crowd", "context"})`; line 180
skips those groups before the state-group loop, so context never enters `group_scores` — hence never the
`breadth` term. `score_decayed` only sums `ACTOR_GROUPS`. Current gate state: **5 of 2,191 open episodes
clear z ≥ 3.5 AND conviction ≥ 0.4.** Removing all context dots leaves that set identical.

**The only one of the four that moves the score is `earnings_proximity`** — ablating it changed
`score_dots` on **14/400** episodes, mean **+0.995**, max **+1.5**. It does *not* move `score_decayed`,
so it moves the **displayed/ranked** score and the notification bar but not the z-gate.

**Where the damage actually is:**

1. **Fabricated numbers on the founder-visible surface.** `_attach_regime`/`_attach_index_pressure` are
   the only rolling state lanes that **don't pass `refresh_state=True`**. Per `_insert_dot_on`'s own
   docstring, `ON CONFLICT` then keeps the ORIGINAL `raw_value` and `ts` while `detail_json` keeps
   refreshing — "a fabricated fact on the dossier."
   - `regime`: **3,243 / 5,707 (56.8%)** have `raw_value != detail.vix`
   - `index_pressure`: **3,696 / 5,707 (64.8%)** have `raw_value != detail.net_usd`; **3,437 (60.2%)**
     have `ts`-day ≠ `detail.date`
   - Live example: AAL's regime dot, `ts=2026-07-28`, `raw_value=18.58`, `detail.vix=14.9`. Its
     index_pressure dot: `raw_value=-1,596,218` vs `detail.net_usd=-92,482,892` — **off by 58×.**
     `evidence-stack-widget.tsx:76` renders `raw_value` directly.
2. **Signature pollution:** 2,192/2,192 open episodes carry `index_pressure,regime` in the string
   printed in every `fusion_stack` card body.
3. **Write load:** 11,418 rows = 4.0% of all 285,264 dots since 07-27, each its own commit on the
   single-writer WAL.

## 7. `market_shock` — why zero. Silently broken, on the arm that mattered

**SPY arm — works, genuinely quiet.** Max |daily move| over the window:

```
07-23 -1.235 | 07-29 -1.539 | 07-30 +1.677 | 08-03 +1.424 | 08-04 +1.803  ← max
```

`1.803% < FUSION_SPY_SHOCK_MOVE_PCT = 2.0`. **Never crossed. Correctly calibrated, honestly silent.**

**VIX arm — dead code.** `_fusion_cached_daily_move("VIX")` needs `("VIX", "1Day")` in a bars cache.

- `"VIX"` appears in exactly **one place in the entire repo** as a bar request: `api/main.py:1766`.
- `data/settings.json` watchlists: 61 symbols, **no `VIX`** (there is `VIXM` — a different symbol).
- `daily_bars` has SPY and VIXY. **No VIX row exists anywhere on disk.** Alpaca serves no index bars;
  Schwab's symbol is `$VIX.X`.
- ⇒ `vix_move` is `None` on every call, forever. `episodes.market_shock` = 0 on **8,049 / 8,049**.

**What it cost:** VIX close-to-close from `macro.db`:

```
07-21 -8.58 | 07-23 +12.38 ← | 07-29 +13.45 ← | 07-30 -17.28 | 08-04 +4.04
```

Both clear the `>= 8.0` threshold. 07-29 was a real risk-off session (SPY −1.54%, VIX +13.5%) that the
SPY arm's 2% bar missed *by design* and the VIX arm was supposed to catch. **Under a working VIX arm,
market_shock fires on ≥2 of 16 observed sessions (12.5% of days). It fired on 0.**

**Third latent bug** (code-read, not yet measured — no shock has ever fired to test it):
`intake.py:234` sets `market_shock = MAX(market_shock, ?)`. **The flag is monotonic and never resets**,
while the underlying window is 60 minutes and the episode TTL is 15 sessions. The first shock to ever
fire will pin `"market-shock window active"` on the dossier for the episode's entire remaining life.

## 8. Honest verdict

| component | verdict | one line |
|---|---|---|
| `regime` | **delete the dot → day-scoped context + conditioner** | 100% coverage = a constant; reads a 2-month store when `macro.db` has 9,248 observations and a percentile function the dossier already uses. |
| `index_pressure` | **delete the broadcast dot → keep per-name for the 13 gauge names; day-net becomes context** | 99.4% of dots stamp a 13-name aggregate onto names the gauge has no data for; `dossier.py:1256` already implements the correct filter. |
| `market_shock` | **fix, then keep as a conditioner** — not a dot | VIX arm has no data source and missed two >12% days; SPY arm is fine. A market-wide shock is by definition true for every name. Also fix the never-resetting `MAX()` latch. |
| `earnings_proximity` | **conditioner + suppressor** — demote from scoring dot | Perfectly predictable, 23% of universe symbol-days (41% at peak) — never rare, so never unusualness; but decisive for *interpreting* other dots. Currently the only one of the four that moves the score. |

**Three of these four are properties of the *day*, not the *name*, and the architecture already
half-knows it** — `annotate_only`, `points=0.0`, `direction="none"`, and a dossier that pointedly reads
better sources instead. The design mistake is not that they inflate scores (they provably don't); it is
that **a per-name row is the wrong *shape* for a per-day fact**, and paying that shape cost bought three
failure modes for free: a 57–65% raw_value/detail contradiction the founder can see in the widget, a
100%-constant token in every card signature, and 11.4k rows of commit pressure on the WAL.
`earnings_proximity` is the mirror image — genuinely per-name, genuinely known-in-advance, and therefore
the one thing here that should *modulate* other channels rather than add mass of its own.
