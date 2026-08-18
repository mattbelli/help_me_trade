# equity_rs — Rust equity tape collector: the contract

*Status 2026-08-18: **PORT BUILT** (`collectors/equity_rs/`, ~1,600 lines) — full front half
(decode → NBBO → blocks/dark pool + ISO sweeps + velocity + spread stats → events + spots →
proxy) and **IDENTICAL** to the Python collector on every synthetic A/B (all four event types,
36k-case CPython formatting parity). Open: the A/B on the first REAL recorded session (08-18),
then the systemd swap. Founder's Rust side project — pair on it, don't dispatch it away.*

**Run the A/B:** `tools/equity_ab.sh data/equity_tape_raw/<date>.mpk.zst [--start 09:30 --end 09:40]`
(replay server + both collectors + diff; scratch DBs in `/tmp/hmt_equity_ab`).
**Build:** `cd collectors/equity_rs && cargo build --release` (`cargo test` = formatting unit tests;
`.venv/bin/python tools/equity_rs_fmt_cases.py | cargo run -q --release --example fmtcheck` = 36k
CPython parity cases). Modules: `main.rs` (args, live/replay loops, reconnect, diff-subscribe),
`collector.rs` (hot path, clocks, flush deadlines), `detect.rs` (the four detectors + Event),
`db.rs` (writer thread, exact SQL), `proxy.rs` (hub proxy), `msg.rs` (msgpack fields, timestamps,
JSON), `pyfmt.rs` (CPython-identical rounding/commas/repr/isoformat/floordiv).

## Why / scope
The Python equity collector (`signals/equity_stream/`, ~1,400 lines) owns the Alpaca stocks-v2
wildcard socket — every US trade + quote (~17k msgs/s midday, 117k msgs/s at the close). It is
the highest-volume byte-crunching process on serv and fully self-contained (perception only;
never touches `trading/`). That makes it the right first Rust target: measurable, fenced, and
swappable one process for one process.

**Rust takes the front half. Python keeps the back half.**
- **Rust (this port):** socket + msgpack decode → NBBO book → detectors (block/dark-pool, ISO
  sweep, print velocity, spread stats) → `equity_tape_events` rows + `fusion_spot_cache` rows
  in `data/trading.db` (WAL) → the local **proxy** the API hub dials (`ws://127.0.0.1:8767`).
- **Python (unchanged for now):** the abnormality engine (`signals/abnormality/`, numpy
  baselines + REST priming) and the convergence watch (`options_flow.convergence.process_events`,
  needs a broker + pushes). Both can later be fed from rows (`ingest_seq` cursor on
  `equity_tape_events`; a per-minute OHLCV table for abnormality) by a small Python sidecar —
  NOT part of the A/B v1.

Definition of done: `tools/equity_tape_ab.py py.db rs.db` prints **IDENTICAL** on a full
recorded session, and the proxy serves the API hub the same frames. Then swap the systemd unit.

## The harness (built)
| piece | what |
|---|---|
| `EQUITY_TAPE_RECORD_DIR=<dir>` on the live collector | taps every raw frame → `<dir>/<ET date>.mpk.zst` (zstd stream of msgpack `[recv_ns, frame_bytes]`; appends across reconnects). ~70 B/msg raw, ~4–6× zstd ⇒ a full session ≈ 6–9 GB. Turn off / prune once a few sessions are captured. |
| `tools/equity_tape_replay.py FILE --speed {0,1,N} [--start HH:MM --end HH:MM]` | serves the file over the **exact Alpaca protocol** (msgpack hello → auth → subscription ack → frames byte-for-byte). Adds `[{"T":"clock","ns":recv_ns}]` frames every `--clock-every-ms` (100) of recorded time + one at `last+60s`, then closes. Each client gets its own replay. |
| `tools/equity_collector_replay.py --db X` | the Python collector on the replay server: virtual clock, scratch DB, no convergence/abnormality/broker, proxy on :8768. Prints msgs/s + counts at EOF. |
| `tools/equity_tape_ab.py A.db B.db` | diffs `equity_tape_events` (by id, all content columns; JSON compared structurally) + `fusion_spot_cache` (symbol → price, ts). Exit 0 = IDENTICAL. |
| Rust: `equity_rs --replay --url ws://127.0.0.1:8770 --db X --proxy-port 8769` | the port; `--db` needs the Python schema (`tools/equity_db_init.py X` for a scratch DB). |

Determinism is pinned by `tests/test_equity_tape_replay.py` (two Python replays of one tape ⇒
identical DBs). The Rust collector inherits that only if it follows the clock rules below.

