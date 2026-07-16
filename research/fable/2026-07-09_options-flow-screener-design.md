# Options-Flow Screener — design spec (perception signal, Mode A)
*Fable 5 · design spec · 2026-07-09 · watches OPRA options prints on a watchlist and surfaces "unusual activity" as honest, deterministic perception events — to help the founder trade the UNDERLYING stock. Companion to the perception audit (`2026-07-02_perception-completeness.md`) and the Decision Engine roadmap (`2026-07-02_decision-engine-roadmap.md`); buildable-by-Composer quality.*

**One-sentence thesis:** the roadmap deferred options flow because "clean feeds cost $50–150/mo and it's noisy at retail" (roadmap §2 row 10) — the **cost half is now falsified** (real-time OPRA is included in the Algo Trader Plus subscription the app already pays for, live-probed 2026-07-08/09), while the **noise half is confirmed by the academic record** — so the right build is a cheap, watchlist-scoped, deterministic *attention router* that tells the founder "something big printed in X's options, here is exactly what we can and cannot know about it," never a scored alpha signal.

**Mode declaration (locked):** this is **Mode A — decision-SUPPORT perception**, per `References/SETUP_AVENUES.md` §2: it aggregates *true* signals for the founder's judgment; it is not a set-and-forget strategy and takes no `/edge-hunt` gauntlet. The DecisionCard rule applies in full: **deterministic code owns every number; no win%, EV, or "confidence %" is ever attached to a flow event** — direction is a label with a documented accuracy *citation*, not a probability. Read-only end to end: no options execution path exists or is created; `trading/execution_service.submit()` is untouched; the no-short invariant is untouched. This feature emits alerts + queryable context, full stop.

---

## 1. Ground truth — what the data actually gives us (live-probed)

Probed read-only against the founder's live keys: `scripts/opra_probe.py` (2026-06 session) + an extended probe this session (2026-07-09, all pure GETs). Results, with the previously-OPEN historical-endpoint question now **resolved**:

