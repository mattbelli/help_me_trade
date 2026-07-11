# Flow capacity research — how others navigate the OPRA-budget problem

*2026-07-11 · 3-agent Sonnet web-research pass, triggered by the founder's "how sure are you
this is the way?" on the two-tier / dynamic-depth streaming design. Dense summary; each agent
marked confirmed vs inferred. Companion: `FLOW_DTE_DIVERSITY_SPEC_2026-07-11.md`.*

## Verdict — what changed and what held

**The architecture's SHAPE is externally validated; the specific mechanisms are novel-but-sound;
and the research opened one door that must be probed BEFORE building the coarse tier.**

## Validated by external evidence

1. **The two-tier split IS the industry structure.** The market bifurcates exactly along our
   design line: Barchart + Market Chameleon are *delayed snapshot* UOA screeners (25-30min
   delay, vol/OI ≥1.25 or ~2× 90-day avg volume, recomputed every 5-10min); Unusual Whales +
   OptionStrat + CheddarFlow are *real-time tape* sweep/block products (sub-second the
   marketing bar). These are recognized as different product categories with different latency
   bars — our "coarse market-wide screen + deep real-time tape" = both categories in one app,
   not a compromise of either.
2. **"Trades broad, quotes narrow" is vendor-intended, not our workaround.** Polygon/Massive
   independently enforces the identical asymmetry: options *trades* channel supports `*`
   wildcard; options *quotes* capped at 1,000/connection, no wildcard. Alpaca documents the
   1,000 cap as a *quotes* cap (Algo Trader Plus tier).
3. **Our $25k premium floor = Unusual Whales' confirmed default** ($25k or ≥150 contracts to
   display). The one industry parameter anyone publishes, and we already match it.
4. **Our DIR-1 aggressor rule is the standard heuristic** (at/above ask = buy-side — Barchart's
   sentiment method verbatim). Our sweep detection (multi-print multi-exchange tight-window
   clustering) is exactly the practitioner pattern; windows/thresholds found in the wild
   (100ms, 400ms, $10k-$1M tiers) are all vendor-specific — **no industry standard exists**,
   so our parameters are as legitimate as anyone's.
5. **Sub-second is the bar for "flow alerts"; 15-30min delay is acceptable only for daily UOA
   screeners.** Our 12ms stream clears the alert bar; the 30s poll does not (correctly treated
   as fallback); an hourly snapshot rotation is squarely within screener norms.

## Novel — we'd be first-documenting (not invalidated, just unprecedented)

- **The 5-min rotation active set with DTE quotas**: no forum post, GitHub repo, or blog
  documents ANY dynamic-subscription rotation strategy for capped options websockets. No
  reference implementation of our architecture exists in open source.
- **The premium-concentration curve** (top-40/name ≈ 71%, top-200 ≈ 96% of daily premium):
  no published benchmark measures contract-level concentration this way. Directionally
  consistent with known structure (volume clusters ATM/front-month; ~85% of option volume in
  the top-10 underlyings — symbol-level analog). Treat as our own measurement; re-measure
  across ≥2 weeks of `options_flow_daily` before sizing budgets off it. One-day sample so far.

## Doors opened (follow-ups)

1. **⚡ PROBE FIRST: Alpaca trades-channel wildcard.** Alpaca docs forbid `*` only for option
   *quotes* ("there are simply too many of them") and are **silent on the trades channel**;
   Polygon's trades channel explicitly supports `*`. Our 2026-07-09 probe never tried it.
   If `{"action":"subscribe","trades":["*"]}` ACKs on the OPRA ws → **market-wide real-time
   trade tape on the current plan**, and the coarse tier becomes real-time instead of hourly
   snapshot rotation (which would then be unnecessary). 5-minute probe at Monday's open
   (must stop the prod stream first — 1-conn limit). Client-side load is the risk to measure:
   full OPRA trade rate, not our 14 msg/s watchlist trickle.
2. **Databento Standard OPRA = $199/mo** (confirmed price; UNCONFIRMED whether that tier is
   true real-time full-feed vs delayed/limited — check directly if Alpaca caps ever bite).
   Direct OPRA enterprise = $6,100/mo minimum (confirmed floor). ⚠️ Multi-user future:
   cheap tiers are personal/display use; *redistributing* flow to app users triggers OPRA
   redistributor/non-display fees (~$1.5-2k/mo, secondary-sourced) — fine for the founder's
   cockpit, a real licensing decision if flow becomes a user-facing product feature.
3. **None of the 7 commercial platforms discloses its pipeline** (only Tradytics names
   Polygon, and only for stocks). All claim "full tape" in marketing; zero technical
   writeups. There is no proven playbook to copy — probe-and-measure is the frontier.

## Priority consequence for the build

Probe (1) gates the coarse-tier design: **do the wildcard probe before building the snapshot
rotation**. If wildcard works, the two-tier design simplifies to: full-tape trades stream
(coarse+fine in one) + hot-quote subset for side confirmation + DTE quotas only for the
*quote* budget. If it fails, the spec's snapshot-rotation coarse tier stands as designed.

## ⚡ Probe result (same day, Sat 2026-07-11)

`{"action":"subscribe","trades":["*"]}` → **`{"T":"subscription","trades":["*"]}` — ACKED**
on the current plan (`scripts/opra_wildcard_probe.py`; won the 1-conn slot from the idling
prod stream by retry-on-406 during a backend bounce; prod reconnected via its 300s backoff,
zero impact on a closed market). ACK ≠ data: Monday-open follow-up = confirm prints actually
flow under `*`, measure market-wide msg rate + client headroom (OPRA ≈50M+ trades/day, open
bursts 10-20× average) and lag under load. If flow confirms, the coarse tier IS the full tape.
