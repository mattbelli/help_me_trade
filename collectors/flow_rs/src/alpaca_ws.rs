//! The wildcard-codes sidecar — Alpaca OPRA WS `trades:["*"]` (msgpack) feeding
//! the pairing matcher, plus the GTH tape tee (`signals/options_flow/
//! gth_tape.py`) and an optional raw-frame recorder. Port of the IO half of
//! `shadow_pairing.py::_alpaca_loop` and `gth_tape.GthTape`. Runs as a tokio
//! task the spine never waits on; a dead sidecar logs and stays dead.
//!
//! Epoch rule (review BLOCKER 2 in the Python module): every reconnect is a gap
//! in the Alpaca tape while the DBN tee kept feeding, so the matcher is RESET
//! after each re-subscribe (counted, logged loudly) — mixed-epoch numbers would
//! silently stop meaning "matched".

use crate::calendar::is_trading_day;
use crate::pairing::Pairing;
use anyhow::{anyhow, bail, Result};
use chrono::{Datelike, Duration as CDuration, NaiveTime, TimeZone};
use chrono_tz::America::New_York;
use futures_util::{SinkExt, StreamExt};
use rmpv::Value;
use std::collections::HashMap;
use std::io::Write;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::Message;

pub const OPRA_WS_URL: &str = "wss://stream.data.alpaca.markets/v1beta1/opra";
const RECONNECT_BASE_SEC: f64 = 5.0;
const RECONNECT_MAX_SEC: f64 = 120.0;
const SWEEP_EVERY_SEC: u64 = 1;
const STATS_EVERY_SEC: u64 = 60;

pub fn now_ns() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos() as i64).unwrap_or(0)
}

// --- GTH tape --------------------------------------------------------------------------------

const GTH_MORNING_END: (u32, u32) = (9, 25);
const GTH_EVENING_START: (u32, u32) = (20, 0);
const GTH_BUFFER_MAX: usize = 50_000;

/// Overnight (Cboe GTH) index-option prints → `data/gth_tape/<morning session>.jsonl`,
/// rows `{"t","s","p","z","c","x"}` — Databento OPRA has no overnight session, so
/// this lane is the only one that sees them. In-session prints reject on one
/// integer compare against a cached [rth_lo, rth_hi) window.
pub struct GthTape {
    dir: PathBuf,
    buf: Vec<(String, String)>,
    rth_lo: i64,
    rth_hi: i64,
    trading_day_memo: Option<(chrono::NaiveDate, bool)>,
    pub written: u64,
    pub dropped_non_session: u64,
    pub dropped_overflow: u64,
    pub errors: u64,
}

impl GthTape {
    pub fn new(dir: PathBuf) -> std::io::Result<Self> {
        std::fs::create_dir_all(&dir)?;
        Ok(Self { dir, buf: Vec::new(), rth_lo: 0, rth_hi: 0, trading_day_memo: None, written: 0, dropped_non_session: 0, dropped_overflow: 0, errors: 0 })
    }

    pub fn offer(&mut self, sym: &str, t_ns: i64, cents: i64, size: i64, cond: &str, exch: &str) -> bool {
        if self.rth_lo <= t_ns && t_ns < self.rth_hi {
            return false;
        }
        let dt = New_York.timestamp_nanos(t_ns);
        let tod = dt.time();
        let morning_end = NaiveTime::from_hms_opt(GTH_MORNING_END.0, GTH_MORNING_END.1, 0).unwrap();
        let evening_start = NaiveTime::from_hms_opt(GTH_EVENING_START.0, GTH_EVENING_START.1, 0).unwrap();
        if morning_end <= tod && tod < evening_start {
            let day = dt.date_naive();
            if let (Some(lo), Some(hi)) = (
                New_York.from_local_datetime(&day.and_time(morning_end)).single(),
                New_York.from_local_datetime(&day.and_time(evening_start)).single(),
            ) {
                self.rth_lo = lo.timestamp_nanos_opt().unwrap_or(0);
                self.rth_hi = hi.timestamp_nanos_opt().unwrap_or(0);
            }
            return false;
        }
        let session = if tod < morning_end { dt.date_naive() } else { dt.date_naive() + CDuration::days(1) };
        let trading = match self.trading_day_memo {
            Some((d, ok)) if d == session => ok,
            _ => {
                let ok = is_trading_day(session);
                self.trading_day_memo = Some((session, ok));
                ok
            }
        };
        if !trading {
            self.dropped_non_session += 1;
            return false;
        }
        let line = format!(
            "{{\"t\":{t_ns},\"s\":{},\"p\":{cents},\"z\":{size},\"c\":{},\"x\":{}}}",
            serde_json::to_string(sym).unwrap_or_default(),
            serde_json::to_string(cond).unwrap_or_default(),
            serde_json::to_string(exch).unwrap_or_default(),
        );
        if self.buf.len() >= GTH_BUFFER_MAX {
            self.buf.remove(0);
            self.dropped_overflow += 1;
        }
        self.buf.push((format!("{:04}-{:02}-{:02}", session.year(), session.month(), session.day()), line));
        true
    }

