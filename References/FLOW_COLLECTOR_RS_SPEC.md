# flow_rs — Rust OPRA flow collector: the scope

*Status 2026-08-18: **stages 0, 1 and 3 BUILT.** `collectors/flow_rs/` is IDENTICAL to the
Python collector on the full 2026-08-17 session (card #331; 13,109 events incl. rolling `detail`,
770 gauge minutes, 2.77M print-lab rows; Rust 24.5 s / 188 MB vs Python 240 s / 460 MB) and now
has the **live Databento transport** (`flow_rs live`: `databento` crate, liveness clocks,
reconnect, stats line, `--record` raw-tape tee) plus the Python back half
(`signals/options_flow/backhalf.py` sidecar; `tools/flow_collector.py` supervises the binary under
`OPTIONS_FLOW_RUST_SPINE=1` — no unit change). **LIVE IN PRODUCTION since 10:31 ET 08-18; stage 2
(pairing) LIVE since 11:40 ET 08-18:** `flow_rs live --pairing` runs the Alpaca wildcard-trades
sidecar in-process (`src/pairing.rs` = the `StreamingCondMatcher` port + the stamp/repair state,
`src/alpaca_ws.rs` = the WS client + `GthTape` + optional raw tee) — codes stamped onto the pending
batch and the sweep buffer at the top of each 1 s cycle, late MULTILEG codes → `spread_flag=1`
repairs, matcher reset per Alpaca reconnect, GTH prints → `data/gth_tape/`. First live minute:
92.3 % of prints matched (Python era: 87.1 %), 94.5 % of matches ≤100 ms, 91 % of prints coded
before the drain. Still owed from stage 2: the **two-tape replay** (Alpaca frames are recordable
via `--record-alpaca-dir`; the replay command does not consume them yet). Successor to the equity port
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

