# Default watchlists — build spec

*Status: SPEC, ready to build (2026-07-14). Scope: the watchlists that **ship with the product** — what any
new user sees on first boot — plus the rule engine that makes some of them self-updating. Companion to
`References/WATCHLIST_SPEC.md` (the v3 column/display spec; this doc is about *which lists exist*, not how a
row renders).*

**Reframe that drives this doc:** these are product defaults for other users, not the founder's personal
lists. A new install should feel like it came with a real trading cockpit already set up. The founder's own
`main` is his business — the seeder must never rewrite it.

---

## 1. Where defaults live

`DEFAULT_SETTINGS["watchlists"]` in `config/settings.py:79-89` — today a single `main` list of
`SPY, QQQ, AAPL, NVDA, MU, AMD`. That constant IS the shipped default: `get_watchlists_state()` (`:608`)
materializes it on any boot with no `data/settings.json`. There is **no watchlist DB table** — watchlists are
a JSON blob in gitignored `data/settings.json`, owned entirely by `config/settings.py`. No migration needed;
extending the constant ships the lists.

Existing users have a `watchlists` key already, so they will **not** pick up new defaults from the constant.
If we want them backfilled, that needs a one-shot merge on load (add lists whose `id` is absent, never touch
existing ids). **Decision needed** — recommend yes, additive-only, since these are all new ids.

---

## 2. The default list set

Static, hand-curated. Deliberately tight — see §4, these compete for the options-flow underlying budget.

| id | Name | Symbols | Why it ships |
|---|---|---|---|
| `main` | Main | SPY QQQ IWM NVDA TSLA AAPL MSFT AMZN META GOOGL AMD | The starter list. Liquid megacaps + the three index ETFs — the names with real options flow, which is what this app's engine is tuned for. |
| `sectors` | Sectors | XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC | The 11 SPDR sectors — the standard rotation read. Answers "what's working today" in one glance. |
| `indexes` | Indexes & Vol | SPY QQQ DIA IWM MDY RSP VTI VXX | Breadth + vol. RSP vs SPY is the equal-weight/breadth tell; VXX is the fear read. |
| `commodities` | Commodities | GLD SLV USO UNG DBC DBA CPER URA | Macro backdrop. CPER (copper) is the growth tell; URA rides the nuclear/AI-power theme. |
| `rates` | Rates & Credit | TLT IEF SHY HYG LQD TIP | The other half of macro. HYG/LQD = credit stress, TLT = duration, TIP = inflation. |

Unique symbol count: **41**. That number is the whole problem — see §4.

### Deliberate exclusions
- **Futures roots** (`/ES`, `/CL`) — they 400 on Alpaca's options endpoints and burn an underlying slot per
  cycle for nothing (`signals/options_flow/scan.py:30` filters them; PR #73's message documents the waste).
  Keep them out of defaults entirely.
- **Meme/high-beta list** — deliberately not shipped. It's a personality, not a default, and it's the kind of
  list that ages badly in a repo.

---

## 3. Dynamic (rule-ranked) lists

**The founder's instinct is right: "top 20 by volume" is not a symbol array, it's a query.** Nothing in the
codebase can express that today — every list is a static array.

### The design that makes this cheap
A dynamic list is a **saved ranking over a base universe**, and its resolved symbols are **cached back into
the list's own `symbols` field**. That one choice means *every existing reader — the widget, scans, news, the
perception layer, mobile — treats a dynamic list exactly like a static one.* Only the refresh job knows the
difference. **No consumer changes.** This is the load-bearing idea in this spec.

### Schema (additive; `normalize_watchlists_state` at `config/settings.py:567` currently drops unknown keys, so
these need explicit plumbing)
```jsonc
{
  "id": "most-liquid-20",
  "name": "Most Liquid 20",
  "kind": "dynamic",                 // absent = static
  "rule": { "rank": "dollar_volume", "universe": "sp500", "limit": 20 },
  "refreshed_at": "2026-07-15T21:05:00Z",
  "symbols": ["NVDA", "TSLA", ...]   // last resolved ranking — the cache
}
```
Validate **shape** in `config/settings.py` and **vocabulary** at the API layer. The `rank`/`universe`
vocabularies live in `signals/`, which imports `config.settings` — validating them in settings is a circular
import. The resolver is the authority and logs+skips an unknown rank.

### Ranks to ship
| rank | Metric (from daily bars) | List it powers |
|---|---|---|
| `dollar_volume` | avg(close x volume) over 20d | Most Liquid 20 / 50 |
| `volume` | avg(volume) over 20d | (parity with the literal ask; see below) |
| `rvol` | latest volume ÷ prior-20d avg | Unusual Volume |
| `gainers` / `losers` | 1-day % change | Top Gainers / Losers |

