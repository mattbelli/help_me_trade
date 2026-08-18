//! equity_rs — the Rust equity tape collector for help_me_trade (v0 scaffold).
//!
//! What this does today: dial an Alpaca stocks-v2 (or replay-server) websocket,
//! run the msgpack handshake (hello → auth → subscribe trades[*]+quotes[*]),
//! decode every frame, and print throughput + delivery-lag stats. It also
//! understands the replay server's `{"T":"clock","ns":..}` frames — the
//! virtual clock every detector must run on under replay.
//!
//! What it does NOT do yet (the port, per References/EQUITY_COLLECTOR_RS_SPEC.md):
//! NBBO book, block/dark-pool + ISO-sweep + velocity detectors, spot cache,
//! SQLite writes, and the local proxy the API hub dials.
//!
//! Run against a replay:  cargo run --release -- --url ws://127.0.0.1:8770
//! Run live (one socket per account! stop hmt-equity-collector first):
//!   cargo run --release   # keys from APCA_API_KEY_ID / APCA_API_SECRET_KEY

use anyhow::{anyhow, bail, Context, Result};
use clap::Parser;
use futures_util::{SinkExt, StreamExt};
use rmpv::Value;
use std::collections::BTreeMap;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::Message;

const STOCKS_WS_URL: &str = "wss://stream.data.alpaca.markets/v2/sip";
const STATS_EVERY: Duration = Duration::from_secs(5);

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
}

/// Wall clock live; the replay server's clock frames under replay.
struct Clock {
    virtual_ms: Option<f64>,
}

impl Clock {
    fn now_ms(&self) -> f64 {
        match self.virtual_ms {
            Some(v) => v,
            None => SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_secs_f64() * 1000.0)
                .unwrap_or(0.0),
        }
    }
}

/// msgpack ext -1 (Timestamp) → epoch ms. Alpaca sends the 64-bit form
/// (34-bit seconds + 30-bit nanos) for stream timestamps; handle all three.
fn ts_to_ms(v: &Value) -> Option<f64> {
    match v {
        Value::Ext(-1, bytes) => match bytes.len() {
            4 => {
                let s = u32::from_be_bytes(bytes[..4].try_into().ok()?);
                Some(s as f64 * 1000.0)
            }
            8 => {
                let raw = u64::from_be_bytes(bytes[..8].try_into().ok()?);
                let nanos = (raw >> 34) as f64;
                let secs = (raw & 0x3_ffff_ffff) as f64;
                Some(secs * 1000.0 + nanos / 1e6)
            }
            12 => {
                let nanos = u32::from_be_bytes(bytes[..4].try_into().ok()?) as f64;
                let secs = i64::from_be_bytes(bytes[4..12].try_into().ok()?) as f64;
                Some(secs * 1000.0 + nanos / 1e6)
            }
            _ => None,
        },
        _ => None,
    }
}

fn field<'a>(m: &'a Value, key: &str) -> Option<&'a Value> {
    m.as_map()?
        .iter()
        .find(|(k, _)| k.as_str() == Some(key))
        .map(|(_, v)| v)
}

fn pack(v: &Value) -> Vec<u8> {
    let mut buf = Vec::new();
    rmpv::encode::write_value(&mut buf, v).expect("msgpack encode");
    buf
}

