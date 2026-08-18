//! Live Databento OPRA transport — the port of `signals/options_flow/dbn_stream.py`
//! (`DatabentoFlowStream` + `FeedLiveness`) on the official `databento` crate.
//!
//! Shape: a transport task owns the `LiveClient` (subscribe ALL_SYMBOLS tcbbo,
//! read, reconnect with backoff) and forwards decoded records over a channel; the
//! main loop owns the `Runner` (detectors + DB) and ticks it once a second with the
//! wall clock — the same split as Python (databento's client thread vs `sweep_loop`).
//! Liveness is judged on DATA, never on the socket's opinion of itself, exactly as
//! the Python module documents: SILENCE 90s / ACK 60s / STALL 300s.

use crate::calendar::is_market_open;
use crate::runner::{today_et, Runner};
use anyhow::{Context, Result};
use chrono::{TimeZone, Utc};
use chrono_tz::America::New_York;
use databento::dbn::{Cmbp1Msg, ErrorCode, ErrorMsg, SymbolMappingMsg, SystemCode, SystemMsg};
use databento::live::{SlowReaderBehavior, Subscription};
use databento::{LiveClient, Symbols};
use dbn::Schema;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::sync::mpsc;

pub const DATASET: &str = "OPRA.PILLAR";
pub const SILENCE_S: f64 = 90.0;
pub const SUBSCRIPTION_ACK_S: f64 = 60.0;
pub const PIPELINE_STALL_S: f64 = 300.0;
pub const RATE_LOG_S: f64 = 60.0;
const CHANNEL_CAP: usize = 1 << 18;

pub struct LiveArgs {
    pub key: String,
    /// Intraday replay start (epoch seconds). ⚠️ Measured 2026-08-17: the gateway
    /// replays ALL_SYMBOLS **interleaved per instrument, NOT in time order** (the
    /// 40th percentile record sat at 13:34, the 70th at 13:56, then back), so no
    /// clock can make it reproduce a session — it is a transport smoke test
    /// (auth, ack, mappings, decode, throughput), never a detector A/B. The file
    /// archive is the detector tape; `record` + `flow_rs replay` is the transport A/B.
    pub replay_from: Option<i64>,
    pub for_secs: Option<u64>,
    /// Tee every record the gateway sends (data, mappings, control) into a
    /// zstd DBN file — Databento's own format, so `flow_rs replay --tape <it>`
    /// against the archived session is the completeness proof.
    pub record: Option<std::path::PathBuf>,
    /// Condition-code pairing sidecar (Alpaca wildcard trades) — None = codes stay empty.
    pub sidecar: Option<crate::alpaca_ws::SidecarArgs>,
}

/// Raw-record tee: the session's records in DBN, as the gateway sent them.
struct Recorder {
    enc: dbn::encode::dbn::Encoder<zstd::stream::AutoFinishEncoder<'static, std::fs::File>>,
    last_flush: Instant,
    records: u64,
}

impl Recorder {
    fn open(path: &std::path::Path, metadata: &dbn::Metadata) -> Result<Self> {
        let f =
            std::fs::File::create(path).with_context(|| format!("create {}", path.display()))?;
        let enc = dbn::encode::dbn::Encoder::with_zstd(f, metadata)?;
        Ok(Self {
            enc,
            last_flush: Instant::now(),
            records: 0,
        })
    }
    fn write(&mut self, rec: dbn::RecordRef<'_>) {
        use dbn::encode::EncodeRecordRef;
        if let Err(e) = self.enc.encode_record_ref(rec) {
            eprintln!("dbn live: recorder write failed: {e}");
        }
        self.records += 1;
        if self.last_flush.elapsed() > Duration::from_secs(5) {
            use dbn::encode::EncodeRecord;
            let _ = self.enc.flush();
            self.last_flush = Instant::now();
        }
    }
}

/// What the transport hands the main loop. Prints carry exactly the fields
/// `_on_record` reads off a tcbbo record.
enum Msg {
    Print {
        instrument_id: u32,
        ts_event: u64,
        ts_recv: u64,
        price: i64,
        size: u32,
        publisher_id: u16,
        bid_px: i64,
        ask_px: i64,
    },
    Map {
        instrument_id: u32,
        raw_symbol: String,
    },
    Ack,
    SlowReader,
    SkippedByGateway,
    SymbolResolutionFailed(String),
    ReplayCompleted,
    Reconnected {
        gap_s: f64,
    },
    /// The transport gave up (replay sessions never reconnect; a production
    /// session that cannot re-authenticate). The main loop exits non-zero.
    Dead(String),
}