    pub fn flush(&mut self) {
        if self.buf.is_empty() {
            return;
        }
        let buf = std::mem::take(&mut self.buf);
        let mut by_session: HashMap<String, Vec<String>> = HashMap::new();
        for (s, l) in &buf {
            by_session.entry(s.clone()).or_default().push(l.clone());
        }
        for (session, lines) in by_session {
            let path = self.dir.join(format!("{session}.jsonl"));
            let res = std::fs::OpenOptions::new().create(true).append(true).open(&path).and_then(|mut f| {
                let mut body = lines.join("\n");
                body.push('\n');
                f.write_all(body.as_bytes())
            });
            if let Err(e) = res {
                self.errors += 1;
                if self.errors == 1 {
                    eprintln!("gth tape: flush to {} failed: {e} — prints lost, counted", path.display());
                }
                continue;
            }
            self.written += lines.len() as u64;
        }
    }
}

// --- the sidecar task -------------------------------------------------------------------------

pub struct SidecarArgs {
    pub key: String,
    pub secret: String,
    pub url: String,
    pub gth_dir: Option<PathBuf>,
    /// Raw-frame tee, same format as the equity recorder (msgpack [recv_ns, frame] in zstd).
    pub record_dir: Option<PathBuf>,
}

fn frames(raw: &[u8]) -> Vec<Value> {
    match rmpv::decode::read_value(&mut &raw[..]) {
        Ok(Value::Array(items)) => items,
        Ok(v @ Value::Map(_)) => vec![v],
        _ => vec![],
    }
}

fn field<'a>(m: &'a Value, k: &str) -> Option<&'a Value> {
    if let Value::Map(kv) = m {
        kv.iter().find(|(key, _)| key.as_str() == Some(k)).map(|(_, v)| v)
    } else {
        None
    }
}

fn str_field<'a>(m: &'a Value, k: &str) -> Option<&'a str> {
    field(m, k).and_then(|v| v.as_str())
}

/// Alpaca msgpack timestamps: ext type -1 (msgpack Timestamp: 32/64/96-bit) → epoch ns.
fn ts_ns(v: &Value) -> Option<i64> {
    match v {
        Value::Ext(-1, data) => match data.len() {
            4 => Some(u32::from_be_bytes(data[..4].try_into().ok()?) as i64 * 1_000_000_000),
            8 => {
                let x = u64::from_be_bytes(data[..8].try_into().ok()?);
                let nanos = (x >> 34) as i64;
                let secs = (x & 0x3_ffff_ffff) as i64;
                Some(secs * 1_000_000_000 + nanos)
            }
            12 => {
                let nanos = u32::from_be_bytes(data[..4].try_into().ok()?) as i64;
                let secs = i64::from_be_bytes(data[4..12].try_into().ok()?);
                Some(secs * 1_000_000_000 + nanos)
            }
            _ => None,
        },
        Value::Integer(i) => i.as_i64(),
        _ => None,
    }
}

fn num_f64(v: &Value) -> Option<f64> {
    match v {
        Value::F64(f) => Some(*f),
        Value::F32(f) => Some(*f as f64),
        Value::Integer(i) => i.as_f64(),
        _ => None,
    }
}

fn pack(v: &Value) -> Vec<u8> {
    let mut buf = Vec::new();
    let _ = rmpv::encode::write_value(&mut buf, v);
    buf
}

fn map(kv: Vec<(&str, Value)>) -> Value {
    Value::Map(kv.into_iter().map(|(k, v)| (Value::from(k), v)).collect())
}