**Rank on dollar volume, not share volume — this is the refinement worth making.** A share-volume
leaderboard is permanently dominated by low-priced names (F, SOFI, NIO) that trade enormous share counts and
very little money. It is not what a trader means by "most active", and it's not where options flow lives.
Ship `dollar_volume` as the default; keep `volume` available for anyone who genuinely wants the share count.

### Universes (already exist — `signals/universes/`)
`sp500` (503), `nasdaq100`, `russell1000`, `spy`. Reuse `resolve_universe_spec()`
(`signals/strategy_universe.py:64`) — its docstring already anticipates exactly this ("Architected so
additional built-ins (e.g. a dynamic liquid screen) can be added as new specs"). Default to `sp500`.

### Cost — must stay batched
Rank via `aget_stock_bars_batch(symbols, timeframe="1Day", days=45)` in chunks of 100 (precedent:
`_BARS_CHUNK = 100` in `scan.py`). **S&P 500 = ~5 requests, not ~500.** This must never become a per-symbol
fan-out — `signals/screener.py`'s `run_scan` takes a per-symbol `get_bars`, so *do not reuse it here*; it
would burst. Schwab's WAF blocks bursts from the serv IP (`brokers/schwab_market_data.py:37` `_waf_gate`).

### Refresh
Daily, post-close (the metrics are daily-bar-derived — intraday refresh buys nothing). Register alongside the
existing loops in `api/main.py` (`_earnings_refresh_loop` at `:610` is the pattern). Plus a manual
`POST /api/watchlists/{id}/refresh`.

**Failure rule:** a rule that resolves to nothing (bad rank, feed down) **leaves the previous symbols in
place**. A stale ranking beats a watchlist that empties itself the one morning the feed hiccups.

### Dynamic lists to ship
`Most Liquid 20` · `Most Liquid 50` · `Unusual Volume` (rvol) · `Top Gainers` · `Top Losers` — all
`universe: sp500`.

---

## 4. ⚠️ The blocker — 41 default symbols vs a 30-underlying cap

**This is the decision for tomorrow. Nothing else here is hard.**

Both the API host (`api/main.py:677 _all_watchlist_symbols`) and the out-of-process collector
(`tools/flow_collector.py:47 watchlist_symbols`) build the **union of all watchlists** as `sorted(set(...))`
and hand it to `eligible_underlyings()` (`signals/options_flow/scan.py:30`), which ends in:

```python
return eligible[:OPTIONS_FLOW_MAX_UNDERLYINGS]   # cap = 30
```

**PR #73 did not remove this clip.** It fixed the *contract-level* allocation (round-robin by rank across
underlyings — a real fix) and raised the cap 25→30 "so the real watchlist fits exactly (30 eligible)". At the
*underlying* level it made the clip **loud, not gone** — its own docstring says "Going over the cap is now a
warning, not a quiet drop." The mechanism is dormant, and dormant only while the union stays ≤30.

**Shipping 41 default symbols re-arms it on every new install**, and because the union is alphabetically
sorted, the names it shreds are the ones that sort last: **SPY, TSLA, XLE, XLF, XLK, XLU, XLV, XLY, VXX** —
i.e. exactly the SPY/TSLA blindness fixed and verified 2026-07-14, plus most of the new Sectors list. The
warning would fire, but flow would still be blind.

Note also the union logic is **duplicated** in `api/main.py` and `tools/flow_collector.py` — divergent copies,
and the collector's is the one that matters once #78 lands. Any fix must cover both or be shared.

### Options
| | Approach | Cost | Notes |
|---|---|---|---|
| **A** | **Priority-order the union + exclude dynamic lists from it.** Active list first, then board order, each list's own order preserved; rule-ranked lists never buy feed capacity. Cap then sheds least-wanted instead of alphabetically-last. | Small — one shared `curated_watchlist_symbols()` in `config/settings.py`, both callers collapse onto it. Prototyped and reverted; ~40 lines + tests. | Interim. Doesn't raise the ceiling, just makes the clip sane. Touches the flow collector — **parked until after the 07-15 open** per founder. |
| **B** | **The wildcard** (plan of record — see the `watchlists-dynamic-never-size-to-them` note). | Larger. | Removes the coupling for good; the founder's stated direction is that watchlists must never be sized to. The real answer. |
| **C** | Ship the lists, feed flow from `main` only. | Trivial. | Blunt; silently narrows flow for users who curate elsewhere. |