/// `FeedLiveness` — pure and clock-injected (Instant seconds since start).
pub struct FeedLiveness {
    last_msg_at: f64,
    last_print_at: f64,
    started_at: f64,
    msgs: u64,
    prints: u64,
    acked: bool,
    rate_at: f64,
    rate_prints: u64,
    pipeline_warned: bool,
}

pub struct FeedDead(pub String);

impl FeedLiveness {
    pub fn new(now: f64) -> Self {
        Self {
            last_msg_at: now,
            last_print_at: now,
            started_at: now,
            msgs: 0,
            prints: 0,
            acked: false,
            rate_at: now,
            rate_prints: 0,
            pipeline_warned: false,
        }
    }

    pub fn observe(&mut self, data_msgs: u64, prints: u64, acks: u64, now: f64) {
        if data_msgs > self.msgs {
            self.last_msg_at = now;
        }
        if prints > self.prints {
            self.last_print_at = now;
            self.pipeline_warned = false;
        }
        self.msgs = data_msgs;
        self.prints = prints;
        if acks > 0 {
            self.acked = true;
        }
    }

    pub fn idle(&mut self, now: f64) {
        self.last_msg_at = now;
        self.last_print_at = now;
        self.pipeline_warned = false;
    }

    /// Err(FeedDead) on a dead transport; Ok(Some(stalled_s)) once per pipeline
    /// stall (records but no prints); Ok(None) when healthy.
    pub fn check(
        &mut self,
        now: f64,
        market_open: bool,
    ) -> std::result::Result<Option<f64>, FeedDead> {
        if !self.acked && now - self.started_at > SUBSCRIPTION_ACK_S {
            return Err(FeedDead(format!(
                "the gateway never acked the subscription {:.0}s after subscribe (data_msgs={}). Measured latency for a good one is ~17ms at any hour, so this is a rejection, not a quiet market.",
                now - self.started_at,
                self.msgs
            )));
        }
        if !market_open {
            self.idle(now);
            return Ok(None);
        }
        let silent = now - self.last_msg_at;
        if silent > SILENCE_S {
            return Err(FeedDead(format!(
                "no records for {silent:.0}s during RTH (msgs={} prints={}). The transport is dead — a quiet market still delivers quotes.",
                self.msgs, self.prints
            )));
        }
        let stalled = now - self.last_print_at;
        if stalled > PIPELINE_STALL_S && !self.pipeline_warned {
            self.pipeline_warned = true;
            return Ok(Some(stalled));
        }
        Ok(None)
    }

    pub fn take_rate(&mut self, now: f64) -> Option<f64> {
        let elapsed = now - self.rate_at;
        if elapsed < RATE_LOG_S {
            return None;
        }
        let rate = (self.prints - self.rate_prints) as f64 * 60.0 / elapsed.max(1e-9);
        self.rate_at = now;
        self.rate_prints = self.prints;
        Some(rate)
    }
}

fn subscription(replay_from: Option<i64>) -> Subscription {
    let b = Subscription::builder()
        .symbols(Symbols::All)
        .schema(Schema::Tcbbo);
    match replay_from {
        Some(secs) => b
            .start(time::OffsetDateTime::from_unix_timestamp(secs).expect("valid start"))
            .build(),
        None => b.build(),
    }
}

async fn connect(key: &str, replay_from: Option<i64>) -> Result<(LiveClient, dbn::Metadata)> {
    // slow_reader_behavior is EXPLICIT (databento flipped the default warn→skip on
    // 2026-04-26 for stateless schemas): under "skip" the gateway drops trades to
    // catch us up and a directional measurement whose denominator shrinks under
    // load is worse than a late one.
    let mut client = LiveClient::builder()
        .key(key)?
        .dataset(DATASET)
        .slow_reader_behavior(SlowReaderBehavior::Warn)
        .build()
        .await
        .context("connect/authenticate to the live gateway")?;
    client
        .subscribe(subscription(replay_from))
        .await
        .context("subscribe")?;
    let metadata = client.start().await.context("start session")?;
    Ok((client, metadata))
}

