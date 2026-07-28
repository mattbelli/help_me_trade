# Live flow pairing — build spec (2026-07-28, founder-directed)

*The whole-market live lane end-state the founder set 07-28 (board `whole-market-flow-corpus`
notes 28s/28t/28u): Databento live carries the market-wide trade+quote spine; Alpaca contributes
ONLY condition codes via wildcard trades; a live matcher pairs them per print. The 1,000-contract
Alpaca quote lane turns OFF at the end of the rollout. This doc is the build spec; Inc 1 (the
matcher + replay rig) shipped 2026-07-28. **Formally supersedes decision #93** ("no production wildcard trades lane") — #93 forbade a
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
  connection gets `406`. ⚠️ Ownership updated 07-28: since `options_flow.provider=databento`
  went live (07-27, whole-market spine in production all session) the collector does NOT open
  the Alpaca socket at all — it is free, and the standalone recorder probe
  (`tools/alpaca_cond_recorder.py`) holds it during the 07-28 measurement session. End-state
  unchanged: the wildcard-trades subscription and the matcher live **inside
  hmt-flow-collector** beside the Databento client, so prints never cross a process boundary
  to be paired — whichever process runs the sidecar is the socket's one owner, never two.
- **Never block the spine on the sidecar — but match-first whenever the twin is in hand**
  (founder 07-28: don't ship unlabeled flow when labeling costs microseconds). Three cases:
  (1) twin already arrived → match inline, the print emits fully labeled, never `pending`;
  (2) twin lands within the pipeline's existing ~1s flush tick → the emit path checks
  enrichment at drain time, so it ships pre-matched at ZERO added latency vs today's cadence;
  (3) twin genuinely late (the open burst — measured on the 07-14 wildcard probe and the 07-16
  bell verify: per-print lag peaks at **6–10s** with the backlog episode lasting ~2–3 minutes
  after the bell, Alpaca-fanout-side with our client at ~150x headroom) → emit
  signed-but-`pending` immediately and enrich in place when the code arrives; holding
  direction-signed flow even seconds at 9:31 to wait for a package flag is the one trade that
  is never worth it. The rule: never emit unlabeled when the label is available or imminent
  within the flush tick; never hold longer than the flush tick. A print that never enriches
  expires to `cond=''` (heuristic fallback, same as the corpus). Inc 2 measures the fraction
  of twins arriving inside 1s (midday vs open) — the expected steady state is match-first as
  the norm, enrich-late as the burst exception.
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

⚠️ Ground truth moved under this section on 07-28: the provider flip to Databento (live in
production since 07-27) already stopped the capped quote/trades lanes — under
`provider=databento` the runner never starts `OptionsFlowStream`, so nothing subscribes the
1,000 quotes today. The Alpaca lane is retired *de facto*; what Inc 3 owns now is making that
**deliberate and verified** rather than a side effect:
- **Standing-NBBO consumer audit** (below) — now an audit of what may have silently regressed
  since 07-27, not a pre-cutover gate: enumerate every consumer of `latest_quote`/`quote_at`
  and confirm each works off nbbo-on-print or REST snapshots.
- The capped-lane code stays revertable by config (`provider=alpaca`) exactly as before.

Original prerequisites, verified not assumed:
- **Standing-NBBO consumer audit.** `tcbbo` quotes only exist at trade time. Anything needing a
  continuous option NBBO (position marks, UI quote displays, spread-width checks at arbitrary
  times) moves to REST snapshots (10k req/min budget, ~30 used) BEFORE the quote lane dies.
  The audit lists every consumer of the current quote stream and its disposition.
- **Replay validation, then a short live shadow** (founder 07-28: replay replaces the original
  ≥5-session parallel run — both live inputs already exist on disk for all 251 corpus sessions,
  so the matcher is validated deterministically against the batch join's known answers instead
  of against a live overlap with no ground truth). Direction parity needs no live run either:
  the quote lane's stored `options_flow_events` diff retroactively against the corpus. Live
  time is spent ONLY on what replay cannot see — real wire-arrival lag and live-WS per-root
  coverage — which takes 1–2 shadow sessions, not 5.
- **Fallback path.** The capped-lane code stays in the tree (dead, flagged) for one release
  after cutover; the collector can be reverted by config, not by git archaeology.

## Increments

- **Inc 1 — matcher + replay validation. ✅ DONE 2026-07-28.** `signals/options_flow/
  live_pairing.py` (StreamingCondMatcher — the frozen join streaming, k-slots tombstoned on
  expiry, idle-key GC, capped rings) + `tools/live_pairing_replay.py` (replays a session's
  tcbbo DBN + Alpaca condition npz in simulated arrival order, diffs against `match_prints`).
  Pinned by `tests/test_live_pairing.py` incl. a randomized streaming≡batch equivalence test.
  Measured on 2025-12-24 (half-day, 8.07M events): **315k events/s** in pure Python (~24x the
  13k/s both-sides whole-market peak), per-print agreement with batch 98.79% decomposing to
  **stream-miss = 0** (streaming is a strict SUPERSET of the batch join — its idle-key GC
  resets the k-rank cascades that vendor-missing copies inflict on a whole-session rank),
  48,509 extra labels recovered, 273 code-clashes (0.007%). Under a 300s artificial Alpaca
  lag: pending peak 226k entries, zero evictions, still stream-miss = 0.
- **Inc 2 — live shadow + consumers in parallel (1–2 sessions). Shadow BUILT 2026-07-28,
  ships OFF behind `OPTIONS_FLOW_PAIRING_SHADOW`** (`signals/options_flow/shadow_pairing.py`;
  fed by dbn_stream's guarded pre-meta-gate `on_raw_print` tee; shadow task excluded from the
  collector's exit wait — deploy = kill the standalone recorder, flip the flag, restart the
  collector after the close). Databento live client +
  wildcard trades sub + the validated matcher inside hmt-flow-collector, metrics + sampled
  paired-print log only. Measures ONLY what replay cannot: real wire-arrival lag distributions
  (open vs midday, both sides), live-WS per-root coverage (⚠️ index roots: SPXW rides SPX on
  the Alpaca WS lane and NDX is absent there), and CPU/RSS on serv. Same window: detectors/
  gauge read the enriched spine alongside the capped lane, outputs compared, provenance
  stamped. Package/clean segregation allowed; no new scoring. Gate: live match rate within a
  measured, explained distance of that day's batch join; consumer divergences trace to
  coverage, not bugs.
- **Inc 3 — cutover.** Quote subs off, capped lane off, spine is primary; REST snapshots serve
  standing-NBBO consumers; nightly audit alarm armed (live-vs-batch gap). The Alpaca socket
  carries exactly one subscription: `trades: ["*"]`.

## Open items the live shadow (Inc 2) must measure (recorded so they don't become assumptions)

1. Databento live tcbbo end-to-end lag vs Alpaca trade arrival (both sides' lag, not just one).
2. Per-root Alpaca wildcard coverage — especially SPX/SPXW/XSP/VIX index roots (the corpus
   census says index-open prints are where Alpaca's history thins; is the LIVE stream better or
   worse there?).
3. Matcher RSS on a full-day open burst (replay says throughput is a non-issue: 315k events/s
   measured vs a 13k/s whole-market peak — the equity-close-burst precedent holds, Rust stays
   declined; memory at a multi-minute open lag is the one resource replay can't price in situ).
4. First-invoice confirmation that live OPRA message volume bills flat on Standard.

## Not in scope

- Corpus/rollup changes of any kind (v6 just landed; registered rules untouched).
- Code-family priors, win%, or any scored surface — gated on 1.8-on-truth.
- Options order execution paths — nothing here touches `trading/` or the chokepoint.
