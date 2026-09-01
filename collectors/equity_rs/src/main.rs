//! equity_rs — the Rust equity tape collector for help_me_trade.
//!
//! Owns the Alpaca stocks-v2 wildcard socket (every US trade + quote), runs the
//! tape detectors (blocks / dark pool, ISO sweeps, print velocity, spread
//! stats), persists `equity_tape_events` + `fusion_spot_cache` to trading.db,
//! and serves the local proxy the API hub dials. Contract + parity rules:
//! References/EQUITY_COLLECTOR_RS_SPEC.md. Runs INSTEAD of hmt-equity-collector
//! (one stocks socket per account) — never both.
//!
//! Live:    equity_rs --db data/trading.db            (keys: APCA_API_KEY_ID / _SECRET_KEY)
//! Replay:  equity_rs --replay --url ws://127.0.0.1:8770 --db /tmp/rs.db --proxy-port 8769

mod bars;
mod collector;
mod db;
mod detect;
mod msg;
mod proxy;
mod pyfmt;
mod record;
mod stats;

use anyhow::{anyhow, bail, Context, Result};
use clap::Parser;
use collector::Collector;
use futures_util::{SinkExt, StreamExt};
use msg::{map, pack, str_field};
use proxy::{ChanSyms, Proxy, CHANNELS, PER_SYMBOL_CHANNELS};
use rmpv::Value;
use std::collections::HashSet;
use std::path::PathBuf;
use std::time::{Duration, Instant};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::Message;

const STOCKS_WS_URL: &str = "wss://stream.data.alpaca.markets/v2/sip";
const RECONNECT_BASE_SEC: f64 = 1.0;
const RECONNECT_MAX_SEC: f64 = 30.0;
const STATS_INTERVAL_S: u64 = 60;
/// Liveness watchdog: quiet this long and we probe with a ping, ...
const PING_AFTER_IDLE_S: u64 = 30;
/// ... and if nothing at all comes back this long after it, the session is dead.
const PONG_TIMEOUT_S: u64 = 30;

#[derive(Parser, Debug)]
#[command(about = "help_me_trade equity tape collector (Rust)")]
struct Args {
    /// Websocket URL — Alpaca live, or a tools/equity_tape_replay.py server.
    #[arg(long, default_value = STOCKS_WS_URL)]
    url: String,
    #[arg(long, env = "APCA_API_KEY_ID", default_value = "replay", hide_env_values = true)]
    key: String,
    #[arg(long, env = "APCA_API_SECRET_KEY", default_value = "replay", hide_env_values = true)]
    secret: String,
    /// trading.db (schema owned by Python; tools/equity_db_init.py for a scratch DB)
    #[arg(long, default_value = "data/trading.db")]
    db: PathBuf,
    #[arg(long, default_value = "127.0.0.1")]
    proxy_host: String,
    #[arg(long, default_value_t = 8767)]
    proxy_port: u16,
    #[arg(long, default_value = "data/volume_profiles")]
    profile_dir: PathBuf,
    /// Our own store for the per-symbol 5-min bars (the abnormality sidecar's input).
    #[arg(long, default_value = "data/equity_bars_5min.db")]
    bars_db: PathBuf,
    /// UI stats snapshot dir (default: <db dir>/collector_stats) — equity.json every second.
    #[arg(long)]
    stats_dir: Option<PathBuf>,
    /// Tee every raw frame to <dir>/<ET date>.mpk.zst (live only; same file
    /// format as the Python recorder). Env EQUITY_TAPE_RECORD_DIR, like Python.
    #[arg(long, env = "EQUITY_TAPE_RECORD_DIR")]
    record_dir: Option<PathBuf>,
    /// Replay mode: virtual clock from {"T":"clock"} frames, no reconnect, no
    /// profile-age rule; exits at end of tape with a summary line.
    #[arg(long)]
    replay: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    // wss:// (Alpaca live) needs a process-level TLS crypto provider; without
    // this rustls 0.23 panics on the first TLS handshake (replay is plain ws://).
    let _ = rustls::crypto::ring::default_provider().install_default();
    let writer = db::Writer::open(&args.db, &args.bars_db)?;
    let (chan_tx, chan_rx) = mpsc::unbounded_channel::<ChanSyms>();
    let proxy = Proxy::new(chan_tx);
    let proxy_task = {
        let p = proxy.clone();
        let (h, port) = (args.proxy_host.clone(), args.proxy_port);
        tokio::spawn(async move {
            if let Err(e) = p.serve(&h, port).await {
                eprintln!("proxy died: {e:#}");
            }
        })
    };
    let mut col = Collector::new(args.replay, args.profile_dir.clone(), proxy.clone(), writer);
    if !args.replay {
        let dir = args.stats_dir.clone().unwrap_or_else(|| args.db.parent().map(|d| d.join("collector_stats")).unwrap_or_else(|| PathBuf::from("data/collector_stats")));
        match stats::SnapshotWriter::new(&dir, "equity") {
            Ok(w) => col.snapshot = Some(w),
            Err(e) => eprintln!("stats snapshot: {} unavailable ({e}) — running without", dir.display()),
        }
    }
    if let (Some(dir), false) = (&args.record_dir, args.replay) {
        col.recorder = Some(record::Recorder::start(dir.clone()).with_context(|| format!("recorder {}", dir.display()))?);
        eprintln!("equity tape recorder ON → {}", dir.display());
    }
    let started = Instant::now();