/// The transport task: read records forever, forward them, reconnect on loss.
async fn transport(
    key: String,
    replay_from: Option<i64>,
    record: Option<std::path::PathBuf>,
    tx: mpsc::Sender<Msg>,
) {
    let (mut client, metadata) = match connect(&key, replay_from).await {
        Ok(c) => c,
        Err(e) => {
            let _ = tx.send(Msg::Dead(format!("{e:#}"))).await;
            return;
        }
    };
    eprintln!(
        "dbn live: OPRA live started — WHOLE MARKET (ALL_SYMBOLS), schema=tcbbo, session={}",
        client.session_id()
    );
    let mut recorder = match record.as_deref() {
        Some(p) => match Recorder::open(p, &metadata) {
            Ok(r) => {
                eprintln!("dbn live: recording every record to {}", p.display());
                Some(r)
            }
            Err(e) => {
                eprintln!("dbn live: recorder unavailable ({e:#}) — running without");
                None
            }
        },
        None => None,
    };
    loop {
        // Drain everything already buffered without touching the socket, then
        // read more. `try_next_record` is pure buffer work; `fill_buf` waits.
        let mut closed = false;
        let mut err: Option<String> = None;
        loop {
            match client.try_next_record() {
                Ok(Some(rec)) => {
                    if let Some(r) = recorder.as_mut() {
                        r.write(rec);
                    }
                    let msg = classify(rec);
                    if let Some(m) = msg {
                        if tx.send(m).await.is_err() {
                            return; // main loop gone
                        }
                    }
                }
                Ok(None) => break,
                Err(e) => {
                    err = Some(format!("decode: {e}"));
                    break;
                }
            }
        }
        if err.is_none() {
            match client.fill_buf().await {
                Ok(0) => closed = true,
                Ok(_) => continue,
                Err(e) => err = Some(format!("read: {e}")),
            }
        }
        // Session lost. Replay sessions NEVER reconnect (a reconnect re-subscribes
        // live and the run would silently measure the current tape); production
        // reconnects with backoff — to the SAME source, which is not the silent
        // downgrade the no-fallback rule forbids.
        let why = err.clone().unwrap_or_else(|| {
            if closed {
                "gateway closed the connection".into()
            } else {
                "unknown".into()
            }
        });
        if replay_from.is_some() {
            let _ = tx
                .send(Msg::Dead(format!("replay session ended: {why}")))
                .await;
            return;
        }
        let gap_start = Instant::now();
        eprintln!("dbn live: session lost ({why}) — reconnecting");
        let mut backoff = Duration::from_secs(1);
        loop {
            match client.reconnect().await {
                Ok(()) => match async {
                    client.subscribe(subscription(None)).await?;
                    client.start().await?;
                    Ok::<(), databento::Error>(())
                }
                .await
                {
                    Ok(()) => break,
                    Err(e) => eprintln!("dbn live: re-subscribe failed: {e}"),
                },
                Err(e) => eprintln!("dbn live: reconnect failed: {e}"),
            }
            tokio::time::sleep(backoff).await;
            backoff = (backoff * 2).min(Duration::from_secs(30));
            if gap_start.elapsed() > Duration::from_secs(600) {
                let _ = tx
                    .send(Msg::Dead(format!(
                        "could not reconnect for {:.0}s",
                        gap_start.elapsed().as_secs_f64()
                    )))
                    .await;
                return;
            }
        }
        if tx
            .send(Msg::Reconnected {
                gap_s: gap_start.elapsed().as_secs_f64(),
            })
            .await
            .is_err()
        {
            return;
        }
    }
}