## The port (`collectors/flow_rs/`, ~1,500 lines — BUILT 08-18)
**Run the A/B:** `tools/flow_ab.sh 2026-08-17 [--start 09:30 --end 09:40]` (Rust `flow_rs replay`,
then Python, then `tools/flow_ab.py` → `IDENTICAL`/`DIFFERENT`; sandboxes under `/tmp/hmt_flow_ab/{rs,py}`).
**Build:** `cd collectors/flow_rs && cargo build --release` (`cargo test` = formatting/OCC/clock/
calendar unit tests; `cargo fmt` needs `rustup component add rustfmt`, user-local). **Scratch DB:**
`.venv/bin/python tools/flow_db_init.py <db>` (schema stays Python's).
**Live:** `flow_rs live --db data/trading.db [--record <file.dbn.zst>] [--replay-from <RFC3339>] [--for-secs N]`
(key from `$DATABENTO_API_KEY`); production runs it via `tools/flow_collector.py` (below).
Modules: `main.rs` (clap subcommands `replay` / `live`), `live.rs` (transport task on the
`databento` crate — ALL_SYMBOLS tcbbo, `SlowReaderBehavior::Warn`, `try_next_record`/`fill_buf`
loop, reconnect with 1→30 s backoff + gap accounting, re-subscribe + start; `FeedLiveness` port
SILENCE 90 s / ACK 60 s / STALL 300 s judged on data records; the 60 s stats line during RTH; SIGTERM
= flush + exit 0; feed dead = exit 1; `Recorder` = every gateway record teed into a zstd DBN file via
the `dbn` encoder), `calendar.rs` (`is_trading_day` / `is_early_close` / `is_market_open` — a port of
`trading/market_calendar.py`, diffed IDENTICAL against it day-for-day 2012–2030), `runner.rs` (`_on_record` →
`normalize_print`, one `sweep_loop` cycle per virtual second, print-lab writer, gauge flush),
`intraday.rs` (`tag_likely_spreads`, DIR-1, midpoint, `detect_block`, the `reason` formatting),
`sweeps.rs`, `rolling.rs`, `gauge.rs`, `occ.rs` (OSI→OCC, `parse_occ_symbol`, DTE, the
`ts_recv→ts_ms` round trip), `db.rs` (the exact `insert_event` / `upsert_minutes` / `record_day`
SQL), `pyfmt.rs` (CPython-identical `round`, `:.nf`, `:g`, `:,`, `isoformat`, **and `sum()`** — a
copy of equity_rs's plus the flow additions; fold into a shared crate when a third port appears).
Parity traps that actually bit (all fixed, all now pinned by the full-day A/B):
`sum()` in CPython ≥ 3.12 is **Neumaier-compensated** — a plain `+=` fold differed in the last
ulp on 22 of 872 sweep premiums (`py_sum` now used exactly where the Python calls `sum(...)`,
NOT for its manual `+=` loops); dict iteration order = insertion order everywhere floats are
summed (rolling buckets, small-print sides, sweep buckets are `Vec`s, never `HashMap`s); the
`ts_recv → ts_ms` path (`fromtimestamp(ns/1e9).isoformat()` → `fromisoformat().timestamp()*1000`
→ `int`) is reproduced step by step, not "ns // 1e6"; `f"{strike:g}"` for fractional strikes.
Half days and non-session dates come from `calendar.rs` on both sides (the batch drop for
non-trading days and the gauge guard are in `Runner::cycle`, as in `on_prints` / `sweep_loop`).

### The live half (stage 3 — BUILT 08-18, flip pending stage 2)
- **Supervisor, not a new unit.** `hmt-flow-collector.service` keeps `ExecStart=python
  tools/flow_collector.py`; with `OPTIONS_FLOW_RUST_SPINE=1` in `.env` that process spawns
  `collectors/flow_rs/target/release/flow_rs live --db data/trading.db` (path override
  `FLOW_RS_BIN`) and hosts the REST poll lane + the `BackHalf` sidecar beside it. First exit wins
  → whole process exits → systemd restarts both (Restart=always). SIGTERM → `proc.terminate()`
  → the Rust side flushes and exits within a second (measured). No sudo, no unit edit.
- **`signals/options_flow/backhalf.py`** — `ingest_seq` cursor over `options_flow_events`,
  sweep/block rows only: `enrich_sweep_events` (Alpaca snapshots + poll OI → `detail`) →
  `flow_rarity.stamp_event` → one UPDATE (`detail`, `otm_pct` COALESCE, `rarity_*` COALESCE) →
  `convergence.process_events` — the same three steps `sweep_loop` runs before its insert, now
  1–2 s after the row. Cursor starts at the newest row, except un-enriched sweep/block rows from
  the last 10 min (a restart gap) are picked up. Bursts/clusters untouched (Rust writes their
  `detail`). Checked 08-18: fusion intake reads neither `detail` nor `rarity_p` for options rows,
  so NULL-then-filled is invisible upstream. Tests: `tests/test_flow_backhalf.py`.
- **Shutdown fix (both modes):** `tools/flow_collector.py` no longer uses `asyncio.run()` — its
  teardown waited on the default executor for a `to_thread` REST poll and turned every deploy
  restart into a SIGKILL; `run_until_complete` + `os._exit` after `main()` returns.
- **⚠️ Measured 2026-08-17 (23:45 ET): Databento's intraday replay is NOT time-ordered.** With
  `subscribe(start=13:30Z)` for ALL_SYMBOLS the gateway streams the window **interleaved per
  instrument** — 400k records in: the 40th-percentile record sat at 13:34, the 50th at 13:33,
  the 70th at 13:56, back to 13:37 at 80% (2,266 backward steps in 400k, max ts 18:58Z). So no
  clock — wall or virtual — makes a gateway replay reproduce a session, and it is NOT the
  transport A/B the previous revision of this doc planned. What it is good for: a smoke test of
  auth / ack / mappings / decode / throughput (`--replay-from`, stops on the gateway's
  `ReplayCompleted`; the mapping count equalled the definition file exactly: 2,028,379; the Rust
  path ingested 207k rec/s end to end). Billing of those replay sessions was NOT verified —
  check the Databento usage page before running more.
- **The transport A/B that IS exact:** run `flow_rs live --record <file>` as a SHADOW (sandbox
  DB, real session, wall clock; `systemd-run --user --unit hmt-flow-rs-shadow …`, no sudo) and
  after the archive lands (~02:30 UTC next day) replay the recording and the archive through the
  same detectors: `flow_rs replay --date D --tape <recording> --defs opra_defs/D` vs `--tape
  opra_tcbbo/D` → `tools/flow_ab.py` IDENTICAL ⇒ the socket delivered every record Databento
  archived, symbology resolved the same, nothing dropped or reordered. First shadow: 2026-08-18
  session (`data/tmp_flow_shadow/`, gitignored).
- **Flip checklist (after stage 2):** build the binary on serv (`cargo build --release` — add to
  `tools/serv-deploy.sh` or a make target), `OPTIONS_FLOW_RUST_SPINE=1` in `.env`, restart the
  unit, watch the 60 s `dbn live:` stats line and the `options_flow backhalf:` line, confirm
  `detail`/`rarity_p` fill within seconds on new sweep rows. Rollback = drop the flag + restart.

### The harness (Python side — BUILT 08-18)
**Run the Python side alone:** `.venv/bin/python tools/flow_collector_replay.py 2026-08-17 --db /tmp/hmt_flow_ab/py/trading.db`
(`--start 09:30 --end 09:40` for a slice), then
`.venv/bin/python tools/flow_ab.py /tmp/hmt_flow_ab/py /tmp/hmt_flow_ab/rs` → `IDENTICAL`/`DIFFERENT`.
Measured: 09:30–09:40 slice = 634k records in 17s (~37k rec/s Python), full 08-17 session
(10.6M records → 13,109 events) = 4 min at ~44k rec/s, two runs IDENTICAL; determinism pinned by `tests/test_flow_tape_replay.py` (synthetic DBN
tape built with `databento_dbn` record constructors — no data/ dependency).

Unlike equity there is **no recorder to build: the tape already exists.**
`data/databento/opra_tcbbo/<date>.dbn.zst` (≈300 MB/day, whole market, pulled nightly ~02:30 UTC
by `tools/nightly_flow_capture.sh` → `tools/opra_tcbbo_backfill.py`, 275 sessions back to 2025-07-28) is the same `tcbbo` records the
live gateway sends, with the symbology in the file's metadata. Today the flow collector has NO
deterministic end-to-end replay against it — every detector change is untestable on the tape.
1. **Virtual clock through `sweep_loop`** — `OptionsFlowRunner(clock=, sleep=, enrich=)`:
   every "now" the consumer reads (`time.time()` for `close_ready` / rolling 60s / gauge 10s;
   `datetime.now(_ET)` for the day, gauge minutes, print-lab window, `meta_for` DTE,
   `on_prints`' trading-day gate) goes through `clock()`; the 1s cadence through `sleep()`;
   `enrich=False` = no broker (no Alpaca snapshots, no convergence). Live = wall clock, unchanged.
2. `tools/flow_collector_replay.py DATE --db X [--start HH:MM --end HH:MM]`: symbology from
   `data/databento/opra_defs/<date>.dbn.zst` (**the ALL_SYMBOLS tcbbo file carries NO
   mappings** — `metadata.mappings` is empty; the definition snapshot is the resolver, same as
   the corpus rollup), records straight into `DatabentoFlowStream._on_record` (a
   `SimpleNamespace(symbology_map=…)` stands in for the live client), the real `sweep_loop`
   driven by the clock rules below, sandbox = the `--db` directory (`TRADING_DB_PATH`, so events,
   `index_pressure_*` AND the print-lab JSONL land under `<dir>/research/print_lab/`, never in
   `data/`). OFF: poll lane, broker/enrichment (`detail` NULL), convergence/pushes, pairing
   sidecar (phase 2), rarity (no lookup table in the sandbox → NULL). Rust reads the same two
   files (`dbn` crate) under the same rules — no replay *server* at all.
   **★ Clock rules (the contract a port must follow to diff IDENTICAL):**
   * tape time `T` is whole seconds; `T0 = floor(first record's ts_recv / 1e9) + 1`;
   * each cycle: T += 1 (first cycle T = T0) → ingest records **in file order** while
     `ts_recv < T·1e9` (the file is NOT strictly ts_recv-ordered — 23k of 10.6M records on 08-17
     go backwards, up to 198s; a backwards record is ingested at once, a forward one waits for its
     second) → drain pending prints into `collected_prints` → one sweep cycle with every
     "now" == T (`int(T*1000)` for the ms clocks, `datetime.fromtimestamp(T, ET)` for the day);
   * end of tape: `END_GRACE_S = 120` more cycles with no new records (open clusters close,
     the last rolling check fires), then stop;
   * `--start/--end` clip by ts_recv time-of-day (ET) before any of the above.
   Live-vs-replay is NOT expected to match (live batches split at different instants and
   `tag_likely_spreads` is per-batch — 08-17 09:30–09:40: live 532 sweeps / 147 blocks, replay
   502 / 144, same first events); replay-vs-replay and Python-vs-Rust must.
3. `tools/flow_ab.py DIR_A DIR_B`: `options_flow_events` by id (all content columns except
   `detail`/`rarity_*`/`ingest_seq`/`created_at`; `exchanges` compared structurally),
   `index_pressure_minutes` (all but `updated_at`) + `index_pressure_days` (session_type,
   minutes_observed, syms, coverage_modes, finalized-or-not), print-lab JSONL (same files, same
   line count, each line structurally equal — Rust must emit the same rows, not the same bytes).
   Exit 0 = IDENTICAL. Two Python replays of 08-17 were IDENTICAL first try (872 events on the
   09:30–09:40 slice, 632,658 lab rows).
4. **Pairing parity (phase 2):** tap the sidecar's Alpaca frames with `signals/equity_stream/
   recorder.RawFrameRecorder` (already generic bytes+ns) → a second tape; the replay merges both
   by arrival ns. Codes exist only live, so phase 1's A/B runs with pairing OFF on both sides.

## Stages + honest sizing
| stage | work | size |
|---|---|---|
| 0 | virtual clock + replay tool + A/B diff + determinism test (Python) — **DONE 08-18** (~430 lines incl. tests) | ~400 lines, 1 session |
| 1 | Rust spine + detectors + DB writes; A/B IDENTICAL on a full historical day, pairing off — **DONE 08-18** (~1,500 lines, 1 session; the file replay needs no transport code) | ~2,000–2,500 lines, 2–3 sessions |
| 2 | pairing sidecar port + Alpaca-tape recorder + two-tape replay; parity on codes/repairs — **BUILT 08-18** (matcher + pairing state + Alpaca WS + GTH tape + raw tee ≈ 800 Rust lines incl. tests; live 11:40 ET). Two-tape replay NOT built (recording is; the replay merge is the remaining piece) | ~800 lines, 1 session |
| 3 | **live transport** in Rust (`databento` crate `LiveClient`, `FeedLiveness`, control-plane counters, reconnect, stats line, `--record` tee) + Python back-half sidecar (enrich/rarity/convergence off rows) + supervisor mode of `tools/flow_collector.py` under `OPTIONS_FLOW_RUST_SPINE=1` (same unit, same interlock); poll lane stays Python — **BUILT 08-18** (~560 Rust + ~330 Python lines incl. tests, 1 session). Transport A/B = shadow recording vs archive (above); production flip waits on stage 2 | ~600 Rust + ~250 Python lines, 1–2 sessions |

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
- **Two replays, two jobs.** The on-disk DBN file is the *iteration* tape (free, any day,
  deterministic — but it bypasses the gateway entirely). Databento's **live intraday replay**
  (`subscribe(start=<ts>)`, ≤~24h back) is a transport *smoke* test only — measured 08-17 it is
  **not time-ordered** (see "The live half"), so it can never be a detector A/B; the exact
  transport A/B is the shadow `--record` tape vs the archive. Billing of replay sessions
  unverified — check the usage page, never assume.
- Rust writes rows only; `db/options_flow.init_db` owns DDL; `data/trading.db` WAL, busy 30s.