pub async fn run(pairing: Arc<Mutex<Pairing>>, args: SidecarArgs) {
    let mut gth = match &args.gth_dir {
        Some(d) => match GthTape::new(d.clone()) {
            Ok(t) => Some(t),
            Err(e) => {
                eprintln!("gth tape: {} unavailable ({e}) — running without", d.display());
                None
            }
        },
        None => None,
    };
    let mut recorder = args.record_dir.as_ref().and_then(|d| match crate::record::Recorder::start(d.clone()) {
        Ok(r) => {
            eprintln!("pairing: recording Alpaca frames → {}", d.display());
            Some(r)
        }
        Err(e) => {
            eprintln!("pairing: recorder {} unavailable ({e}) — running without", d.display());
            None
        }
    });
    let mut backoff = RECONNECT_BASE_SEC;
    let mut connected_once = false;
    let mut sweep_tick = tokio::time::interval(Duration::from_secs(SWEEP_EVERY_SEC));
    sweep_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let mut last_stats = tokio::time::Instant::now();
    eprintln!("pairing: starting (matcher + wildcard codes sidecar)");
    loop {
        match session(&pairing, &args, &mut gth, recorder.as_mut(), &mut connected_once, &mut sweep_tick, &mut last_stats).await {
            Ok(()) => {}
            Err(e) => {
                {
                    let mut p = pairing.lock().unwrap();
                    if p.stopping {
                        break;
                    }
                    p.reconnects += 1;
                }
                eprintln!("pairing: sidecar stream error ({e:#}); reconnect in {backoff:.0}s — a 406 means another process holds the OPRA socket");
            }
        }
        if pairing.lock().unwrap().stopping {
            break;
        }
        // keep sweeping while disconnected — the DBN tee is still feeding
        let until = tokio::time::Instant::now() + Duration::from_secs_f64(backoff);
        while tokio::time::Instant::now() < until {
            sweep_tick.tick().await;
            housekeeping(&pairing, &mut gth, &mut last_stats);
        }
        backoff = (backoff * 2.0).min(RECONNECT_MAX_SEC);
    }
    if let Some(g) = gth.as_mut() {
        g.flush();
    }
    if let Some(r) = recorder.take() {
        let (f, d) = r.stop();
        eprintln!("pairing: Alpaca recorder frames={f} drops={d}");
    }
}

fn housekeeping(pairing: &Arc<Mutex<Pairing>>, gth: &mut Option<GthTape>, last_stats: &mut tokio::time::Instant) {
    let now = now_ns();
    let stats_due = last_stats.elapsed() >= Duration::from_secs(STATS_EVERY_SEC);
    if stats_due {
        *last_stats = tokio::time::Instant::now();
    }
    let line = {
        let mut p = pairing.lock().unwrap();
        p.sweep(now);
        if stats_due {
            let g = gth.as_ref().map(|g| (g.written, g.dropped_non_session)).unwrap_or((0, 0));
            Some(p.stats_line(g, rss_mb()))
        } else {
            None
        }
    };
    if let Some(g) = gth.as_mut() {
        g.flush();
    }
    if let Some(l) = line {
        eprintln!("{l}");
    }
}

