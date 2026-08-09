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
  (`dropped_pending`), never silently (the F7 lesson). Budget MEASURED 07-28 (review, real
  class on serv): ~484 B retained per pending print (the expiry ring pins the key tuple +
  OCC string for the full horizon even when paired), so the cap is `MAX_PENDING=2M` ≈ 1 GB
  hard ceiling — vs a measured 226k stress peak. Expected real add on a healthy session:
  ~125 MB midday, ~250-400 MB across the open burst; the live shadow reports RSS per stats
  line. (The original "~6M ≈ low hundreds of MB" estimate was ~10x optimistic — corrected.)
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

**RETIRED 2026-08-09 — the "one clean release" condition was met** (cutover 07-27; enriched
sessions 08-04..06 at baseline; Friday 08-07 was vendor weather on the OTHER lane; the revert
was never needed). Deleted: `signals/options_flow/stream.py` (OptionsFlowStream),
`poll.anchor_quote_symbols`/`active_subscription_symbols`, the runner's stream callbacks, the
four quote-budget constants (QUOTE_ANCHOR_BUDGET/HOT_QUOTE_MAX/HOT_QUOTE_TTL_SECONDS/
OPRA_QUOTE_SUB_CAP), `scripts/opra_replay_bench.py`, and the capped-lane tests.
`stream_run` now refuses LOUDLY on any `options_flow.provider` other than databento (default
flipped to databento); the poll lane's `emit_blocks` is constant False. `STREAM_TRADES_MAX`
survives — it caps the POLL lane's active set. Revert is git archaeology by design. Soft
verifies closed the same round: print-lab rows carry the derived `spread` marker (76.2% of
08-06 lab rows — live, non-degenerate) and sweeps/blocks daily counts are plausible across
the 08-03 boundary (sweeps hold 10–13k/day; blocks 671–940 → 2,099–2,808/day = the documented
BLK-1 whole-market move; spread_flag 0–9/day healthy vs 623+373 on degraded Friday — the
late-repair path fires exactly when codes arrive late). Collector rebooted clean on the
retired tree (PID 3126664).

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
  - **2026-08-03 — enrichment WIRED (founder-directed: "wire trade matching classification
    now").** The matcher's resolutions now stamp condition codes onto the spine's normalized
    prints in place: `on_dbn` returns a print_id, dbn_stream registers the normalized dict via
    `attach_print`, and the resolution (inline, on the twin's arrival, or on the 1s sweep)
    assigns `conditions=[code]` — a fresh list, never a mutation, so the drain thread reads
    either state coherently. Emit rules honored as specced: match-first when the twin is in
    hand (Inc-2 session #1 measured ≤1s = 98.6%), never hold the spine, late prints ship
    unlabeled (counted). Downstream F-2 spread-leg exclusion, hygiene exclusion, and the ISO
    sweep flag activate with zero detector changes. Segregation only — still no code-family
    scoring before 1.8-on-truth. `OPTIONS_FLOW_PAIRING_METRICS_ONLY=1` reverts to the pure
    Inc-2 shadow. Same round: BLK-1 moved onto the spine (whole-market blocks; the REST poll
    lane's block detection is gated off under provider=databento so one block ≠ two rows).
    Remaining Inc-3 tail: standing-NBBO consumer audit, nightly live-vs-batch audit alarm,
    retiring the capped-lane code after one clean release.

## Open items the live shadow (Inc 2) must measure (recorded so they don't become assumptions)

1. Databento live tcbbo end-to-end lag vs Alpaca trade arrival (both sides' lag, not just one).
2. Per-root Alpaca wildcard coverage — especially SPX/SPXW/XSP/VIX index roots (the corpus
   census says index-open prints are where Alpaca's history thins; is the LIVE stream better or
   worse there?). **ANSWERED 2026-08-09:** the standing d<a gap on these roots is the overnight
   **Cboe GTH session** — Databento OPRA.PILLAR carries NO Global Trading Hours (zero records
   00:00–13:15 UTC, live and historical, tcbbo AND trades, measured via free record counts),
   while the Alpaca WS delivers the full GTH tape (~60k prints/night, only on GTH-eligible
   roots; NDX/NDXP zero = the authenticity signature). Intra-RTH the spine is complete — on
   2026-08-06 dbn GAINED 2,587 SPXW prints on Alpaca over the session. Overnight Alpaca prints
   become `alpaca_orphans` and expire (harmless, bounded); detectors read the spine so no
   overnight events are minted. Consequence for scope: every "whole-market" claim about the
   spine or the corpus means RTH-only; overnight index-flow perception, if ever wanted, rides
   the Alpaca lane alone. **It is now wanted and shipped (Phase B, 2026-08-09):** the sidecar
   tees GTH prints to `data/gth_tape/` (`gth_tape.py`) and a z-scored morning line lands at
   ~9:16 ET on report/perception/widget — card `gth-overnight-phase-b`.
3. Matcher RSS on a full-day open burst (replay says throughput is a non-issue: 315k events/s
   measured vs a 13k/s whole-market peak — the equity-close-burst precedent holds, Rust stays
   declined; memory at a multi-minute open lag is the one resource replay can't price in situ).
4. First-invoice confirmation that live OPRA message volume bills flat on Standard.

## Standing-NBBO consumer audit — RUN 2026-08-09, CLEARED (two provenance repairs shipped)

Method: repo-wide sweep for `quote_at`/`latest_quote`/`get_option_*` callers and every
`direction_basis` reader, each disposition verified against live state (events table by
day × type × basis; collector journal), not assumed from the code alone.

| # | consumer | quote source today | disposition |
|---|----------|--------------------|-------------|
| 1 | `sweep_loop` print classification — DIR-1 + mid_side (`runner.py:500`) | nbbo-on-print: tcbbo stamps `nbbo` at ingest, basis `dbn_nbbo`; `dbn_stream.quote_at` returns `None` honestly | **COVERED** — verified live: block events carry `dbn_nbbo` daily |
| 2 | sweep clusters (`sweeps.py:_cluster_direction`) | per-print direction (from #1) | **WORKS; basis stamp REPAIRED 08-09** — matched the literal `"stream_quote"` only, so every sided sweep since 07-27 stamped basis `none` (~45k/wk sided sweeps mislabeled) |
| 3 | bursts + clusters (`rolling.py`) | per-print direction (from #1) | **WORKS; basis stamp REPAIRED 08-09** — hardcoded `"stream_quote"` when sided: a false era claim for every sided burst/cluster since 07-27 (~700/day) |
| 4 | poll lane classification (`poll.py:448-521`) | REST `get_option_latest_quotes` → `polled_quote` | REST — unaffected, verified live (directional_pressure rows) |
| 5 | sweep detail enrichment (`runner.py:150-183`) | REST option snapshots (greeks/IV/OI/quote) | REST — unaffected |
| 6 | chain snapshots (`poll.py:638`, `signals/options_chain_snapshot.py`, `black76.py`) | REST chain snapshot | REST — unaffected |
| 7 | post-close samplers (`tools/options_chain_sampler.py`, `tools/index_chain_sampler.py`) | REST, post-close | REST — unaffected |
| 8 | capped-lane internals (`stream.py` quote store, `poll.anchor_quote_symbols`) | the retired lane itself | N/A — dead under `provider=databento`, revertable by config |
| 9 | UI option-quote display | — | **does not exist** (frontend has zero option-quote fetches) |
| 10 | option position marks | — | **no marking path exists** — books/trading are equity-only; portfolio values come from the broker positions API |
| 11 | agents/perception (`options_flow`, `unusual_volume` resources) | events DB reads | N/A — no live quote dependency |
| 12 | **options-flow WIDGET** (`options-flow-widget.tsx`) — score + basis label + why text | reads the stored `direction_basis` string | **MISSED by this audit; REPAIRED 2026-08-09** — see the amendment below |

Blast-radius check on the two mislabels: every BEHAVIORAL reader keys off `direction`,
not `direction_basis` — unusual-rank `+3 known aggressor` (`db/options_flow.py:406`),
convergence, fusion intake dots, and the caveat/honesty text (`_caveat_for_direction`)
were all correct throughout. The damage was provenance-only: stored events from the dbn
era are indistinguishable from the Alpaca era exactly where the retro corpus diff and
1.8-on-truth need the split. Rows written 07-27→08-09 keep their wrong stamps (era is
recoverable from the date + this note; never backfilled).

**AMENDMENT 2026-08-09 (same day, later): the blast-radius claim above was WRONG in one
place — consumer 12.** The sweep above searched Python; the frontend was never grepped,
and row 9 ("UI option-quote display does not exist") is true about quote *fetches* but was
read as clearing the UI. The widget consumes the basis *label*, not a quote, in three
places — and one of them is behavioral, which is exactly what the paragraph above says
does not exist: `computeScore` awarded its **1.5-point "confirmed aggressor"** component
on `direction_basis === "stream_quote"`, so every `dbn_nbbo` event scored 1.5 lower on a
0–10 scale; the detail panel rendered the raw string `dbn_nbbo` in the *side basis* cell;
and the why-line printed **"side off a polled quote"** — a false provenance claim, since
that side came from the NBBO on the print, not a ≤30s REST quote. Measured before the fix:
7,344 sided `dbn_nbbo` events already in the store, and 1,374 vs 679 `stream_quote` on
08-07, so ~2/3 of confirmed-aggressor flow was mislabelled. **Retiring the capped Alpaca
lane the same morning escalated this to 100%**: `stream.py` is deleted, `dbn_stream.py` is
the only transport and always stamps `quote_basis`, so `runner.py:501`'s `"stream_quote"`
fallback is now unreachable and the widget's literal matched nothing. Fixed by an
`AT_PRINT_BASES` set (`dbn_nbbo` + `stream_quote`, the latter kept because historical
events still render); `polled_quote` still routes to its own honest label. **Lesson for
the next audit: "every reader of X" means every LANGUAGE, not every `.py`.**

Known follow-up, NOT fixed here (pre-existing, historical rows only): `honestyFlag` returns
`"—"` — its strongest flag — for a sided event whose basis is literally `none`, so the
~91k rows mislabelled between 07-27 and the 08-09 repair display as fully confirmed.

**VERDICT: no consumer needs a standing option NBBO. The quote lane's retirement is now
deliberate and verified, not a side effect.** Remaining Inc-3 tail: capped-lane code
retirement after one clean release.

## Overnight quotes — ANSWERED 2026-08-09: there are none, on either session (`tools/gth_quote_probe.py`)

The approved overnight-aggressor plan (a rolling ~800-contract quote subscription) assumed
Alpaca quotes outside RTH. It does not. The curb session (16:15–17:00 ET) was proven
quoteless on 07-31; GTH (20:15–09:15 ET) carries normal condition codes and was left
genuinely open, so `tools/gth_quote_probe.py` sampled it — read-only, no socket, no writes,
cron'd 01:00/05:00/08:00 + 12:30 UTC Mon–Fri.

**Verdict: quoteless, unambiguously.** 46 root-samples (23 polls × SPXW/XSP) across 7
sessions 07-31→08-07, at 16:15/16:20, 21:00, 01:00, 04:00 and 08:30 ET. **Every single
sample returned 0 classifiable contracts** — not "few", zero, out of 3,000 contracts
fetched per root each time — while 20–854 contracts were *actively printing* in that same
snapshot. `newest_quote` never moved off the prior session's `19:59:59.999 UTC` (15:59:59
ET) no matter how many hours later the poll ran. Trades flow all night; the quote book
freezes at the RTH bell and stays frozen.

Consequences, and they are structural rather than a tuning problem:

- **Overnight flow can be measured for ACTIVITY but never for DIRECTION**, on any lane we
  hold. This is the measured basis for the GTH Phase A/B rule "activity only, never
  direction" — it is now a finding, not an assumption.
- Alpaca has **no historical options-quotes endpoint** (404), so this was only ever
  answerable live, inside a session. That is why the probe exists and why it is cron'd.
- Do not re-open the rolling-overnight-quote-subscription plan against this vendor. Any
  overnight aggressor read needs a *different* quote source, and that is a purchase
  decision gated on the no-paid-feeds-until-profitable rule.

## Nightly live-vs-batch audit alarm — ARMED 2026-08-09 (`tools/flow_pairing_audit.py`)

The corpus-firewall clause ("each night the REST join doubles as the audit") is now real,
plus the same-day check the Friday post-mortem asked for:

- **`--nightly`** — step 6 of `nightly_flow_capture.sh` (02:30 UTC cron): reconstructs each
  session's LIVE match rate from the collector's journald stats lines (per-epoch deltas —
  matched/resolved reset on every matcher reconnect; dbn/alp are process-cumulative,
  differenced per pid), compares against `join_ledger.json`'s batch rate, appends the row to
  `data/alpaca_conditions/pairing_audit.json`, and posts one ntfy alarm per breached session.
  The audit posts its own DEGRADED alarms and exits 0; a nonzero exit means the audit itself
  could not run and trips the wrapper's capture-failure alarm.
- **`--intraday`** — own cron 14:45 + 18:30 UTC Mon–Fri (both firings inside RTH in both DST
  regimes): trailing ~50 min of stats lines → alarms on wire-lag p50 median > 2s or the
  sidecar delivering < 50% of the spine's prints. Self-gates on tape activity (≥ 12 active
  minutes — no market calendar), dedupes one push per alarm kind per day. This is the
  "codes degraded" flag, delivered to the phone rather than a UI corner.
- **Thresholds, calibrated on measured ground truth** (constants + rationale at the top of the
  tool): live floor 90% (healthy 96.5–98.4% measured 08-04..06), batch floor 95% (ledger
  median 97.9%, p10 96.2%, degraded tail = 6/260 sessions below 95% — the Addendum-E 98%
  JOIN_FLOOR is a migration gate, 141/260 below it, useless for health), |gap| 5pts (healthy
  1–3pts, either direction alarmed: live≪batch = daemon drop, batch≪live = vendor tape hole),
  lag share 10% of active minutes at p50 > 2s (healthy < 1%, Fri 08-07 = 65%).
- **Validated on real history before arming**: Aug 4–6 audit silent; Fri 08-07 trips all four
  alarms — and the session-AGGREGATE live rate is **59.9%** (the 74.3% in the card notes was
  the best epoch; Friday is more label-blind than first recorded). Batch 47.8% < live 59.9%:
  Alpaca's HISTORICAL tape hole was worse than its live fanout. Intraday replay: Fri
  10:00–10:50 ET fires both alarms (lag p50 20.5s, sidecar at 42%) — the phone would have
  known by 10:45 ET; Thu windows and Fri afternoon correctly quiet.

Known cosmetic issue, deliberately not fixed here (collector untouched): the stats line's
enrich-bucket percentages divide by epoch-matched, so they print garbage (e.g. `633678400.0%`)
whenever the matcher epoch is fresh — the audit parser never reads them.

## Not in scope

- Corpus/rollup changes of any kind (v6 just landed; registered rules untouched).
- Code-family priors, win%, or any scored surface — gated on 1.8-on-truth.
- Options order execution paths — nothing here touches `trading/` or the chokepoint.