**Recommendation: ship the static defaults + dynamic engine on A, land B when the wildcard work happens.**
A and B compose — A is about *which 30 survive a clip*, B is about *not needing to clip*.

> **⚠️ Corrected 2026-07-14 (founder).** An earlier draft of this note said "if W confirms, build B and skip
> A." **Under the decided hybrid (§5 Tier 3) that is exactly backwards — A becomes permanent and
> load-bearing, not an interim fallback.** The hybrid runs the *capped set* through the opening burst and
> flips to wildcard ~3 minutes in. So the 30-cap is live during the opening window of **every session**, and
> the opening window is where the committed index-pressure gauge lives. A cap that clips alphabetically
> blinds SPY/TSLA precisely in the highest-value three minutes of the day.
>
> **Build A regardless of the W/C verdict.** It stops being about "which 30 survive if the wildcard fails"
> and becomes "which 30 we watch during the open, forever." Still sequence it after the 07-15 open (don't
> confound the verification), but it is no longer conditional on the outcome.

**Sequencing constraint (founder, 2026-07-14):** no flow-collector changes until after the 2026-07-15 open —
that session is verifying PR #71/#73/#75 live and must not be confounded by a new variable. So either land the
watchlists *without* touching the flow path (defaults ≤30 unique — would mean dropping ~2 of the 5 lists), or
wait for the open and ship A with them.

---

## 5. ✅ Options volume ranking — we have the data (two tiers)

> **Corrected 2026-07-14.** An earlier draft of this section said this "cannot be built — needs a paid feed
> (Polygon/CBOE/Unusual Whales)". **That is false, and it was false in the way that matters: it reasoned only
> from REST endpoints and never read the streaming research.** Recording the correction because the wrong
> version would have killed the single most on-mission list in this doc.

### Tier 1 — today, scoped to watched underlyings (branch C)
Per-contract volume is already on disk. `brokers/options_chain.py:85` parses `v` off the daily bar into
`side["volume"]`, and since PR #75 we page the **full** chain per underlying rather than one alphabetical
page. So "top N contracts by volume" across the ~30 watched names is available **right now** — that's what
the morning log yields.

Its limit is real but narrower than I claimed: it *reorders names we already chose*. It tells you where flow
concentrated among your watchlist; it cannot surface a name you aren't watching. Useful, not discovery.

### Tier 2 — market-wide, via the wildcard trades tape (branch W) ← **this is the list**
`References/FLOW_CAPACITY_RESEARCH_2026-07-11.md` §"Probe result": Alpaca forbids `*` on option **quotes**
("there are simply too many of them") and **allows it on trades**. Probed 2026-07-11:

```
{"action":"subscribe","trades":["*"]}  →  {"T":"subscription","trades":["*"]}   # ACKED, current plan
```

That is a **market-wide real-time options trade tape on the plan we already pay for.** Aggregate the tape by
underlying → a true *Top N by options volume*, and it is **genuine discovery**: it surfaces the name you
weren't watching, which is the entire point and the thing no other list in this doc does.

**Status:** ACK ≠ data. The 07-14 open probe's "wildcard too hot for this client" verdict was a
**misdiagnosis** — the client sat at ~150x headroom; the lag was Alpaca's upstream open-burst fanout queue
(57ms → 6.3s → recovered 13:31:54). Branch W/C is **genuinely unanswered**, and the **2026-07-15 open is the
verdict**. That is the same session gating this whole doc (§4), so one open settles both.

**If W confirms, this inverts §4 rather than competing with it.** The trades lane stops being watchlist-sized
entirely — no 30-underlying budget to clip, which is precisely the founder's standing call that watchlists
must never be sized to. The cap survives only for the ~60-sub *quote* budget used for side confirmation.

**Coverage must be stamped, not implied.** `FLOW_INTELLIGENCE_DESIGN_2026-07-11.md` already specs a
`coverage` column (`active_set` | `market_wide`) for exactly this: the denominator changes when the wildcard
turns on, and without the stamp, pre/post comparisons are silently apples-to-oranges and unreconstructable
later. A "Top Options Volume" list built on Tier 1 and one built on Tier 2 are **different lists wearing the
same name** — label them, and never let a Tier-1 ranking render as market-wide.

### Tier 3 — the hybrid (DECIDED, founder 2026-07-14)

**Not "C or W" — both, switched intra-session.** Run the capped active set (C) through the opening burst,
then flip to wildcard (W) ~3 minutes in and hold market-wide for the rest of the session. Even if tomorrow's
cap helps with Alpaca's latency, the hybrid gets built; the cap is not an alternative to the wildcard, it's
the first three minutes of it.

