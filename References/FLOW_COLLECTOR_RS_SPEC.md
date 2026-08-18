# flow_rs — Rust OPRA flow collector: the scope

*Status 2026-08-18: **SCOPED, not started.** Successor to the equity port
(`References/EQUITY_COLLECTOR_RS_SPEC.md`, card #329) — same recipe: a tape on disk,
a deterministic replay, an A/B that prints IDENTICAL, then a one-process-for-one-process
systemd swap. Founder's Rust project — pair on it, don't dispatch it away.*

## What the process is today (measured 08-17)
`hmt-flow-collector` = `tools/flow_collector.py` → `signals/options_flow/runner.py`, four legs
in one asyncio process:

| leg | what | rate / cost |
|---|---|---|
| **spine** `dbn_stream.py` | Databento OPRA `tcbbo` live, `ALL_SYMBOLS` (whole US options tape; NBBO rides on the print) → symbology map → `normalize_print` → 1s drain into `sweep_loop` | 20–28k prints/min RTH (≈330–470/s; peak 6,642/s measured 03-20); `dropped=0 slow=0` all day |
| **sweep_loop** (`runner.py` ~350 lines) | per print: `tag_likely_spreads` → DIR-1 `classify_direction` + midpoint → `detect_block` (BLK-1) → `SweepBuffer` (SWP-1) + `RollingFlow` (bursts/clusters, 60s step) + `PressureGauge` (index windows, 10s flush) + print-lab JSONL; then enrich (Alpaca REST snapshots) → `insert_event` (+ rarity stamp) → `convergence.process_events` (broker + volume profiles + pushes) | 08-17: 8,197 sweeps · 1,743 blocks · 1,552 clusters · 427 bursts |
| **poll** `poll.py` (780 lines) | 30s Alpaca REST lane: contracts/OI seed, `unusual_volume`, `directional_pressure` | 486 + 34 events; pure REST |
| **pairing sidecar** `shadow_pairing.py` + `live_pairing.py` + `gth_tape.py` | Alpaca OPRA WS `trades:["*"]` (msgpack) → `StreamingCondMatcher` stamps condition codes onto the spine's prints in place before the drain (F-2 spread/hygiene/ISO gates read them); GTH index tape persist | ~95% of twins land inside the flush tick |

Process: RSS 370–420 MB (the 2.03M-entry symbology map ≈ 260 MB of Python dicts), 20 threads.
Startup blind window < 60s (mapping ingest is inside the first minute). CPU is small (single-digit
% of a core; the equity tape is ~50× the message rate).

**Side finding (independent of Rust):** every `tools/serv-deploy.sh` restart SIGKILLs this unit —
8× during RTH on 08-17 (`Main process exited, code=killed, status=9/KILL`). TERM does not exit
inside the script's 6s: the last log lines before each kill are the poll leg's httpx GETs, i.e.
`asyncio.run` is waiting in `shutdown_default_executor` for the `to_thread` REST poll to finish.
Each kill resets open sweep clusters / rolling buckets / gauge `seen` state mid-session. Cheap
Python fix (bound the poll thread at shutdown or `os._exit` after `runner.stop()`); a Rust binary
exits instantly by construction.

## The cut — Rust takes the spine + sweep_loop's pure detectors + pairing; Python keeps REST
**Rust (`collectors/flow_rs/`):**
- transport: the official `databento` Rust crate (`LiveClient`, tokio; DBN decoding is
  databento's own `dbn` crate) — heartbeats, `SubscriptionAck`, slow-reader / skipped-records
  errors, reconnect gap accounting, `FeedLiveness` (SILENCE 90s / ACK 60s / STALL 300s clocks).
- symbology: `instrument_id → raw_symbol` for ~2M contracts as `HashMap<u32, Box<str>>`
  (~80 MB, vs 260 MB); `occ_from_osi` (strip spaces), OCC parse (`^[A-Z]{1,6}\d{6}[CP]\d{8}$`),
  DTE, `normalize_print` (`ts_ms` = `ts_recv` → **CPython's** `datetime.isoformat` → parse
  round trip; keep the float path or prove ms-floor equality on the tape — parity trap #1).
- per-print detectors, ported exactly from Python: `tag_likely_spreads`, `classify_direction`,
  `classify_midpoint`, `is_hygiene_excluded`, `detect_block`, `SweepBuffer` (window/close rules),
  `RollingFlow` (bursts + clusters), `PressureGauge` (windows, half-day rule, `record_day`
  monotone merge), print-lab JSONL (window-gated), coverage stamps.
- writes: `options_flow_events` upsert (`db/options_flow.insert_event` SQL incl. `ingest_seq`),
  `flag_events_spread` (pairing late repairs), `index_pressure` minute upsert + day row.
  Schema stays Python-owned (never DDL from Rust). **`detail`, `rarity_p/n` and convergence are
  NOT written by Rust** — see the sidecar below.
- pairing sidecar: Alpaca options WS `trades:["*"]` msgpack (the equity_rs WS/msgpack code is
  reusable), `StreamingCondMatcher` port (the o-sign Amendment-1 tolerance windows), reset-on-
  reconnect epochs, GTH tape persist. Runs as a task the spine never waits on.
- stats line every 60s mirroring the Python one (prints/min, mapped, acks, slow, dropped…).

**Python (unchanged or new-small):**
- `poll.py` REST lane stays exactly as is (it is I/O + Alpaca rate limits, not bytes).
- **NEW back-half sidecar (~150 lines):** cursor on `options_flow_events.ingest_seq` → enrich
  `detail` (Alpaca snapshots + poll OI) → `flow_rarity.stamp_existing` → `convergence.
  process_events` → pushes. Today enrichment happens *before* insert; after the swap `detail`
  lands 1–2s after the row. Check every reader of `detail` tolerates NULL-then-filled (the UI
  poller and the daily report do; verify the fusion consumers).
- hot-lane merge / watchlist only feed the poll lane; the spine is whole-market and unfiltered.

## The harness (Python side, built FIRST — valuable on its own)
Unlike equity there is **no recorder to build: the tape already exists.**
`data/databento/opra_tcbbo/<date>.dbn.zst` (≈300 MB/day, whole market, pulled nightly ~02:30 UTC
by `tools/nightly_flow_capture.sh` → `tools/opra_tcbbo_backfill.py`, 275 sessions back to 2025-07-28) is the same `tcbbo` records the
live gateway sends, with the symbology in the file's metadata. Today the flow collector has NO
deterministic end-to-end replay against it — every detector change is untestable on the tape.
1. **Virtual clock through `sweep_loop`** (~10 wall-clock sites: `time.time()` for `close_ready` /
   rolling 60s / gauge 10s, `datetime.now(_ET)` for the day + gauge minutes + print-lab window,
   `is_trading_day(now)`, `stream.acked_market_wide`). Inject a `clock` callable; live = wall.
2. `tools/flow_collector_replay.py FILE [--start HH:MM --end HH:MM] --db X`: iterate DBN records
   in `ts_recv` order, resolve symbols from the file, call `DatabentoFlowStream._on_record`, tick
   the virtual clock from `ts_recv` (drain every 1s of tape time), scratch DB, poll/convergence/
   enrichment/broker/pushes OFF, pairing OFF (phase 1). Rust reads the same file
   (`dbn` crate) under the same clock rules — no replay *server* needed at all.
3. `tools/flow_ab.py A.db B.db`: diff `options_flow_events` by id (all content columns except
   `detail`/`rarity_*`/`ingest_seq`/`created_at`; JSON compared structurally), `index_pressure`
   minute rows, and the print-lab files. Exit 0 = IDENTICAL. Pin determinism first (two Python
   replays identical — expect to find nondeterminism: `time.time_ns()` print ids in pairing,
   rarity cache staleness, `datetime.now` day rollover).
4. **Pairing parity (phase 2):** tap the sidecar's Alpaca frames with `signals/equity_stream/
   recorder.RawFrameRecorder` (already generic bytes+ns) → a second tape; the replay merges both
   by arrival ns. Codes exist only live, so phase 1's A/B runs with pairing OFF on both sides.

## Stages + honest sizing
| stage | work | size |
|---|---|---|
| 0 | virtual clock + replay tool + A/B diff + determinism test (Python) | ~400 lines, 1 session |
| 1 | Rust spine + detectors + DB writes; A/B IDENTICAL on a full historical day, pairing off | ~2,000–2,500 lines, 2–3 sessions |
| 2 | pairing sidecar port + Alpaca-tape recorder + two-tape replay; parity on codes/repairs | ~800 lines, 1 session |
| 3 | Python back-half sidecar (enrich/rarity/convergence off rows) + systemd swap under the `OPTIONS_FLOW_EXTERNAL_COLLECTOR` interlock; poll lane keeps running in Python (own small unit or inside the sidecar) | ~250 lines, 1 session |

Equity was ~1,600 Rust lines in ~2 sessions; this is ~2× that. **What it buys, measured:** ~300 MB
RSS back, instant clean exit on deploy (no more mid-session SIGKILL), lower CPU on a base that is
already small, and a real per-print budget for future detectors (a 6.6k/s peak is nothing in
Rust). **What it does not buy:** correctness — the Python spine dropped nothing on 08-17 — and it
freezes the detector code into a second language. The flow science ledger says every thread but
o-sign Amendment 1 is CLOSED, so the detectors are as stable as they will get; if a new detector
program opens, build it on the tape (stage 0) first, port second.

## Ops invariants carried over
- ONE Databento live session; ONE Alpaca options WS per account — `flow_rs` runs **instead of**
  `hmt-flow-collector`, same interlock flag; never both.
- Never a metered Databento live *replay* (`start=`) for the A/B — the on-disk file is the tape.
- Rust writes rows only; `db/options_flow.init_db` owns DDL; `data/trading.db` WAL, busy 30s.