    let result = run(&args, &mut col, chan_rx).await;
    proxy_task.abort();

    // Drain the DB queue before reporting; the summary must reflect what's on disk.
    let db_errors = col.db.as_ref().map(|w| w.errors.clone());
    if let Some(w) = col.db.take() {
        w.shutdown();
    }
    if let Some(rec) = col.recorder.take() {
        let (frames, drops) = rec.stop();
        eprintln!("equity tape recorder: frames={frames} drops={drops}");
    }
    let total: u64 = col.msgs.values().sum();
    let secs = started.elapsed().as_secs_f64();
    eprintln!(
        "{}: msgs={} t={} q={} in {:.1}s ({:.0} msgs/s) | events={} sweeps={} velocity={} spots={} callback_errors={} db_errors={}",
        if args.replay { "replay finished" } else { "collector stopped" },
        total,
        col.msgs.get("t").copied().unwrap_or(0),
        col.msgs.get("q").copied().unwrap_or(0),
        secs,
        total as f64 / secs.max(1e-9),
        col.persisted,
        col.sweeps.emitted,
        col.velocity.emitted,
        col.spots_flushed,
        col.callback_errors,
        db_errors.map(|e| e.load(std::sync::atomic::Ordering::Relaxed)).unwrap_or(0),
    );
    result
}

async fn run(args: &Args, col: &mut Collector, mut chan_rx: mpsc::UnboundedReceiver<ChanSyms>) -> Result<()> {
    let mut wanted: [HashSet<String>; 5] = Default::default();
    let mut backoff = RECONNECT_BASE_SEC;
    let mut last_total: u64 = 0;
    let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    loop {
        match session(args, col, &mut chan_rx, &mut wanted, &mut last_total, &mut sigterm, &mut backoff).await {
            Ok(SessionEnd::Stop) => return Ok(()),
            Ok(SessionEnd::Closed) => {
                col.proxy.set_upstream_healthy(false);
                if args.replay {
                    return Ok(());
                }
                eprintln!("equity stream closed; reconnecting in {backoff:.0}s");
            }
            Err(e) => {
                col.proxy.set_upstream_healthy(false);
                if args.replay {
                    return Err(e);
                }
                eprintln!("equity stream error ({e:#}); reconnecting in {backoff:.0}s");
            }
        }
        col.reconnects += 1;
        tokio::select! {
            _ = tokio::time::sleep(Duration::from_secs_f64(backoff)) => {}
            _ = tokio::signal::ctrl_c() => return Ok(()),
            _ = sigterm.recv() => return Ok(()),
        }
        backoff = (backoff * 2.0).min(RECONNECT_MAX_SEC);
    }
}