**The 3 minutes is well-calibrated to the measured data,** not a round number: the 07-14 open burst ran
57ms → 6.3s and recovered by 13:31:54 UTC = **09:31:54 ET — under 2 minutes past the bell.** A 3-minute
switch carries ~1 minute of margin. Re-measure at the 07-15 open before pinning the constant.

**Why a switch rather than both lanes:** the OPRA connection limit is **1** (the 07-11 probe had to win the
slot from the idling prod stream via retry-on-406). C ⊂ W — subscribing `*` already includes the capped set —
so the hybrid is not trading *coverage* for *coverage*. It trades **freshness for width** during the burst:
C gives ~57ms on 30 underlyings; W gives everything, 6.3s stale.

**The distinction that should drive the switch: lag hurts reaction, not aggregation.** A 6.3s delay barely
touches a 3-minute net-imbalance aggregate — the prints still arrive, just late, and a windowed sum is
complete once it drains. It is fatal to a *reactive* burst/sweep alert, where 6.3s stale is 6.3s wrong. If
the opening gauge is the only opening-window consumer, W-from-the-bell may serve it fine and the switch only
protects the alert lane. **Worth settling explicitly at the 07-15 open** — it decides whether the switch is
global or per-consumer.

**✅ RESOLVED BY MEASUREMENT — `data/tape_2026-07-14_1329_open.mpk`** (13MB, 180s of the 07-14 open,
13:29:22→13:32:22 UTC). Two prior drafts of this section theorized about index-option availability; **the
answer was on disk the whole time.** Analysis below is from the tape, not inference.

**Alpaca's OPRA wildcard carries index options. Definitively.** 185,464 trades, **2,369 distinct roots**,
1,030,492 contracts in 180s — the full market-wide tape, not a capped set.

| # | root | contracts | share |
|---|---|---|---|
| 1 | SPY | 148,306 | 14.39% |
| 2 | QQQ | 98,752 | 9.58% |
| 3 | **SPXW** | **57,115** | **5.54%** |
| 4 | NVDA | 49,334 | 4.79% |
| 8 | IWM | 21,434 | 2.08% |

Every index root is present: **SPXW #3**, XSP #44, VIX #52, NDXP #98, SPX #103, RUT #509, **NDX #576**.
The "maybe Alpaca filters index options out" branch is **dead** — it never needed a probe.

**⚠️ Correction for the opening gauge: NDX is a dead root.** The committed gauge names SPY/QQQ/SPXW/**NDX**/
IWM. Measured, NDX is **53 contracts, rank #576** — QQQ is **1,863× larger**, and even NDXP (the PM-settled
weekly, #98) is 21× larger. **The NDX leg should be NDXP, or dropped in favour of QQQ**, which already carries
the Nasdaq signal at #2. Four of the five proposed legs are top-8 by volume; NDX is the one that isn't, by
three orders of magnitude.

**Discovery vs. stream (still true, now less important):** contract discovery runs through Alpaca's
`GET /v2/options/contracts` — a *trading-API* endpoint that won't enumerate index options Alpaca doesn't
trade. There is **no Schwab options path at all** (`brokers/schwab*.py` has no chain/contracts method), and
`brokers/options_chain.py` is typed to `AlpacaAdapter`. So the C lane can't easily reach SPXW — but the
wildcard hands it over for free at #3, which is what matters.

### Measured open-burst lag — the switch constant, and a 3-5x correction