/// One record → what the main loop needs to know (None = ignorable, e.g. heartbeat).
fn classify(rec: dbn::RecordRef<'_>) -> Option<Msg> {
    if let Some(m) = rec.get::<Cmbp1Msg>() {
        let lvl = &m.levels[0];
        return Some(Msg::Print {
            instrument_id: m.hd.instrument_id,
            ts_event: m.hd.ts_event,
            ts_recv: m.ts_recv,
            price: m.price,
            size: m.size,
            publisher_id: m.hd.publisher_id,
            bid_px: lvl.bid_px,
            ask_px: lvl.ask_px,
        });
    }
    if let Some(m) = rec.get::<SymbolMappingMsg>() {
        return m.stype_out_symbol().ok().map(|s| Msg::Map {
            instrument_id: m.hd.instrument_id,
            raw_symbol: s.to_string(),
        });
    }
    if let Some(m) = rec.get::<SystemMsg>() {
        return match m.code().ok()? {
            SystemCode::SubscriptionAck => Some(Msg::Ack),
            SystemCode::SlowReaderWarning => Some(Msg::SlowReader),
            SystemCode::ReplayCompleted => Some(Msg::ReplayCompleted),
            _ => None,
        };
    }
    if let Some(m) = rec.get::<ErrorMsg>() {
        return match m.code().ok()? {
            ErrorCode::SkippedRecordsAfterSlowReading => Some(Msg::SkippedByGateway),
            ErrorCode::SymbolResolutionFailed => Some(Msg::SymbolResolutionFailed(
                m.err().unwrap_or("").to_string(),
            )),
            other => {
                eprintln!(
                    "dbn live: gateway error {:?}: {}",
                    other,
                    m.err().unwrap_or("")
                );
                None
            }
        };
    }
    None
}

#[derive(Default)]
struct Counters {
    data_msgs: u64,
    acks: u64,
    slow: u64,
    skipped: u64,
    sym_fail: u64,
    reconnects: u64,
    gap_s: f64,
}

fn wall() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64()
}

/// The live loop; returns the process exit code (0 = clean stop, 1 = feed dead).
pub fn run(runner: &mut Runner, args: LiveArgs) -> Result<i32> {
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    rt.block_on(run_async(runner, args))
}