async fn session(
    pairing: &Arc<Mutex<Pairing>>,
    args: &SidecarArgs,
    gth: &mut Option<GthTape>,
    recorder: Option<&mut crate::record::Recorder>,
    connected_once: &mut bool,
    sweep_tick: &mut tokio::time::Interval,
    last_stats: &mut tokio::time::Instant,
) -> Result<()> {
    let mut req = args.url.as_str().into_client_request()?;
    req.headers_mut().insert("Content-Type", "application/msgpack".parse()?);
    let (ws, _) = tokio_tungstenite::connect_async(req).await?;
    let (mut tx, mut rx) = ws.split();
    // handshake: connected → auth → authenticated
    let deadline = tokio::time::Instant::now() + Duration::from_secs(12);
    let mut sent = false;
    'hs: loop {
        let msg = tokio::time::timeout_at(deadline, rx.next()).await.map_err(|_| anyhow!("no authenticated ack"))?;
        let Some(msg) = msg else { bail!("socket closed during handshake") };
        let raw = match msg? {
            Message::Binary(b) => b,
            Message::Text(t) => t.into_bytes(),
            _ => continue,
        };
        for item in frames(&raw) {
            match (str_field(&item, "T"), str_field(&item, "msg").unwrap_or("")) {
                (Some("error"), m) => bail!("code={:?} {m}", field(&item, "code")),
                (Some("success"), "authenticated") => break 'hs,
                (Some("success"), "connected") if !sent => {
                    tx.send(Message::Binary(pack(&map(vec![
                        ("action", "auth".into()),
                        ("key", args.key.as_str().into()),
                        ("secret", args.secret.as_str().into()),
                    ]))))
                    .await?;
                    sent = true;
                }
                _ => {}
            }
        }
    }
    tx.send(Message::Binary(pack(&map(vec![("action", "subscribe".into()), ("trades", Value::Array(vec!["*".into()]))])))).await?;
    eprintln!("pairing: subscribed trades=['*']");
    // The epoch boundary lives HERE, after re-subscribe: gap-fed dbn prints are
    // discarded rather than mis-ranked.
    if *connected_once {
        let (d, a) = pairing.lock().unwrap().reset_matcher();
        let n = pairing.lock().unwrap().matcher_resets;
        eprintln!("pairing: MATCHER RESET #{n} after sidecar gap — new epoch; discarded pending d={d} a={a} (prints during the gap are unmeasured, never mismatched)");
    }
    *connected_once = true;
    let mut recorder = recorder;
    loop {
        tokio::select! {
            biased;
            _ = sweep_tick.tick() => {
                housekeeping(pairing, gth, last_stats);
                if pairing.lock().unwrap().stopping { return Ok(()); }
            }
            msg = rx.next() => {
                let Some(msg) = msg else { bail!("socket closed") };
                let raw = match msg? {
                    Message::Binary(b) => b,
                    Message::Close(_) => bail!("server closed"),
                    _ => continue,
                };
                if let Some(r) = recorder.as_deref_mut() { r.record(&raw); }
                let now = now_ns();
                let items = frames(&raw);
                let mut p = pairing.lock().unwrap();
                for item in &items {
                    match str_field(item, "T") {
                        Some("t") => {
                            let (Some(sym), Some(t)) = (str_field(item, "S"), field(item, "t").and_then(ts_ns)) else { continue };
                            let (Some(px), Some(sz)) = (field(item, "p").and_then(num_f64), field(item, "s").and_then(num_f64)) else { continue };
                            let cents = (px * 100.0).round() as i64;
                            let size = sz as i64;
                            let cond = str_field(item, "c").unwrap_or("");
                            p.on_alpaca(sym, t, cents, size, cond, now);
                            if let Some(g) = gth.as_mut() {
                                g.offer(sym, t, cents, size, cond, str_field(item, "x").unwrap_or(""));
                            }
                        }
                        Some("subscription") => {
                            let wide = matches!(field(item, "trades"), Some(Value::Array(a)) if a.iter().any(|s| s.as_str() == Some("*")));
                            p.acked_wide = wide;
                            eprintln!("pairing: subscription ack wide={wide}");
                        }
                        Some("error") => eprintln!("pairing: server error: {item}"),
                        _ => {}
                    }
                }
            }
        }
    }
}

fn rss_mb() -> f64 {
    std::fs::read_to_string("/proc/self/statm")
        .ok()
        .and_then(|s| s.split_whitespace().nth(1).and_then(|p| p.parse::<f64>().ok()))
        .map(|pages| pages * 4096.0 / 1e6)
        .unwrap_or(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gth_window_and_session_keying() {
        let dir = std::env::temp_dir().join(format!("gth_test_{}", std::process::id()));
        let mut g = GthTape::new(dir.clone()).unwrap();
        let et = |y, mo, d, h, mi| New_York.with_ymd_and_hms(y, mo, d, h, mi, 0).unwrap().timestamp_nanos_opt().unwrap();
        assert!(!g.offer("SPXW260818C06400000", et(2026, 8, 18, 12, 0), 100, 1, "", "C")); // RTH → reject + cache window
        assert!(!g.offer("SPXW260818C06400000", et(2026, 8, 18, 15, 0), 100, 1, "", "C")); // fast path
        assert!(g.offer("SPXW260818C06400000", et(2026, 8, 17, 22, 0), 100, 1, "a", "C")); // Mon evening → Tue file
        assert!(g.offer("SPXW260818C06400000", et(2026, 8, 18, 3, 0), 100, 1, "", "C")); // Tue 03:00 → Tue file
        assert!(!g.offer("SPXW260818C06400000", et(2026, 8, 22, 22, 0), 100, 1, "", "C")); // Sat night → Sun (not trading)
        assert_eq!(g.dropped_non_session, 1);
        g.flush();
        let body = std::fs::read_to_string(dir.join("2026-08-18.jsonl")).unwrap();
        assert_eq!(body.lines().count(), 2);
        assert!(body.starts_with("{\"t\":"), "{body}");
        assert!(body.contains("\"c\":\"a\""));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn msgpack_timestamp_ext_decodes() {
        // 64-bit form: nanos<<34 | secs
        let secs: u64 = 1_755_500_000;
        let nanos: u64 = 123_456_789;
        let x = (nanos << 34) | secs;
        let v = Value::Ext(-1, x.to_be_bytes().to_vec());
        assert_eq!(ts_ns(&v), Some(secs as i64 * 1_000_000_000 + nanos as i64));
        let v = Value::Ext(-1, (secs as u32).to_be_bytes().to_vec());
        assert_eq!(ts_ns(&v), Some(secs as i64 * 1_000_000_000));
    }
}