## Protocol (what the socket speaks)
- Connect with header `Content-Type: application/msgpack` (else Alpaca sends JSON text frames).
- Server hello `[{"T":"success","msg":"connected"}]` → send `{"action":"auth","key","secret"}` →
  expect `[{"T":"success","msg":"authenticated"}]` → send
  `{"action":"subscribe","trades":["*"],"quotes":["*"], "dailyBars":[...],"bars":[...],"updatedBars":[...]}`
  (per-symbol channels = union of what proxy clients asked for; diff-subscribe on change).
- Every frame is a msgpack **array** of maps. `T` ∈ `t` trade, `q` quote, `b` minute bar,
  `u` updated bar, `d`/`db` daily bar, `subscription`, `error`, `success`, and (replay only)
  `clock` `{"ns": int}`. Timestamps `t` are msgpack ext −1 (Timestamp: 32/64/96-bit forms).
- Trade fields used: `S` symbol, `p` price, `s` size, `x` exchange, `c` conditions (list of
  1-char strings), `i` trade id, `t` ts. Quote fields used: `S`, `bp`, `ap`, `t`.
- Live: reconnect with backoff 1s→30s; a callback error must never tear the socket down.
  Replay: EOF ends the run.

## Clocks (the parity-critical part)
`now_ms` = wall clock live; **the last clock frame's `ns/1e6` under replay**. Every use of
"now" below is `now_ms` — never the OS clock in replay:
1. Quote/trade `ts_ms` = message `t` → epoch ms; if missing, `now_ms`.
2. `side_vs_nbbo` freshness (`NBBO_FRESH_MS`=2000) is judged with the trade's `ts_ms`.
3. Block/dark-pool cooldown (60s per (symbol, kind)) uses `now_ms/1000` (Python: a seconds clock).
4. Sweep/velocity `flush(now_ms)` and the spot flush run from **deadlines**: on the first clock
   frame set `next_flush = v+2000`, `next_spot = v+30000`; on any later clock frame `v`,
   if `v ≥ next_flush` → run the events flush at `now=v` then `next_flush = v+2000`; same for
   spots at 30s. Live: 2s / 30s loops on the wall clock. (`runner._on_clock`.)
5. Events flush = drain block/dark-pool pending (max 256 queue) → `sweeps.flush(now)` →
   `velocity.flush(now)` → any sweep events closed inline by a later print; insert in that
   order (`ingest_seq` is assigned in insert order — not compared by the A/B, but keep it).

## Detectors — port these exactly (source of truth = the Python)
- **NBBO book** (`runner._on_quote`): per symbol `(bid, ask, ts_ms)`; skip if `bp`/`ap`
  missing/unparseable or `S` empty. Also feeds `SpreadStats` (1-in-16 sampling, 64-sample
  window; **stats only, no events**) and the proxy (`quotes` channel).
- **Trade path** (`runner._on_trade`), in this order:
  1. skip if no `S` or `p` is None; `size = int(s or 0)`; `conditions = c or []`.
  2. **before** the exclude filter, if `size>0 && price>0`: `TapeDetector.on_print(...,
     prev_price=last_admitted_price[symbol])` (blocks/dark pool — `signals/equity_tape.py`:
     needs prev price; rejects prints >10% off prev; notional ≥ $1M AND ≥ floor where floor =
     `max($1M, median_daily_shares × price × 1e-4)` from `data/volume_profiles/<SYM>.json`
     `median_daily_total` (live: ignore files older than 7 days; **replay: no age rule**), or
     $10M with no profile; kind = `dark_pool` if `x=="D"` else `equity_block`; 60s cooldown per
     (symbol, kind); direction = tick test vs prev price; id `eq_{date}_{SYM}_{dp|blk}_{tradeid}`).
     Then if `"F" ∈ conditions` → `SweepAggregator.on_iso_print` (window 1000ms, ≥$500k gross,
     ≥3 venues, 30s cooldown keyed on window start; direction = |buy−sell| ≥ 50% of classified;
     id `eqsw_{date}_{SYM}_{start_ms}`); a print that closes the previous window returns that
     event → append to pending.
  3. `excluded = any(c in TRADE_EXCLUDE_CONDITIONS)` — set in `brokers/market_stream.py`
     (`4 7 9 B C G H M N P Q R T U V W Z`). If NOT excluded and `size>0 && price>0`: direction
     via `side_vs_nbbo(price, nbbo, ts_ms)`; signed notional (+buy/−sell/0); `VelocityDetector.
     on_print` (10s buckets, warmup 15, spike ≥10× EMA(α=0.05) and ≥30 prints, cooldown 300s,
     EMA contribution capped at 3× baseline, gap decay ≤60 buckets; id `eqvel_{date}_{SYM}_{bucket_start_ms}`);
     `last_admitted_price[symbol] = price`; `spot_dirty[symbol] = (price, iso_ts)`.
  4. proxy publish (`trades` channel) — the JSON copy with `t` as RFC3339.