fn map(pairs: Vec<(&str, Value)>) -> Value {
    Value::Map(pairs.into_iter().map(|(k, v)| (Value::from(k), v)).collect())
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    let mut req = args.url.as_str().into_client_request()?;
    // Alpaca negotiates frame encoding off this header; without it frames are JSON.
    req.headers_mut()
        .insert("Content-Type", "application/msgpack".parse()?);
    let (ws, _) = tokio_tungstenite::connect_async(req)
        .await
        .with_context(|| format!("connect {}", args.url))?;
    let (mut tx, mut rx) = ws.split();

    // --- handshake: hello → auth → subscribe ---------------------------------------
    let hello = next_batch(&mut rx).await?;
    if !hello.iter().any(|m| field(m, "T").and_then(Value::as_str) == Some("success")) {
        bail!("unexpected hello: {hello:?}");
    }
    tx.send(Message::Binary(pack(&map(vec![
        ("action", "auth".into()),
        ("key", args.key.as_str().into()),
        ("secret", args.secret.as_str().into()),
    ]))))
    .await?;
    let auth = next_batch(&mut rx).await?;
    if !auth.iter().any(|m| field(m, "msg").and_then(Value::as_str) == Some("authenticated")) {
        bail!("auth failed: {auth:?}");
    }
    tx.send(Message::Binary(pack(&map(vec![
        ("action", "subscribe".into()),
        ("trades", Value::Array(vec!["*".into()])),
        ("quotes", Value::Array(vec!["*".into()])),
    ]))))
    .await?;
    eprintln!("connected {} — trades[*] quotes[*]", args.url);

    // --- hot loop --------------------------------------------------------------------
    let mut clock = Clock { virtual_ms: None };
    let mut counts: BTreeMap<String, u64> = BTreeMap::new();
    let mut lag_samples: Vec<f64> = Vec::with_capacity(8192);
    let mut frames: u64 = 0;
    let mut bytes: u64 = 0;
    let started = Instant::now();
    let mut last_stats = Instant::now();
    let mut last_total: u64 = 0;

    while let Some(msg) = rx.next().await {
        let raw = match msg? {
            Message::Binary(b) => b,
            Message::Close(_) => break,
            Message::Text(t) => t.into_bytes(), // replay/hub JSON — not expected here
            _ => continue,
        };
        frames += 1;
        bytes += raw.len() as u64;
        let batch = match rmpv::decode::read_value(&mut raw.as_slice()) {
            Ok(Value::Array(items)) => items,
            Ok(_) | Err(_) => {
                *counts.entry("undecodable".into()).or_default() += 1;
                continue;
            }
        };
        for m in &batch {
            let t = field(m, "T").and_then(Value::as_str).unwrap_or("?");
            *counts.entry(t.to_string()).or_default() += 1;
            match t {
                "clock" => {
                    if let Some(ns) = field(m, "ns").and_then(Value::as_i64) {
                        clock.virtual_ms = Some(ns as f64 / 1e6);
                        // TODO(port): run due flushes here (2s events, 30s spots).
                    }
                }
                "t" | "q" => {
                    // TODO(port): NBBO update / detectors go here.
                    if lag_samples.len() < 8192 && frames % 16 == 0 {
                        if let Some(ts) = field(m, "t").and_then(ts_to_ms) {
                            lag_samples.push(clock.now_ms() - ts);
                        }
                    }
                }
                "error" => eprintln!("vendor error frame: {m:?}"),
                "subscription" => eprintln!("subscription ack: {m:?}"),
                _ => {}
            }
        }
        if last_stats.elapsed() >= STATS_EVERY {
            let total: u64 = counts.values().sum();
            let secs = last_stats.elapsed().as_secs_f64();
            lag_samples.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let pct = |p: f64| -> f64 {
                if lag_samples.is_empty() {
                    f64::NAN
                } else {
                    lag_samples[((lag_samples.len() - 1) as f64 * p) as usize]
                }
            };
            eprintln!(
                "stats: msgs={} ({:.0}/s) t={} q={} frames={} MB={:.1} lag_p50_ms={:.0} lag_p95_ms={:.0} vclock={}",
                total,
                (total - last_total) as f64 / secs,
                counts.get("t").copied().unwrap_or(0),
                counts.get("q").copied().unwrap_or(0),
                frames,
                bytes as f64 / 1e6,
                pct(0.5),
                pct(0.95),
                clock.virtual_ms.map(|v| format!("{v:.0}")).unwrap_or_else(|| "wall".into()),
            );
            lag_samples.clear();
            last_total = total;
            last_stats = Instant::now();
        }
    }
    let total: u64 = counts.values().sum();
    let secs = started.elapsed().as_secs_f64();
    eprintln!(
        "done: msgs={} in {:.1}s ({:.0} msgs/s) by type {:?}",
        total,
        secs,
        total as f64 / secs.max(1e-9),
        counts
    );
    Ok(())
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
