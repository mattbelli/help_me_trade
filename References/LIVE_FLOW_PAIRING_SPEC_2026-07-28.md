# Live flow pairing — build spec (2026-07-28, founder-directed)

*The whole-market live lane end-state the founder set 07-28 (board `whole-market-flow-corpus`
notes 28s/28t/28u): Databento live carries the market-wide trade+quote spine; Alpaca contributes
ONLY condition codes via wildcard trades; a live matcher pairs them per print. The 1,000-contract
Alpaca quote lane turns OFF at the end of the rollout. This doc is the build spec; no code exists
yet. **Formally supersedes decision #93** ("no production wildcard trades lane") — #93 forbade a
lane the code silently *assumed*; this is a deliberately built, spec'd consumer.*

## What it is (three pieces, one process)

1. **Spine — Databento live `tcbbo`, whole market.** Every OPRA print with the BBO in force at
   trade time → direction + aggressiveness (`u`, at-ask/at-bid/mid) computed at arrival for
   every contract in the market, no subscription cap. Plumbing exists: `signals/options_flow/
   dbn_stream.py` + `FeedLiveness` (Phase 0 liveness DONE 07-27, probed against the real
   gateway). Live OPRA is included in the founder's Databento Standard plan (founder-confirmed
   07-28, ran live that day; ⚠️ per `verify-spend-caps`: confirm on the first invoice that
   message-volume billing is genuinely flat before Inc 3 makes us depend on it).
2. **Sidecar — Alpaca wildcard trades, whole market.** `trades: ["*"]` on the existing OPRA
   socket — the print's condition code `c` is IN the message (`stream.py:628` already parses
   it). We take codes only; no quotes, no direction from this stream.
3. **Matcher — the frozen join, streaming.** Same semantics as `tools/condition_labels.py`
   (which the corpus sweep just validated across ~2.0B prints): key = (contract, price_cents,
   size), k-th-identical occurrence within key, |Δt| ≤ 100ms on the shared OPRA participant
   timestamp (offset measured exactly 0ns). The batch module stays the corpus authority; the
   streaming matcher is a separate implementation of the SAME frozen rule, and Inc 1 verifies
   it against the batch join on the same day's data.

## Hard constraints (violate any of these and the build is wrong)

- **One OPRA socket per account.** Alpaca allows exactly one options websocket; a second
  connection gets `406`. `hmt-flow-collector` owns it today. Therefore the wildcard-trades
  subscription and the matcher live **inside hmt-flow-collector** — not a new daemon fighting
  for the socket. The Databento live client also runs in that process (different vendor, no
  conflict) so prints never cross a process boundary to be paired.
- **Never block the spine on the sidecar.** Alpaca's open-burst queue lag is measured upstream
  behavior — its prints can arrive **minutes** late at the open, exactly when the tape matters
  most. Lifecycle: a Databento print emits immediately (`cond=pending`, direction already
  signed); when the Alpaca twin lands, the record enriches in place and re-notifies consumers
  that subscribed to enrichment. A print that never enriches expires to `cond=''` (heuristic
  fallback, same as the corpus).
- **Bounded memory.** Pending-match buffers are rings with a hard entry cap and a time cap
  (10 min — generously above measured open-burst lag). Overflow evicts oldest WITH a counter
  (`dropped_pending`), never silently (the F7 lesson). Budget target: worst case ~6M pending
  entries at open burst ≈ low hundreds of MB; Inc 1 measures the real number.
- **The corpus firewall.** Live pairing is INTRADAY-ONLY. The research corpus (`flow_corpus.db`)
  is written exclusively by the nightly REST chain (`tools/nightly_flow_capture.sh` → rollup) —
  complete-by-construction, join-ledger-audited. The live matcher never writes corpus tables.
  Each night the REST join doubles as the audit: live match rate vs batch match rate per session
  = the daemon's measured drop rate, logged, alarmed if the gap grows.
- **No code-family interpretation before 1.8-on-truth lands.** The daemon ships codes on prints;
  what a `t` block or `f` package *means* for detectors waits for the corpus re-run's measured
  priors. Until then consumers may segregate (package vs clean) but not score.

## What turns OFF, and when

At Inc 3 (not before): the 1,000-contract quote subscriptions and the capped/watchlist trades
lane both retire — direction comes from the Databento spine for every name. Prerequisites,
verified not assumed:
- **Standing-NBBO consumer audit.** `tcbbo` quotes only exist at trade time. Anything needing a
  continuous option NBBO (position marks, UI quote displays, spread-width checks at arbitrary
  times) moves to REST snapshots (10k req/min budget, ~30 used) BEFORE the quote lane dies.
  The audit lists every consumer of the current quote stream and its disposition.
- **Parallel-run validation.** Inc 2 runs both lanes ≥ 5 sessions: per-print direction from the
  Databento spine vs our own live-quote DIR-1 on the overlapping (subscribed) names. Divergence
  is measured and explained before the reference lane is removed.
- **Fallback path.** The capped-lane code stays in the tree (dead, flagged) for one release
  after cutover; the collector can be reverted by config, not by git archaeology.

## Increments

- **Inc 1 — shadow matcher (no consumers).** Databento live client + wildcard trades sub +
  streaming matcher inside hmt-flow-collector, emitting only metrics + a sampled paired-print
  log: match rate (per root — ⚠️ index roots specifically: SPXW rides SPX on the Alpaca WS lane
  and NDX is absent there; per-root coverage tells us which index codes we actually get live),
  enrichment-lag distribution (open vs midday), pending-buffer peaks, CPU/RSS, dropped counters.
  Runs ≥ 3 sessions. Gate: live match rate within a measured, explained distance of that day's
  batch join; process overhead acceptable on serv.
- **Inc 2 — consumers in parallel.** Detectors/gauge read the enriched spine alongside the
  existing capped lane; outputs compared (burst/cluster counts, gauge values), provenance
  stamped per event (`coverage`/source discipline as today). Package/clean segregation allowed;
  no new scoring. Gate: consumer parity understood; divergences trace to coverage, not bugs.
- **Inc 3 — cutover.** Quote subs off, capped lane off, spine is primary; REST snapshots serve
  standing-NBBO consumers; nightly audit alarm armed (live-vs-batch gap). The Alpaca socket
  carries exactly one subscription: `trades: ["*"]`.

## Open items Inc 1 must measure (recorded here so they don't become assumptions)

1. Databento live tcbbo end-to-end lag vs Alpaca trade arrival (both sides' lag, not just one).
2. Per-root Alpaca wildcard coverage — especially SPX/SPXW/XSP/VIX index roots (the corpus
   census says index-open prints are where Alpaca's history thins; is the LIVE stream better or
   worse there?).
3. Throughput/CPU at the open burst and close burst in Python; Rust only if measured need
   (equity close burst precedent: 117k msgs/s = 20.3% of a core — 5x margin, Rust declined).
4. First-invoice confirmation that live OPRA message volume bills flat on Standard.

## Not in scope

- Corpus/rollup changes of any kind (v6 just landed; registered rules untouched).
- Code-family priors, win%, or any scored surface — gated on 1.8-on-truth.
- Options order execution paths — nothing here touches `trading/` or the chokepoint.