- **Event row shape**: see the dict literals in `detectors.py` / `equity_tape.py` — `reason`,
  `caveat`, `detail` are compared verbatim by the A/B (float formatting must match Python's
  `round(x, 4)` / `f"{x:,.1f}"` / `f"{x:,.2f}"` output).
- **Spot cache**: `fusion_spot_cache(symbol PK, price TEXT, ts TEXT, updated_at)`; price is
  `str(float)` — Python repr (`100.1`, `50.0`), ts = the print's `t` as RFC3339 with `+00:00`.

## DB writes
`data/trading.db`, WAL, `busy_timeout` 30s. `equity_tape_events` upsert on `id`
(`db/equity_tape.py::insert_event`: `BEGIN IMMEDIATE`; new id ⇒ `ingest_seq` from
`bus_ingest_sequences('equity_tape')`; `ts`/`created_at` keep first-detection values;
`conditions`/`detail` stored as JSON text). Schema is **owned by Python** — Rust writes rows,
never DDL (run `db.equity_tape.init_db` once from Python if the tables are missing).

## Proxy (what the API hub dials)
Loopback `ws://127.0.0.1:8767`, JSON text frames, Alpaca-shaped: hello `[{"T":"success","msg":"connected"}]`;
`auth` → `authenticated` (accept anything); `subscribe`/`unsubscribe` with channel lists
(`trades quotes dailyBars bars updatedBars`) → ack `{"T":"subscription", <each channel: sorted list>}`.
Per-client queue (max 2000, drop newest, count drops); sender batches ≤128 messages per frame.
Per-symbol channels' union → upstream diff-subscribe. **While upstream is down, refuse new
clients with `[{"T":"error","code":503,"msg":"upstream not connected"}]` and drop existing
ones** — the hub must see the outage. (`signals/equity_stream/proxy.py`.)

## Ops
- One stocks socket per account (Alpaca 406s the second): the Rust collector runs **instead of**
  `hmt-equity-collector`, behind the same `EQUITY_TAPE_EXTERNAL_COLLECTOR=1` interlock. Never both.
  **LIVE since 2026-08-18 10:33 ET:** `EQUITY_RUST_COLLECTOR=1` in `.env` makes `tools/equity_collector.py`
  supervise the binary as a child (creds via env; no unit edit, no sudo) and run the Python sidecar
  (`signals/equity_stream/sidecar.py`) beside it: convergence off `equity_tape_events` rows (ingest_seq
  cursor), abnormality off the 5-min bars the binary writes. Rollback = flag `0` + restart.
- **5-min bars (`src/bars.rs`) — Rust owns `data/equity_bars_5min.db`** (`--bars-db`): the hot half of the
  abnormality detector (prints → 1-min → 5-min per symbol, buckets 0–77 from 09:30 ET). At every 5-min
  boundary +2 s it folds the in-progress minutes (fold-then-reset — Python's detector double-counted a
  folded minute) and upserts the completed buckets (`bucket < current`, incl. re-emitted stragglers), then
  advances `equity_bars_5min_flush.through_bucket` — the sidecar waits on that marker, loads the session
  grid, merges the REST prime under it (buckets before a mid-session start), primes the detector and runs the
  same boundary. Verified 08-18 vs an independent recount from the tape (SPY bucket 0 h/l/c/v exact).
  Rows kept 30 days; the whole universe's intraday 5-min bars are queryable there (`db/equity_bars_5min.py`).
- **Raw-frame tee (`src/record.rs`)**: `EQUITY_TAPE_RECORD_DIR` (or `--record-dir`) writes the same
  `<ET date>.mpk.zst` the Python recorder did (append across restarts, drop-and-count on overflow), so
  `tools/equity_ab.sh` replays a Rust-recorded session unchanged. ~10–15 GB per full session — unset the env
  when tapes aren't wanted. `tools/equity_ab.sh` passes a scratch `--bars-db` (never the production store).
- Systemd unit mirrors the Python one (`WorkingDirectory` = repo, `Restart=always`).
- Toolchain: rustup in `~/.cargo` (user-local, no sudo); `cargo build --release` in
  `collectors/equity_rs/`. `target/` is gitignored.
- Stats line every 60s like the Python one (msgs/s by type, lag p50/p95, events, proxy clients/drops).
- **UI snapshot (`src/stats.rs`)**: `data/collector_stats/equity.json` every second (`--stats-dir`) — rate,
  latency p50/p90/p95/p99 now + ET day (log-spaced histogram), per-minute series, counters. Served by
  `GET /api/collectors/stats`; the toolbar SIP chip's dropdown renders it (`feed-stats-panel.tsx`).