| Endpoint | Shape | Probe result |
|---|---|---|
| Chain snapshots | `GET /v1beta1/options/snapshots/{underlying}?feed=opra` | **200**; full chain pages; per contract `latestTrade{p,s,x,c,t}`, `latestQuote{bp,bs,bx,ap,as,ax,c,t}`, greeks, IV |
| **Historical trades** | `GET /v1beta1/options/trades?symbols=<OCC,csv>&start=&end=&limit=` (query-param, multi-symbol, `next_page_token`) | **200** — this is the correct form. The old probe's path form `/options/trades/{occ}` → **404** (that was the whole mystery; it was never a subscription gap) |
| History depth | same, `start=2024-02-12` on an expired SPY contract | **200 with prints** — Feb 2024 depth confirmed (~2.5 yr, essentially one bull regime — fine for threshold tuning, not for multi-regime validation) |
| Latest trade/quote | `/v1beta1/options/trades/latest`, `/v1beta1/options/quotes/latest?symbols=&feed=opra` | **200**; quote carries full NBBO-ish top-of-book `{bp,bs,bx,ap,as,ax}` |
| **Historical quotes** | `GET /v1beta1/options/quotes?symbols=` | **404 — does not exist.** No historical option NBBO on Alpaca (any tier). Confirmed against docs ([optionlatestquotes](https://docs.alpaca.markets/reference/optionlatestquotes) is the only quotes endpoint) |
| Condition codes | `GET /v1beta1/options/meta/conditions/trade` | **200** — live OPRA mapping (see §2.1); real prints in the probe carried `I` (electronic auto-ex), `S` (**intermarket sweep**), `a` (auction), `f` (**electronic multi-leg**) |
| Exchange codes | `GET /v1beta1/options/meta/exchanges` | **200** — `A`=NYSE American, `B`=BOX, `C`=CBOE, `E`=EDGX, `I`=ISE, `M`=MIAX, `N`=NYSE Arca, … (full OPRA participant set) |
| Streaming | `wss://stream.data.alpaca.markets/v1beta1/opra` — **msgpack only**, subscribe by exact OCC symbol (no wildcards for options), trade msgs `{T:"t",S,p,s,x,c,t}`, quote msgs `{T:"q",S,bx,bp,bs,ax,ap,as,c,t}` | not yet connected (next probe, §8); docs: [real-time option data](https://docs.alpaca.markets/docs/real-time-option-data) |

Other hard facts that shape the design:
- **Rate limits:** Algo Trader Plus = **10,000 req/min** on market data ([tiers](https://docs.alpaca.markets/us/docs/about-market-data-api)); historical trades accepts ≤100 symbols/request, `limit` ≤10,000 rows. REST polling is nowhere near a constraint at watchlist scale.
- **Streaming caps:** the plus tier lists a **1,000-symbol quote subscription cap**; options wildcard subscriptions are unsupported. So "stream all of SPY's chain" (5,000+ contracts) is impossible by construction — any streaming design must manage a **dynamic active set ≤~1,000 contracts**.
- **Universe-wide is physically out:** OPRA is the highest-volume US feed — ~130–170 **billion** messages/day, peak microbursts 75–187M msg/sec, >1M live contracts ([Databento OPRA guide](https://databento.com/microstructure/opra), [Pico on the 2024 96-line expansion](https://www.pico.net/blog/opra-96-line-expansion-the-big-boost-in-latency-and-infrastructure-requirements/)). Watchlist scoping isn't a product compromise; it's the only viable retail architecture.
- **Open interest** is not in the market-data snapshots; it lives on the trading API contracts endpoint (`GET /v2/options/contracts`, `open_interest` + `open_interest_date` fields — read-only metadata, **not** an order path). Verify field presence in the next probe (§8).
- **Existing in-repo options plumbing to reuse:** `brokers/alpaca.py:1134` `get_option_quote` / `:1160` `aget_option_quote` already call `/v1beta1/options/snapshots` with the auth headers + fail-soft parse (`_parse_option_snapshot`, `:1085`). The screener's client code extends this class — no new credential handling.

**The one structural data hole (drives the whole honest framing):** with **no historical quotes**, trade-vs-NBBO direction classification can only happen **at ingest time** (live quote alongside the print). The 2.5-yr backfill can tune *volume and premium* baselines but can never be direction-tagged retroactively. Plan around it; don't pretend otherwise.

## 2. Research — taxonomy, classification, baselines (cited)

### 2.1 What a "sweep," "block," and "split" actually are — and what OPRA marks
- **Sweep** = an order routed simultaneously across multiple exchanges for speed (the buyer pays up rather than wait). On the tape: a burst of same-contract prints, milliseconds apart, across **different exchange codes**. OPRA does carry an explicit ISO condition code — **`S` (ISOI, "executed as an Intermarket Sweep Order")**, plus ISO-auction variants `b`/`d` — and our probe saw a live `S` print on SPY, so the flag is real in Alpaca's `c` field. But a multi-print sweep's *size* is only recoverable by clustering the prints; vendors (FlowAlgo, InsiderFinance) describe detection as cross-exchange clustering with the ISO code as corroboration ([FlowAlgo: what is a sweep/split](http://help.flowalgo.com/en/articles/1104745-what-is-a-sweep-a-split), [QuoteMedia OPRA condition table](https://support.quotemedia.com/support/solutions/articles/13000100515-trade-indicators-for-options-opra-)).
- **Split** = the same aggression on **one** exchange: rapid sequential fills, same contract, same exchange code (FlowAlgo's literal definition).
- **Block** = a single large negotiated print (floor/crossing mechanisms; often condition `e` SLFT, or just an outsized `I`/`a` print). Vendor size thresholds are all over the map (TrendSpider quotes ">10,000 contracts" — index-scale; single names need far lower bars); there is **no industry-standard number**, so ours must be a documented constant (§5).
- **Multi-leg / spread legs are marked** — this is the good news of the condition-code table: `f` (MLET, electronic multi-leg vs complex book — seen live in our Feb-2024 probe pull), `g,h,i,j` and the stock+option combo codes `k…s`. The bad news: legs that execute against single-leg orders (`j` MESL) or auction fills can print without an unambiguous multi-leg mark, so vendors *also* cluster same-second, same-underlying, multi-strike prints to catch spreads. We do both (§5, filter F-2).
- **Hygiene codes:** cancels `A/C/E/G`, late/out-of-sequence `B/D/F/H`, extended-hours `v` — all must be excluded from real-time alerting or your "block" is a bust print. Alpaca's `/meta/conditions/trade` returns this exact mapping at runtime (probe-confirmed), so the code should **fetch and cache the table rather than hardcode it**.

### 2.2 Aggressor-side (buy vs sell initiated) — what's achievable
- The academic benchmark on options specifically is Savickas & Wilson (2003, JFQA 38(4)): **quote rule ≈83% accurate, Lee-Ready ≈80%, EMO ≈77%, tick rule ≈59% (near-coin-flip — never use it for options)** ([paper](https://ideas.repec.org/a/cup/jfinqa/v38y2003i04p881-902_00.html)). Failure modes: outside-quote and *reversed-quote* trades (options market makers routinely buy at the ask), and **midpoint prints are definitionally unclassifiable**.
- Vendors implement exactly this: price at/above ask → "ask-side" (assumed buy), at/below bid → "bid-side"; several explicitly **discard mid fills** rather than guess (InsiderFinance: "no way to tell… does not display orders executed at mid" — [source](https://www.insiderfinance.io/resources/explaining-the-order-flow)).
- **Design consequence:** direction is a 3-value label `buy_side | sell_side | undetermined`, computed only when a fresh quote (stream) or near-in-time quote (REST poll) exists; the record carries `direction_basis` naming which. The UI/agent phrasing is fixed: *"ask-side (quote rule, ~83% class accuracy on options — Savickas-Wilson 2003)"* — a citation, never a probability of the trade being *right*.

### 2.3 "Unusual" baselines — principled vs arbitrary
- **Volume > open interest (vol/OI)** — the most principled public flag: today's volume exceeding all previously-open contracts *guarantees* new positions were opened (OPRA has no open/close flag; next-day ΔOI is the only other evidence). Barchart's documented screen: **vol/OI ≥ 1.25, volume > 500, OI > 100, price > $0.10** ([Barchart UOA](https://www.barchart.com/options/unusual-activity)). Principled, ship first.
- **Volume z-score vs trailing average** — principled *only if bucketed*: baselines must be computed within DTE buckets (§5) because 0DTE/weekly turnover swamps everything (§3.4), and per underlying (a 3σ day in F is a Tuesday in TSLA). "2σ above the 20-day average" circulates as a CBOE definition (UNCONFIRMED primary source) — treat the exact multiplier as a tunable constant justified by our own backfilled distribution, not by appeal to authority.
- **Premium floors** — arbitrary but necessary noise gates. Documented vendor practice: Unusual Whales API defaults `min_premium` **$50k** (flow) / **$250k** (screener), `vol_greater_oi` and `is_otm` as first-class filters, and notably `max_multileg_volume_ratio: 0.1` to suppress spread contamination ([UW API doc](https://unusualwhales.com/skill.md)); "golden sweep" ≥$1M is marketing shorthand. We start at **$25k for single names / $250k for SPY-QQQ** and let the backfill percentiles re-justify (§5).
- **%-of-day's-volume / #prints** — weak standalone; used only as corroboration inside sweep clustering.

## 3. The honest pitfalls — why this must stay an attention router (give this section full weight)

1. **The public signal, alone, has no demonstrated predictive power.** The canonical "options flow predicts stocks" result — Pan & Poteshman (2006, RFS): low put/call stocks beat high by >40bps next-day — was built on **non-public CBOE data** (customer type + open/close + signed direction), and their own decomposition found **"no predictability at all from the public signal"** ([paper](https://academic.oup.com/rfs/article-abstract/19/3/871/1646711)). Ge, Lin & Pearson (2016, JFE) localize the predictive content in **open-buy calls** — a flag OPRA does not carry ([paper](https://www.sciencedirect.com/science/article/abs/pii/S0304405X16000167)). What we can compute from OPRA is the *low-information projection* of those signals. This is THE reason the screener is Mode A perception and never a scored feature.
2. **Directional ambiguity — the #1 way flow misleads.** An ask-side call print is indistinguishable from: a bullish open-buy, a **sell-to-close by a prior winner**, a **hedge** (fund long 1M shares buying puts looks "bearish"; covered-call selling looks like "call flow"), or **one leg of a spread**. Every vendor quietly admits this (WhaleStream: "no fool-proof way of determining a buy or sale"). Real disambiguation requires the paid CBOE Open-Close dataset ([DataShop](https://datashop.cboe.com/cboe-options-open-close-volume-summary)) — out of scope and out of budget by the roadmap's $0/mo rule.
3. **~30–40% of options volume is multi-leg/complex** (ISE historically ~64% spreads in sample months; CME equity-index options ~40% multi-leg in 2023 — [FlexTrade](https://flextrade.com/resources/buy-side-options-trading-covering-the-spread-in-complex-order-books-with-multi-leg-strategies/)). An unfiltered "unusual call volume" alert routinely fires on the long leg of a collar. Spread suppression (F-2) is not optional hygiene; it's a third of the tape.
4. **0DTE rewrote the baselines.** 0-1DTE is ~47–60% of SPX volume (Cboe/OptionAlpha/SpotGamma, 2023–2025: [Cboe](https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options/), [OptionAlpha](https://optionalpha.com/blog/the-rise-of-spx-0dte-trading-analyzing-volume-trends), [SpotGamma](https://spotgamma.com/record-0dte-volume-reshapes-the-sp-500/)) and >40% of SPY's; it is dominated by mechanical/structural flow (Cboe: 100k contracts at a strike can net to ~3k of dealer exposure). Hence DTE-bucketed baselines and a default **0-1DTE exclusion for index/mega-cap underlyings**.
5. **True informed prints are rare even at the leakiest event.** Augustin-Brenner-Subrahmanyam (Mgmt Sci 2019): across 1,859 takeovers, only ~25% show abnormal pre-announcement options activity ([paper](https://pubsonline.informs.org/doi/10.1287/mnsc.2018.3122)); QuantPedia's read of the literature is that the historical put/call alpha faded post-2009 enforcement ([summary](https://quantpedia.com/how-common-is-insider-trading-evidence-from-the-options-market/)). Vendor "win-rate" marketing measures the whale's entry-to-exit, not a copier's achievable return, with survivorship-of-anecdotes doing the rest ([honest-track-record critique](https://optionpilot.ainvest.com/blog/honest-options-track-record-methodology)).
6. **Therefore the product framing is fixed:** flow events are *facts about the tape* ("$1.4M of NVDA Aug $190 calls printed ask-side across 4 exchanges in 300ms — vol 6× OI"), delivered as one input into the founder's connect-the-dots judgment and the convergence watch — **never** "smart money is buying, follow it." Every alert carries the machine-readable caveat field (§6) so the chat agent narrates the ambiguity instead of hiding it.

## 4. Ingestion architecture

**Where it runs:** serv, inside the existing `api/main.py` lifespan job-runner — the screener registers `register_job(Job(name="options_flow", fn=_options_flow_once, interval_s=OPTIONS_FLOW_INTERVAL_SECONDS, enabled=lambda: _OPTIONS_FLOW_ENABLED))`, exactly the `rvol_alert` pattern (`api/main.py:1406-1412`, loop body `:875-943`, interval const `:188-190`). Fail-soft everywhere (a data hiccup logs and skips; never raises into the runner). Env-gated off by default on the PC (the order-flow GUI RAM incident is a lesson: heavy data loops live on serv).

**Scope:** a dedicated **flow watchlist** (default: reuse the app watchlists via `get_watchlists_state()`, capped at `OPTIONS_FLOW_MAX_UNDERLYINGS = 25`). Justification is §1: OPRA-universe is ~10⁵× beyond retail feasibility, Alpaca subscriptions cap at ~1,000 contract symbols, and the mission is watchlist-centric anyway (the founder wants *his* names watched).

**Phase-1 transport: REST polling, not websocket.** Deliberate call: the websocket is msgpack-only, needs a connection manager + dynamic (re)subscription, and contends for the account's stream connection; REST polling reuses `brokers/alpaca.py`'s existing httpx plumbing and is trivially fail-soft. At watchlist scale the poll math is comfortable: per cycle (60s) ≈ 25 snapshots pages + ≤25 trades requests ≈ **≤60 req/min against a 10,000/min budget**. Latency of ~1-2 min is fine for a signal whose honest use is "look at this underlying today," not front-running sweeps. The websocket is Phase 3, only if the eval says intraday sweep detail earns it.

Per cycle (market hours only, reuse `_market_session()` gating):
1. **Active-set refresh** (every 5 min): `GET /v1beta1/options/snapshots/{u}?feed=opra` per underlying → contracts with a trade today; rank by `latestTrade.s` recency/size; keep top `ACTIVE_SET_PER_UNDERLYING = 60` (≈1,500 contracts total worst-case, chunked ≤100/request downstream).
2. **Trades pull** (every 60s): `GET /v1beta1/options/trades?symbols=<chunk>&start=<cursor>` with per-contract cursors (the probe's `next_page_token` pagination). Emits raw prints into the detector.
3. **Quote co-fetch:** for contracts with candidate events this cycle, `GET /v1beta1/options/quotes/latest?symbols=&feed=opra` → direction classification with `direction_basis="polled_quote"` (honest: the quote is up to ~60s newer than the print; §2.2 accuracy is an *upper bound* here).
4. **OI refresh** (daily, pre-open): `GET /v2/options/contracts?underlying_symbols=…` → `open_interest` per contract for vol/OI. (Trading API, read-only metadata; **no order endpoints are touched** — flag for the safety review anyway since it imports near broker code.)

**Storage** (`db/options_flow.py`, new tables — mindful of the ops audit's unbounded-DB-growth finding):
- `options_flow_events` — detected events only (§6 shape), pruned at 180 days.
- `options_flow_daily` — per (underlying, contract, date): volume, prints, premium, close OI, DTE bucket — the baseline store. Tiny (~25 names × ~200 contracts × 250 days ≈ 1.25M small rows/yr; prune expired contracts at 400 days).
- Raw prints are **not** persisted (aggregate on the fly); a `OPTIONS_FLOW_DEBUG_RAW=1` env flag may spool a rolling 24h parquet for debugging.

**Backfill for threshold tuning (one-shot script, PC or serv):** use **`GET /v1beta1/options/bars` (1Day)** — not trades — to build `options_flow_daily` back to Feb 2024 for the watchlist (bars are ~10³× cheaper than prints and baselines only need daily volume/premium). Output: per-underlying, per-DTE-bucket volume distributions → the constants in §5 get set at chosen percentiles with the derivation committed alongside (`research/options_flow/thresholds.md`). Caveat stated up front: 2.5 yr ≈ one bull regime; thresholds are tunable constants, not validated parameters.

## 5. Detection engine — the rules, every threshold a named constant

All constants live in `signals/options_flow/constants.py` with a comment citing this doc's §2/§3 and the backfill percentile that justified the value. Nothing inline, nothing magic.

**Filters (run first, all phases):**
- **F-1 hygiene:** drop conditions `{A,C,E,G}` (cancels), exclude `{B,D,F,H}` (late/OOS) and `v` (ext-hours) from real-time alerts; mapping fetched from `/v1beta1/options/meta/conditions/trade` at startup (cached, probe-confirmed).
- **F-2 spread suppression:** prints with multi-leg conditions `{f…s}` are tagged `spread_leg=True` and excluded from *directional* signals (still counted in volume); additionally, same-underlying prints within `SPREAD_CLUSTER_WINDOW_MS = 1000` at ≥2 strikes with matched sizes → `likely_spread=True`. Mirrors UW's `max_multileg_volume_ratio` idea: an underlying-day whose flagged volume is >`MULTILEG_RATIO_MAX = 0.4` of total gets its unusual-volume event demoted to `spread_dominated`.
- **F-3 0DTE:** for underlyings in `INDEX_MEGACAP_SET` (SPY, QQQ, and any name with daily listed expiries), DTE ≤1 contracts are excluded from unusual-volume baselines and events by default (`ZERO_DTE_EXCLUDE = True`) — §3.4.

**Rules (each independently shippable):**
- **UV-1 vol/OI (ship first — easy, principled):** contract-level `volume_today / max(OI,1) ≥ VOL_OI_RATIO_MIN = 1.25` AND `volume_today ≥ MIN_CONTRACTS = 500` AND `OI ≥ MIN_OI = 100` AND `mid ≥ MIN_PRICE = 0.10` AND premium ≥ `MIN_PREMIUM_USD = 25_000` (`MIN_PREMIUM_INDEX_USD = 250_000` for `INDEX_MEGACAP_SET`). Constants straight from the Barchart/UW documented practice (§2.3), premium floors re-justified by backfill percentiles. → `event_type="unusual_volume"`, direction `undetermined` unless DIR-1 adds it.
- **UV-2 volume z-score:** underlying-level, per DTE bucket `{2-7, 8-30, 31-90, 90+}`: `z = (vol_bucket_today − mean_20d) / std_20d ≥ VOL_ZSCORE_MIN = 3.0` with `std` floored at `ZSCORE_STD_FLOOR` (backfill-derived) to kill thin-name false fires. Catches "the whole call side of X lit up" that no single contract trips.
- **BLK-1 block:** single print `size ≥ BLOCK_MIN_CONTRACTS = 250` AND `premium ≥ BLOCK_MIN_PREMIUM_USD = 100_000`, not `spread_leg`. (TrendSpider's 10k figure is index-scale; 250 on a single name is already >99th percentile of print size — backfill will confirm/adjust.)
- **DIR-1 aggressor tagging (needs a quote):** quote rule with tolerance `QUOTE_EPS = $0.01`: `p ≥ ask − eps → buy_side`; `p ≤ bid + eps → sell_side`; else `undetermined` (never guessed — §2.2). Aggregate per underlying-day into `directional_pressure`: `net_premium_buyside − sellside` per call/put side, event when `|net| ≥ PRESSURE_MIN_USD = 250_000` AND classified fraction ≥ `PRESSURE_MIN_CLASSIFIED = 0.5`. Basis field records `polled_quote` (Phase 2) vs `stream_quote` (Phase 3).
- **SWP-1 sweep (Phase 3 — needs streaming timestamps):** cluster same-contract prints within `SWEEP_WINDOW_MS = 500` spanning ≥`SWEEP_MIN_EXCHANGES = 3` distinct `x` codes, summed premium ≥ `MIN_PREMIUM_USD`; corroborate (not require) condition `S`; direction from DIR-1 on the cluster's prints. Split variant: same window/exchange, ≥`SPLIT_MIN_PRINTS = 5`.

**Dedup/budget:** per (underlying, event_type, day) dedup keyed like `_should_rvol_alert` (`api/main.py:920-927`); global push budget `OPTIONS_FLOW_MAX_PUSH_PER_DAY = 5` (everything still lands in the resource + notifications table).

## 6. Output contract + perception integration

**Event record** (one shape everywhere — DB row, resource row, notification `data`):
```json
{
  "id": "of_2026-07-09_NVDA_0007", "ts": "…Z", "underlying": "NVDA",
  "occ_symbol": "NVDA260821C00190000", "right": "call", "strike": 190.0,
  "expiry": "2026-08-21", "dte": 43, "otm_pct": 8.2,
  "event_type": "unusual_volume|block|sweep|directional_pressure",
  "contracts": 4100, "premium_usd": 1435000, "vol_oi_ratio": 6.1, "vol_zscore": 4.2,
  "direction": "buy_side|sell_side|undetermined",
  "direction_basis": "stream_quote|polled_quote|none",
  "spread_flag": false, "n_prints": 37, "exchanges": ["C","N","M","E"],
  "reason": "NVDA Aug $190C: $1.4M premium, 4,100 contracts (6.1x OI), ask-side, 37 prints/4 exchanges",
  "caveat": "direction inferred from quote rule (~83% class accuracy, Savickas-Wilson 2003); cannot distinguish new position vs close vs hedge vs spread leg"
}
```
Hard rules: **no `win_prob`, no `ev`, no `confidence` field exists in the schema** (the anti-`_find_ev_bucket` lesson, enforced by shape not by prompt); `reason` and `caveat` are deterministic template strings — the LLM narrates *from* them, never writes them.

**(a) Queryable resource — one registry line per the maintenance contract** (`ai/perception/resources.py:550-554`): add `"options_flow"` to `RESOURCES` (`:555`) with `read=_read_options_flow` (filters: `symbol`, `event_type`, `min_premium`, `since`, `limit` ≤50) reading `options_flow_events`, and `summarize=` (today's events by underlying: counts, net classified premium by side, spread-dominated flags). Alias `"flow"→"options_flow"` in `_RESOURCE_ALIASES` (`:689`). Optionally later: an `options_flow` field group in `get_context(symbol)` (`ai/perception/context.py`) so a symbol deep-dive shows today's flow inline. Registered here, `query("options_flow")` and `list_capabilities()` work with zero new tools — the exact design contract of `References/PERCEPTION_LAYER.md`.

**(b) Proactive convergence watch — the actual point of the feature.** A small evaluator `signals/options_flow/convergence.py` runs at the end of each poll cycle: for each new flow event, check within the same session (i) RVOL on the underlying ≥ `CONV_RVOL_MIN = 1.5` (reuse `load_profile`/`_compute_rvol_from_total_volume`, the `api/main.py:875-943` machinery), (ii) price within `CONV_LEVEL_ATR = 0.5`×ATR of a session node / active price-alert level (reuse the session-node + `list_price_alerts` reads), (iii) optionally a fresh headline (`afetch_company_news`). **≥2 components → one `notify(kind="convergence", …)`** (`signals/notify.py:43` — persist-first, WS broadcast, push respecting the daily budget) whose body lists the *component facts*: "NVDA: $1.4M ask-side call flow + RVOL 2.3× + sitting on VAH $188.40". Single-component flow events notify only above `SOLO_PUSH_PREMIUM_USD = 1_000_000`; everything else is pull-only via the resource. Convergence alerts are the connect-the-dots product: information comes to the founder; judgment stays with him.

**(c) Journal/eval loop (what keeps it honest over time):** every pushed event/convergence is recorded (notifications table + a nightly job stamping forward 1d/5d underlying returns onto `options_flow_events`). After a quarter, a simple report — "flow events fired N times; founder acted on K; hit-rate of *acted* ones vs ignored" — decides whether flow ever graduates to a DecisionCard *input* (Tier-B thesis support, still no fabricated numbers) or gets its kill-date invoked, per the roadmap's every-avenue-has-a-kill-date rule. No backtest gauntlet is claimed or needed — this measures *usage value*, not alpha.

## 7. Phased build plan (each phase independently shippable, observe-only)

| Phase | Ships | Effort | Depends on |
|---|---|---|---|
| **1 — EOD unusual volume + resource** | backfill script (bars→`options_flow_daily` + thresholds doc); nightly UV-1/UV-2 scan (EOD, snapshots+bars only); `options_flow` RESOURCES entry + alias; events table + pruning | **S (2-3 days)** | nothing new — REST + existing alpaca client |
| **2 — intraday polling + blocks + convergence** | the 60s job-runner loop (active set, trades cursor-pull, quote co-fetch); BLK-1 + intraday UV-1; DIR-1 with `polled_quote` basis; convergence watch + `notify` w/ budgets; OI daily refresh | **M (4-6 days)** | Phase 1; probe item P-2 (OI field) |
| **3 — streaming + sweeps** | msgpack websocket client (`opra` feed), dynamic ≤1,000-contract subscription manager; SWP-1 sweep/split clustering; DIR-1 upgraded to `stream_quote` at-print classification | **L (1-2 wks)** | Phase 2 + probe items P-1/P-3; only if the Phase-2 eval shows the founder actually uses flow |
| **4 — eval + graduation decision** | forward-return stamping job; quarterly usage/hit-rate report; founder decision: DecisionCard Tier-B input, keep-as-is, or kill | **S (1-2 days) + a quarter of runtime** | Phase 2 |

Safety posture, every phase: no new broker-write path; the only trading-API call is the read-only contracts-metadata GET; `trading/` untouched (still run the `trading-safety-adversary` ritual on the PR that touches `signals/push`-adjacent notify budgets); serv-only heavy loops; env-gated; fail-soft.

## 8. Open questions for the founder + the next technical probe

**Founder decisions:**
1. **The one that matters most: Phase-3 scope discipline.** Sweeps/streaming are the seductive half (the vendor marketing half) and the expensive half — and §3 says the extra directional fidelity may not change any decision you make. Recommendation: **ship Phases 1-2, run the eval a quarter, and let usage data authorize Phase 3** — decide now that that's the rule, so the shiny-object pull is pre-answered.
2. **Flow watchlist composition:** your core ~15-25 names — and do SPY/QQQ stay in (index flow ≈ macro/0DTE noise; they get the $250k floor + 0DTE exclusion) or out entirely?
3. **Push budget:** is 5 flow/convergence pushes/day the right ceiling for step-away trust (vs pull-only with a morning digest)?

**Next probe (one market-hours session, read-only, extends `scripts/opra_probe.py`):**
- **P-1** websocket: connect `wss://stream.data.alpaca.markets/v1beta1/opra` (msgpack), subscribe 5 active contracts' trades+quotes; confirm message schemas, the practical subscription cap for Algo Trader Plus, and whether the options stream contends with the equities stream connection.
- **P-2** `GET /v2/options/contracts?underlying_symbols=SPY&limit=5`: confirm `open_interest`/`open_interest_date` fields on our subscription (needed for UV-1).
- **P-3** live-hours field check on `/options/trades`: confirm intraday prints arrive with ≤~seconds lag and which `feed` the historical endpoint served (we passed none and got OPRA-grade data — verify entitlement default), plus condition-code frequency counts over one hour to sanity-check F-1/F-2 assumptions.
- **P-4** snapshot full key dump on one contract (does it carry `dailyBar` volume? If yes, active-set ranking simplifies and Phase-1 needs fewer bars calls).