async fn run_async(runner: &mut Runner, args: LiveArgs) -> Result<i32> {
    let (tx, mut rx) = mpsc::channel::<Msg>(CHANNEL_CAP);
    let transport_task = tokio::spawn(transport(
        args.key.clone(),
        args.replay_from,
        args.record.clone(),
        tx,
    ));

    // Pairing sidecar: a task the spine never waits on; the runner tees every
    // print into the shared matcher and pulls resolved codes each cycle.
    let mut sidecar_task = None;
    if let Some(sc) = args.sidecar {
        let _ = rustls::crypto::ring::default_provider().install_default();
        let pairing = std::sync::Arc::new(std::sync::Mutex::new(crate::pairing::Pairing::new()));
        runner.pairing = Some(pairing.clone());
        sidecar_task = Some(tokio::spawn(crate::alpaca_ws::run(pairing, sc)));
    }

    let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let mut sigint = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())?;
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    tick.tick().await; // the immediate first tick

    let t0 = Instant::now();
    let deadline = args.for_secs.map(|s| t0 + Duration::from_secs(s));
    let mut live = FeedLiveness::new(0.0);
    let mut c = Counters::default();
    let mut exit_code = 0;
    let mut dead: Option<String> = None;

    'main: loop {
        tokio::select! {
            biased;
            _ = sigterm.recv() => { eprintln!("dbn live: SIGTERM — stopping"); break 'main; }
            _ = sigint.recv() => { eprintln!("dbn live: SIGINT — stopping"); break 'main; }
            _ = tick.tick() => {
                let now = wall();
                if let Err(e) = runner.cycle(now) {
                    // Python: "options_flow sweeps: cycle failed" — log and keep going.
                    eprintln!("dbn live: cycle failed: {e:#}");
                }
                let mono = t0.elapsed().as_secs_f64();
                live.observe(c.data_msgs, runner.stats.prints, c.acks, mono);
                let now_et = Utc.timestamp_opt(now.floor() as i64, 0).unwrap().with_timezone(&New_York);
                let open = is_market_open(&now_et);
                match live.check(mono, open) {
                    Err(FeedDead(why)) => {
                        eprintln!("dbn live: FEED DEAD — {why}");
                        exit_code = 1;
                        break 'main;
                    }
                    Ok(Some(stalled)) => eprintln!(
                        "dbn live: records flowing but NO prints for {stalled:.0}s during RTH (data_msgs={} unresolved_symbols={} skipped_no_meta={}). The socket is alive and the pipeline is not — check symbology and meta, not the connection.",
                        c.data_msgs, runner.stats.unresolved_symbols, runner.stats.skipped_no_meta
                    ),
                    Ok(None) => {}
                }
                if let Some(rate) = live.take_rate(mono) {
                    if open || args.replay_from.is_some() {
                        eprintln!(
                            "dbn live: {rate:.1} prints/min (data_msgs={} prints={} mapped={} acks={} slow={} dropped={} unresolved_syms={} no_meta={} reconnects={} gap_s={:.1} events={} minute_rows={} coded={} repairs={})",
                            c.data_msgs, runner.stats.prints, runner.mapped(), c.acks, c.slow, c.skipped, c.sym_fail,
                            runner.stats.skipped_no_meta, c.reconnects, c.gap_s, runner.stats.events_written, runner.stats.minute_rows_written,
                            runner.stats.prints_coded, runner.stats.repairs_applied
                        );
                    }
                }
                if let Some(d) = deadline {
                    if Instant::now() >= d { eprintln!("dbn live: --for-secs elapsed — stopping"); break 'main; }
                }
            }
            m = rx.recv() => {
                match m {
                    Some(Msg::Print { instrument_id, ts_event, ts_recv, price, size, publisher_id, bid_px, ask_px }) => {
                        c.data_msgs += 1;
                        if c.data_msgs == 1 {
                            eprintln!("dbn live: first data record ts_recv={} ({})", ts_recv, crate::pyfmt::iso_from_unix(ts_recv as f64 / 1e9));
                        }
                        let today = today_et(wall().floor() as i64);
                        runner.on_record(instrument_id, ts_event, ts_recv, price, size, publisher_id, bid_px, ask_px, today);
                    }
                    Some(Msg::Map { instrument_id, raw_symbol }) => runner.map_symbol(instrument_id, &raw_symbol),
                    Some(Msg::Ack) => c.acks += 1,
                    Some(Msg::SlowReader) => {
                        c.slow += 1;
                        eprintln!("dbn live: gateway says we are a SLOW READER ({} warnings) — records are still all being sent, but we are behind real time and the prints we classify are stale.", c.slow);
                    }
                    Some(Msg::SkippedByGateway) => {
                        c.skipped += 1;
                        eprintln!("dbn live: the GATEWAY DROPPED RECORDS to let us catch up ({} events). Prints are missing and cannot be recovered.", c.skipped);
                    }
                    Some(Msg::SymbolResolutionFailed(err)) => {
                        c.sym_fail += 1;
                        eprintln!("dbn live: symbol resolution FAILED ({}) — that name is not streaming and the session did not stop: {err}", c.sym_fail);
                    }
                    Some(Msg::ReplayCompleted) => {
                        eprintln!("dbn live: replay caught up with real time (data_msgs={} prints={}) — stopping", c.data_msgs, runner.stats.prints);
                        break 'main;
                    }
                    Some(Msg::Reconnected { gap_s }) => {
                        c.reconnects += 1;
                        c.gap_s += gap_s;
                        eprintln!("dbn live: RECONNECTED after a {gap_s:.1}s gap ({} this session, {:.1}s total). Subscriptions were replayed; the prints inside the gap are gone. A sweep cluster straddling it closes with legs missing.", c.reconnects, c.gap_s);
                    }
                    Some(Msg::Dead(why)) => { dead = Some(why); }
                    None => {
                        let why = dead.take().unwrap_or_else(|| "transport task exited".to_string());
                        eprintln!("dbn live: transport is DEAD — {why}; flow is DARK until restart");
                        exit_code = if args.replay_from.is_some() { 0 } else { 1 };
                        break 'main;
                    }
                }
            }
        }
    }
    transport_task.abort();
    if let Some(p) = &runner.pairing {
        p.lock().unwrap().stopping = true;
    }
    if let Some(t) = sidecar_task {
        // lets the sidecar flush the GTH tape + recorder (its sweep tick sees `stopping`)
        let _ = tokio::time::timeout(Duration::from_secs(3), t).await;
    }
    let (events, minutes) = runner.finish()?;
    eprintln!(
        "dbn live: stopped after {:.0}s — data_msgs={} prints={} mapped={} acks={} slow={} reconnects={} | events={:?} index_pressure_minutes={} (exit {})",
        t0.elapsed().as_secs_f64(), c.data_msgs, runner.stats.prints, runner.mapped(), c.acks, c.slow, c.reconnects, events, minutes, exit_code
    );
    Ok(exit_code)
}