From the same tape (`recv_ns` minus each print's exchange timestamp, 10s buckets):

| wall (UTC) | msgs/s | p50 lag | p99 lag | max |
|---|---|---|---|---|
| 13:29:52 | 754 | 0.608s | 1.591s | 1.67s |
| **13:30:02** (bell) | **2306** | 3.316s | 7.968s | 9.41s |
| 13:30:42 | 1232 | 3.310s | **21.204s** | 27.93s |
| 13:31:02 | 1185 | 2.499s | 5.685s | **30.32s** |
| 13:31:32 | 1018 | 1.475s | 4.907s | 9.40s |
| **13:31:52** | 1031 | **0.040s** | 0.896s | 1.32s |
| 13:32:12 | 1068 | 0.045s | 0.581s | 0.82s |

**Three results:**

1. **The 3-minute switch is well-calibrated — keep it.** Recovery to p50 0.04s lands at **13:31:52 UTC =
   09:31:52 ET, 1m52s after the bell.** A 3-minute switch carries **~68s of margin**. Re-measure each session
   before treating the constant as stable (one-day sample).
2. **⚠️ Lag is 3-5x worse than recorded.** Prior notes say the burst ran "57ms → 6.3s". Measured: **p99 peaked
   at 21.2s and max hit 30.3s**; 6.3s is roughly the p50 peak (5.77s at 13:30:12), not the tail. Anything
   sized off "6.3s" is sized off a median and will be wrong at the tail.
3. **The lag is upstream — but "upstream" ≠ "independent of what we subscribe to".** Rate went 754 → 2,306 →
   ~1,100/s while lag blew out to 21-30s: load barely moved, lag moved 20x, so a *client-side* bottleneck
   cannot produce that shape (the "too hot for this client" verdict stays a misdiagnosis). **That rules out
   our client — it does not rule out our subscription size.** Alpaca's fanout queue is per-connection: `*`
   makes them push ~1,000-2,300 msg/s to us; the capped set is a ~14 msg/s trickle.
   **Evidence that size drives lag:** pre-bell — calm market, no burst — the *wildcard* already ran **0.608s
   p50** (13:29:52) against the capped set's reported **~57ms**. ~10x the lag at ~50x the rate, before any
   burst. **⚠️ Unproven:** we have never measured the capped lane *through an open burst*. The 1-conn limit
   means W and C cannot be measured in the same session — it needs two mornings. Until then, "C stays fast
   through the burst" is a well-supported inference, not a fact, and the hybrid rests on it.
   Note also the burst never approached the design doc's "10-20x average" estimate — observed peak is
   **~2.2x** the tape's own 1,030/s mean.

### What the lag means for each consumer

**The "lag hurts reaction, not aggregation" argument survives, but the drain is 30s, not 6.3s.**

- **Opening gauge (aggregate):** tolerates it — prints arrive late, not lost, and a windowed sum is complete
  once it drains.
  > **Corrected 2026-07-15 (founder).** An earlier draft said the gauge "must wait ~30s past the window
  > close." **Wrong — it over-generalized max-lag-during-window into wait-past-window.** The wait you need is
  > the lag **at the window's closing moment**, not the worst lag anywhere inside it. The 30s delays hit
  > prints from the first ~90s (a 09:30:35 print lands ~09:31:05); all stragglers are in by ~09:31:30, and
  > from 09:31:52 lag is 0.04s. **So for a 09:30-09:33 window, everything has landed by 09:33:00 — a ~2s wait
  > suffices.** The 30s tail lives *inside* the window, not past its edge.
  >
  > **The rule that generalizes:** wait = lag at window close. It only bites if the window **closes during
  > the burst** — a 09:30-09:31 gauge would genuinely need ~30s, because its own edge sits in the worst of
  > it. The committed 3-minute window closes well past recovery and is safe.
- **Reactive burst/sweep alerts:** **21s at p99 is fatal.** This is the real justification for the C lane
  through the burst — not coverage, *freshness*. It is also why the switch may want to be per-consumer:
  the gauge wants W from the bell (for SPXW at #3), the alert lane wants C.

**Bottom line for the hybrid:** the tape proves W delivers the whole market through the burst (2,369 roots,
1,030/s, SPXW #3) — the data is *there*, just late. So a C-first hybrid trades away SPXW/QQQ/SPY index
coverage in the gauge's own window to buy freshness that only the alert lane needs. **Settle W-from-the-bell
vs. C-first per-consumer, not globally.**

### The quote budget — measured coverage, a units trap, and an unknown cap

Founder question (2026-07-15): *"What if we subscribe to the 60 highest option volume tickers — wouldn't we
cover ~99% of quotes?"* Answered from the tape:

**1. 60 roots is ~80%, not 99%.** The tail is fat — 2,369 roots traded in 180s.

| top-N roots | % of volume | | coverage target | roots needed |
|---|---|---|---|---|
| 10 | 49.20% | | 50% | 11 |
| 20 | 63.55% | | 80% | **58** |
| **60** | **80.63%** | | 90% | 141 |
| 200 | 92.90% | | 95% | 273 |
| 500 | 97.84% | | **99%** | **769** |

**2. ⚠️ Units trap: the ~60 quote budget is 60 CONTRACTS, not 60 roots.** You cannot subscribe to a root for
quotes — only to individual OCC contracts. In one 3-minute window the top 60 roots had **18,351 distinct
contracts trade** (~300x the budget). **SPY alone: 1,072. QQQ: 1,205. SPXW: 884.** A single root overruns a
60-contract budget by ~18x. Full chains are larger still — only *traded* contracts appear in a tape (PR #75
measured SPY's live chain at 13,730 contracts).

**3. The ~60 is OURS, not Alpaca's — and the binding constraint is CPU, not the vendor.**
Alpaca's numeric quote cap is genuinely **unknown**: the often-cited "1,000 cap" is **Polygon's number,
imported by mistake** and retracted in `FLOW_CAPACITY_RESEARCH_2026-07-11.md` ("**That is false.** No Alpaca
doc states a numeric options-quote cap at any tier"). Only `*` is documented-forbidden on quotes.

**But the vendor cap is close to irrelevant, because we hit our own CPU wall first.**
`FLOW_INTELLIGENCE_DESIGN_2026-07-11.md:16` is explicit and *measured*:

> "The scarce resource is **quotes-CPU, not the 1,000 cap** (measured: 150 quote subs ≈ 52% of a core)"

≈ **0.35% of a core per quote sub.** Which prices everything:

| quote scope | contracts | CPU |
|---|---|---|
| the 40/20 budget | 60 | ~21% of a core |
| the measured datum | 150 | ~52% of a core |
| **SPY alone** | 1,072 | **~3.7 cores** |
| **SPY+QQQ+SPXW** | 3,161 | **~11 cores** |

> **⚠️ Twice-corrected 2026-07-15.** Draft 1 proposed quoting the index complex. Draft 2 called it "dead on
> arrival: ~11 cores". **Both were wrong.** The 11-core figure priced *all 3,161 traded contracts* — but
> index volume is enormously concentrated, so you never need the chain (see below). Draft 2 also asserted the
> box couldn't afford it without checking: **serv has 16 cores and idles at ~0.2 load.**

### Measured — the trade tape is nearly free; quotes are the entire cost

`scripts/opra_replay_bench.py` on the 07-14 open tape:

```
frames: 55,838   msgs: 185,465   tape: 180s at 1,031 msgs/s recorded
replay: 1.16s = 160,495 msgs/s (6.23 us/msg)
projected: 0.6% of one core at recorded rate
```

**The full market-wide wildcard trade tape costs 0.6% of one core.** Any concern about wildcard *client* load
is settled: it is free. 100% of the CPU problem is the quote lane.

### Index-complex quoting — concentration makes it plausible; chattiness makes it unmeasured

Contract-level concentration in the same tape (share of each root's own opening volume):

| root | contracts traded | top25 | top50 | top100 | top200 |
|---|---|---|---|---|---|
| SPY | 1,072 | **61.8%** | 75.6% | 87.1% | 94.4% |
| QQQ | 1,205 | 49.1% | 65.1% | 80.3% | 91.1% |
| SPXW | 884 | 44.8% | 68.9% | 85.7% | 94.1% |

**SPY's top 25 strikes carry 62% of its opening volume.** Naive cost to quote the complex, extrapolating the
0.35%-core-per-sub datum:

| target | subs | naive cores |
|---|---|---|
| 80% of each root | **238** (SPY 64, QQQ 99, SPXW 75) | ~0.83 |
| 90% | 444 | ~1.54 |
| 95% | 735 | ~2.55 |

> **⚠️ Do not trust those core figures — they are an extrapolation from an average, applied to the extreme.**
> Working back from the measurement: 150 subs ≈ 52% of a core ≈ ~83k msg/s ≈ **~550 quotes/sec per contract**.
> That datum was measured on contracts of *unknown composition*. The contracts this table selects are **ATM
> index 0DTE strikes — the chattiest instruments in the market.** Pricing the top of the chattiness
> distribution at its mean will understate, possibly badly. **238 subs may cost far more than 0.83 cores.**

### The two real constraints (neither is "cores")

1. **The GIL.** One Python process ≈ one core, and `signals/options_flow/` has **no multiprocessing** — the
   consumer is single-process. 16 cores idle behind a 1-core ceiling. Using them needs a receiver→worker
   fan-out, and the **1-connection OPRA limit forces exactly one receiver**. That is an architecture build,
   not a config change. The founder's "fine with it using all the cores" is a reasonable *policy* (2-minute
   window, idle box) that the *architecture* cannot currently honour.
2. **Quote rate on the target contracts is unmeasured.** A trades tape cannot reveal it.

**⚡ The probe that prices this — cheap and decisive.** At an open, subscribe quotes to the **top ~25 SPY
strikes** for 2 minutes; record msg/s and CPU. That converts the whole question from extrapolation to
measurement, and it reuses `scripts/opra_ws_probe.py` (which already subscribes trades+quotes and dumps lag
stats). Do this before building the quotes-follow-heat manager to *any* budget — the 40/20 split is itself
sized off the same unverified average.

### ★ The way out — POLL quotes, don't stream them

Founder, 2026-07-15: *"There has to be another way. I'm not looking to just watch the indexes at open —
that's a huge compromise."* **Correct, and there is.** Every design above (40/20, index-complex,
receiver+workers) accepts a false premise: **that direction requires a streaming quote subscription.**

**The insight:** streaming delivers **~550-1,150 NBBO ticks/sec per contract**. Classification needs **one**
— the tick nearest each print. Streaming spends ~7 cores to discard >99% of what it receives.

**What the market-wide flow actually looks like** (07-14 open tape, 185,405 prints / $0.70B premium in 180s):

| premium ≥ | prints/min | distinct contracts | roots | % of volume | stream cores | **poll cost** |
|---|---|---|---|---|---|---|
| $25,000 | 1,191 | 2,054 | 202 | 18.8% | 7.12 | **21 req/sweep → 250ms @ 50% of budget** |
| $100,000 | 221 | 442 | 80 | 8.6% | 1.53 | 5 req/sweep → 300ms @ 10% |
| $250,000 | 66 | 153 | 41 | 5.2% | 0.53 | 2 req/sweep → trivial |

The whole market's *significant* flow is a few hundred contracts: 185k prints, but only 198 clear $250k.

**The mechanism, all of it already in the client:**
- `get_option_latest_quotes()` (`brokers/alpaca.py:1369`) → `GET /v1beta1/options/quotes/latest?symbols=<csv>`,
  **`_OPTIONS_QUOTES_CHUNK = 100`** symbols/request, returns full top-of-book.
- **Algo Trader Plus = 10,000 req/min ≈ 167 req/s** (design doc: *"REST polling is nowhere near a
  constraint"*).
- → **2,054 contracts refreshed every 250ms at half the rate budget, ~0.03 cores.** ~200x cheaper than
  streaming the same set, and market-wide (202 roots), not index-only.

**It fixes coverage, not just CPU — this is the real win.** Both approaches are reactive: you must watch a
contract *before* its big print. Streaming pre-positions on ~150 contracts; **polling pre-positions on
2,000+** for a fraction of a core. **13x more first-prints caught.** The breadth is the point.

**Free from the trade tape, no quotes at all:** condition codes carry `S` = **intermarket sweep** and `f` =
**electronic multi-leg** (design doc §22, seen in the live probe). Sweep detection and the F-2 spread-leg
filter — ~30-40% of the tape — are market-wide for zero marginal cost.

**Honest limits:**
- Polled NBBO is ≤250ms stale, not at-print. **But nothing gets at-print NBBO**: `GET /v1beta1/options/quotes`
  is **404, does not exist on any tier** (design doc §20). Streaming has the same reactive gap. Polling is
  200x cheaper for a comparable approximation.
- **The precision isn't worth 7 cores anyway.** The design's own research: Pan & Poteshman found *"no
  predictability at all from the public signal"*, and an ask-side call print is indistinguishable from an
  open-buy, a sell-to-close, a hedge, or one leg of a spread — *"real disambiguation requires the paid CBOE
  Open-Close dataset"*. A 250ms approximation is well matched to a signal the doc itself calls a
  low-information projection.

**Consequence: the receiver+workers build is unnecessary, and so is the 40/20 quote budget.** Keep a small
stream lane only where sub-250ms freshness provably matters; poll everything else. **Re-derive the
quotes-follow-heat manager against a ~2,000-contract polled set instead of a ~60-contract streamed one before
building it.**

The hybrid means `coverage` is not a one-time flip; it changes **mid-session, every session**. This is why
`FLOW_INTELLIGENCE_DESIGN_2026-07-11.md` §4 stamps **two** columns, not one: `coverage` *and*
**`minutes_into_session`**. The second is what makes a hybrid session reconstructable.

**The consequence for a "Top N by options volume" list is a real bias, not a footnote.** Aggregate a hybrid
session naively and:
- watched names are counted for **100%** of the session (C covers them through the burst, W after);
- unwatched names are counted only **after the switch** — they miss the opening burst entirely;
- and the opening burst is **10–20× average rate**, i.e. a wildly disproportionate share of the day's volume.

So a naive daily ranking **systematically under-counts exactly the names we aren't already watching** — which
re-introduces the circularity this list exists to escape, now as a silent bias rather than a hard limit. It
would look like discovery while quietly ranking the watchlist to the top.

**Rule: compute the market-wide ranking over the `market_wide` window only** (filter on
`minutes_into_session` ≥ switch), and label it as such — "most active since 9:33", which is honest — rather
than presenting a coverage-inhomogeneous number as a full-session ranking. A full-session market-wide
ranking is only available on a W-from-the-bell session.

### ⚠️ Licensing — lands directly on the "ship it to other users" framing
From `FLOW_CAPACITY_RESEARCH_2026-07-11.md` §"Doors opened" (2): cheap OPRA tiers are **personal/display
use**. *Redistributing* flow to app users triggers OPRA **redistributor/non-display fees** (~$1.5-2k/mo,
secondary-sourced) — "fine for the founder's cockpit, a real licensing decision if flow becomes a user-facing
product feature."

**A default watchlist shipped to other users, populated from the OPRA tape, is exactly that trigger.** This is
the one item in this doc that is not an engineering decision. It does not block the founder's own cockpit; it
blocks shipping this particular list as a product default. Resolve before it ships to anyone else — the rest
of §2's static lists carry no such constraint.

---

## 6. Other ideas worth building (ranked)

0. **Top Options Volume (market-wide)** — §5 Tier 2, off the wildcard tape. **The most on-mission list in
   this doc**: this app's edge is options flow, and this is the only default that performs *discovery* —
   every other list either reorders names you already picked or ranks the equity tape. Gated on the 07-15 W/C
   verdict, then the licensing question in §5 before it ships to other users.
1. **Earnings This Week** — dynamic, off the earnings calendar we *already* maintain
   (`arefresh_earnings_calendar`, prewarmed at `api/main.py:602`). Needs a `rank` sourced from the calendar
   rather than bars. **Highest value of anything buildable today** — the one list that tells a user what's
   about to move, with the data already on disk and no gate in front of it.
2. **My Positions** — mirrors currently-held positions. Trivially useful for every user, no new data.
3. **52-week Highs / Breakouts** — `signals/screener.py` already has the `near_high` preset; expose it as a rank.
4. **Mag 7** — AAPL MSFT NVDA AMZN GOOGL META TSLA. Static, one line, universally wanted.
5. **Crypto proxies** — IBIT COIN MSTR MARA RIOT. Static. Ships the theme without a crypto data feed.
6. **International** — EEM EFA FXI EWZ EWJ INDA. Static. Rounds out the macro picture.
7. **Options-Liquid 30** — the names that actually carry tradeable options flow. This is the list that should
   arguably *define* the flow engine's underlying set once the wildcard lands (§4B) — a curated feed universe
   instead of "whatever the union happens to be".

(1) and (2) are the two that would make a new user feel the app is alive on day one.

---

## 7. Build order

1. **Defaults + backfill** (§1, §2) — static only. Ships value immediately, zero new machinery.
   ⚠️ Gated on §4: 41 symbols over a 30 cap.
2. **Rule engine** (§3) — `signals/watchlist_rules.py`: ranks, `resolve_rule`, `refresh_dynamic_watchlists`.
   Settings plumbing for `kind`/`rule`/`refreshed_at`. Tests: dollar-volume-beats-share-volume, unknown rank →
   `[]`, feed failure keeps previous symbols, rule survives a save round-trip.
3. **Refresh loop + API** (§3) — daily post-close job, `POST /api/watchlists/{id}/refresh`, rank vocabulary
   validation at the route.
4. **Flow priority order** (§4A) — **after the 07-15 open, but build it either way.** The hybrid keeps the cap
   live through every opening window, so this is permanent, not a fallback. Shared
   `curated_watchlist_symbols()`; collapse both duplicate unions onto it. Tests must pin: not alphabetical,
   active-list-first, dynamic lists excluded.
5. **Top Options Volume** (§5 Tier 2) — on a confirmed W, founder's cockpit only until the OPRA
   redistribution question is answered. Stamp `coverage` **and** `minutes_into_session`; rank over the
   `market_wide` window only, labelled "since HH:MM" — never present a hybrid session as a full-session
   market-wide ranking.
5. **UI** — dynamic lists render for free (they're just cached symbols). Add a "rule" affordance + a
   `refreshed_at` timestamp so a user can tell a self-updating list from one they typed. Per the founder's
   style rules: **no pill chips**, **no second widget header** — the rule belongs in the existing toolbar
   dropdown, not a badge bar.

Verify per step: `ruff check .` + `python -m pytest -q tests/test_watchlist_*.py tests/test_options_flow_*.py`
(name the modules — the full suite hangs on network/ws tests).