enum SessionEnd {
    Stop,
    Closed,
}

async fn session(
    args: &Args,
    col: &mut Collector,
    chan_rx: &mut mpsc::UnboundedReceiver<ChanSyms>,
    wanted: &mut [HashSet<String>; 5],
    last_total: &mut u64,
    sigterm: &mut tokio::signal::unix::Signal,
    backoff: &mut f64,
) -> Result<SessionEnd> {
    let mut req = args.url.as_str().into_client_request()?;
    // Alpaca negotiates frame encoding off this header; without it frames are JSON.
    req.headers_mut().insert("Content-Type", "application/msgpack".parse()?);
    let (ws, _) = tokio_tungstenite::connect_async(req).await.with_context(|| format!("connect {}", args.url))?;
    let (mut tx, mut rx) = ws.split();

    // --- handshake: hello → auth → subscribe -----------------------------------------
    let hello = next_batch(&mut rx).await?;
    if !hello.iter().any(|m| str_field(m, "T") == Some("success")) {
        bail!("unexpected hello: {hello:?}");
    }
    tx.send(Message::Binary(pack(&map(vec![
        ("action", "auth".into()),
        ("key", args.key.as_str().into()),
        ("secret", args.secret.as_str().into()),
    ]))))
    .await?;
    let auth = next_batch(&mut rx).await?;
    if !auth.iter().any(|m| str_field(m, "T") == Some("success") && str_field(m, "msg") == Some("authenticated")) {
        bail!("auth failed: {auth:?}");
    }
    let mut sub: Vec<(&str, Value)> = vec![
        ("action", "subscribe".into()),
        ("trades", Value::Array(vec!["*".into()])),
        ("quotes", Value::Array(vec!["*".into()])),
    ];
    for ch in PER_SYMBOL_CHANNELS {
        if !wanted[ch].is_empty() {
            sub.push((CHANNELS[ch], sym_array(&wanted[ch])));
        }
    }
    tx.send(Message::Binary(pack(&map(sub)))).await?;
    let mut applied: [HashSet<String>; 5] = wanted.clone();
    eprintln!(
        "equity stream connected: trades[*] quotes[*] dailyBars={} bars={}",
        applied[proxy::CH_DAILY].len(),
        applied[proxy::CH_BARS].len()
    );
    col.proxy.set_upstream_healthy(true);
    // We are subscribed and streaming: the next failure starts its backoff from
    // scratch rather than inheriting the ladder this reconnect climbed.
    *backoff = RECONNECT_BASE_SEC;

    // --- loop ------------------------------------------------------------------------------
    let mut flush_tick = tokio::time::interval(Duration::from_millis(collector::FLUSH_INTERVAL_MS as u64));
    let mut spot_tick = tokio::time::interval(Duration::from_millis(collector::SPOT_FLUSH_INTERVAL_MS as u64));
    let mut stats_tick = tokio::time::interval(Duration::from_secs(STATS_INTERVAL_S));
    let mut bars_tick = tokio::time::interval(Duration::from_secs(1));
    // Upstream liveness. 2026-09-01: Alpaca stopped sending mid-session with the
    // socket still ESTAB — no Close frame, no error, no FIN — and `rx.next()`
    // simply never woke again, so the feed was dead for 6.5h (through the open)
    // with reconnects=0 and the proxy still advertising upstream_healthy. Nothing
    // recovers that by itself: our subscription lives in Alpaca's session state,
    // so a wedged session is a pipe to nobody and only a NEW one gets us back.
    // Hence an active probe. Any inbound frame counts as life — data, a pong, or
    // the server's own ping — so an honestly quiet tape (overnight, weekends)
    // never trips this; only silence that survives a ping does.
    // Measured 2026-09-01 against /v2/test: Alpaca answers a client ping in 15-21ms.
    let mut watchdog_tick = tokio::time::interval(Duration::from_secs(5));
    let mut last_rx = Instant::now();
    let mut ping_sent: Option<Instant> = None;
    flush_tick.tick().await;
    spot_tick.tick().await;
    stats_tick.tick().await;
    bars_tick.tick().await;
    watchdog_tick.tick().await;
    loop {
        tokio::select! {
            biased;
            _ = tokio::signal::ctrl_c() => return Ok(SessionEnd::Stop),
            _ = sigterm.recv() => return Ok(SessionEnd::Stop),
            Some(cs) = chan_rx.recv() => {
                wanted[cs.channel] = cs.symbols;
                // Diff-subscribe per-symbol channels upstream (EquityStream._apply_channel_diffs).
                let mut unsub: Vec<(&str, Value)> = vec![("action", "unsubscribe".into())];
                let mut subs: Vec<(&str, Value)> = vec![("action", "subscribe".into())];
                for ch in PER_SYMBOL_CHANNELS {
                    let gone: HashSet<String> = applied[ch].difference(&wanted[ch]).cloned().collect();
                    let new: HashSet<String> = wanted[ch].difference(&applied[ch]).cloned().collect();
                    if !gone.is_empty() { unsub.push((CHANNELS[ch], sym_array(&gone))); }
                    if !new.is_empty() { subs.push((CHANNELS[ch], sym_array(&new))); }
                    applied[ch] = wanted[ch].clone();
                }
                if unsub.len() > 1 { tx.send(Message::Binary(pack(&map(unsub)))).await?; }
                if subs.len() > 1 { tx.send(Message::Binary(pack(&map(subs)))).await?; }
            }
            msg = rx.next() => {
                let Some(msg) = msg else { return Ok(SessionEnd::Closed) };
                last_rx = Instant::now();
                ping_sent = None;
                match msg? {
                    Message::Binary(b) => col.on_frame(&b),
                    Message::Text(t) => col.on_frame(t.as_bytes()), // never expected (msgpack negotiated)
                    Message::Close(_) => return Ok(SessionEnd::Closed),
                    _ => {}
                }
            }
            _ = flush_tick.tick(), if !args.replay => {
                let now = col.now_ms();
                col.flush_events(now);
            }
            _ = spot_tick.tick(), if !args.replay => col.flush_spots(),
            _ = bars_tick.tick(), if !args.replay => {
                let now = col.now_ms();
                col.flush_bars(now);
                col.write_snapshot();
            }
            _ = stats_tick.tick() => {
                eprintln!("{}", col.stats_line(STATS_INTERVAL_S as f64, last_total));
            }
            _ = watchdog_tick.tick(), if !args.replay => {
                match ping_sent {
                    Some(sent) if sent.elapsed() >= Duration::from_secs(PONG_TIMEOUT_S) => {
                        col.stalls += 1;
                        eprintln!(
                            "equity stream STALLED: silent for {:.0}s, no pong {:.0}s after ping \u{2014} dropping the dead session and reconnecting (the tape has a gap from here)",
                            last_rx.elapsed().as_secs_f64(),
                            sent.elapsed().as_secs_f64(),
                        );
                        return Ok(SessionEnd::Closed);
                    }
                    None if last_rx.elapsed() >= Duration::from_secs(PING_AFTER_IDLE_S) => {
                        tx.send(Message::Ping(Vec::new())).await?;
                        ping_sent = Some(Instant::now());
                    }
                    _ => {}
                }
            }
        }
    }
}

fn sym_array(set: &HashSet<String>) -> Value {
    let mut v: Vec<&String> = set.iter().collect();
    v.sort();
    Value::Array(v.into_iter().map(|s| Value::from(s.as_str())).collect())
}

async fn next_batch<S>(rx: &mut S) -> Result<Vec<Value>>
where
    S: StreamExt<Item = std::result::Result<Message, tokio_tungstenite::tungstenite::Error>> + Unpin,
{
    let msg = rx.next().await.ok_or_else(|| anyhow!("socket closed during handshake"))??;
    let raw = match msg {
        Message::Binary(b) => b,
        other => bail!("expected binary handshake frame, got {other:?}"),
    };
    match rmpv::decode::read_value(&mut raw.as_slice())? {
        Value::Array(items) => Ok(items),
        v => Ok(vec![v]),
    }
}
