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
//!
//! Stage A of the Alpaca spine: the same socket also carries up to 1,000 quote
//! subscriptions picked by `quotebook::QuoteBook` (re-ranked every 5 s; diffs go
//! out through a writer task so the read loop never waits on a send). Every
//! trade gets a shadow direction that rides the matcher to its dbn twin.
//!
//! Stage B (`flow_rs live --spine alpaca`): no dbn tape and no matcher — RTH
//! trades go straight to the Runner (`live::Msg::AlpacaTrades`, one send per
//! frame) carrying their native code and the pre-trade book; the quote book,
//! heat file, GTH tape and recorder run as before.

use crate::calendar::is_trading_day;
use crate::live::Msg;
use crate::pairing::Pairing;
use crate::print::Dir;
use crate::quotebook::{in_quote_hours, Quote, QuoteBook, QuoteHours, RERANK_EVERY_NS};
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
const HEAT_PERSIST_EVERY_SEC: u64 = 60;
// Alpaca spine: a socket can sit ESTAB with no data (feed-can-be-healthy-and-dead),
// and FeedLiveness only judges RTH — so ping, and reconnect when nothing (not
// even a pong) has come back, at any hour, before the open finds it dead.
const PING_EVERY_SEC: u64 = 20;
const DEAD_AFTER_SEC: u64 = 60;

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
    /// Quote-book seed/persist file (`data/flow_quote_heat.json`).
    pub heat_path: Option<PathBuf>,
    /// Stage-A shadow minute lines → `<dir>/<ET date>.jsonl` (`data/flow_alpaca_shadow`).
    pub shadow_dir: Option<PathBuf>,
    /// The Alpaca spine: RTH trades → the live loop's Runner; None = the dbn
    /// spine's pairing sidecar.
    pub spine: Option<tokio::sync::mpsc::Sender<Msg>>,
}

/// One Alpaca wildcard trade for the Runner (Alpaca spine, 09:30–16:15 ET only).
#[derive(Debug, Clone, PartialEq)]
pub struct AlpacaPrint {
    pub sym: String,
    pub t_ns: i64,
    pub price: f64,
    pub size: i64,
    pub exch: String,
    pub cond: String,
    /// The usable pre-trade book (latest quote with t < trade t, ≤ 5 s, 0 < bid < ask).
    pub nbbo: Option<Quote>,
    pub tick: Dir,
}

/// The quote book plus its timers and files, owned by the sidecar task (never
/// under the pairing lock, so a re-rank can't stall the spine's tee).
struct QuoteLane {
    book: QuoteBook,
    /// Alpaca spine: trades feed the Runner, not the matcher.
    spine: bool,
    hours: QuoteHours,
    heat_path: Option<PathBuf>,
    shadow_dir: Option<PathBuf>,
    last_rerank_ns: i64,
    last_persist: tokio::time::Instant,
}

impl QuoteLane {
    fn persist(&self) {
        if let Some(p) = &self.heat_path {
            if let Err(e) = self.book.save(p, now_ns()) {
                eprintln!("pairing: quote heat save to {} failed: {e}", p.display());
            }
        }
    }
}

type SubTx = tokio::sync::mpsc::UnboundedSender<Message>;

fn quotes_msg(action: &str, syms: Vec<String>) -> Message {
    Message::Binary(pack(&map(vec![("action", action.into()), ("quotes", Value::Array(syms.into_iter().map(Value::from).collect()))])))
}

struct AbortOnDrop(tokio::task::JoinHandle<()>);

impl Drop for AbortOnDrop {
    fn drop(&mut self) {
        self.0.abort();
    }
}

/// One wildcard trade: the quote book (heat, tick, pre-trade quote), then the
/// Runner batch (Alpaca spine, RTH only) or the matcher (dbn spine), and the
/// GTH tape (which keeps only the overnight session).
#[allow(clippy::too_many_arguments)]
fn on_trade(lane: &mut QuoteLane, p: &mut Pairing, gth: &mut Option<GthTape>, batch: &mut Vec<AlpacaPrint>, sym: &str, t: i64, px: f64, size: i64, cond: &str, exch: &str, now: i64) {
    let cents = (px * 100.0).round() as i64;
    let (shadow, book) = lane.book.on_trade(sym, t, cents, size);
    if !lane.spine {
        p.on_alpaca(sym, t, cents, size, cond, shadow, now);
    } else if lane.hours.contains(t) {
        batch.push(AlpacaPrint { sym: sym.to_string(), t_ns: t, price: px, size, exch: exch.to_string(), cond: cond.to_string(), nbbo: book, tick: shadow.tick });
    }
    if let Some(g) = gth.as_mut() {
        g.offer(sym, t, cents, size, cond, exch);
    }
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
    let mut lane = QuoteLane { book: QuoteBook::new(), spine: args.spine.is_some(), hours: QuoteHours::default(), heat_path: args.heat_path.clone(), shadow_dir: args.shadow_dir.clone(), last_rerank_ns: 0, last_persist: tokio::time::Instant::now() };
    if let Some(p) = &lane.heat_path {
        match lane.book.seed(p, now_ns()) {
            Ok((kept, expired)) => eprintln!("pairing: quote book seeded {kept} contracts from {} ({expired} expired dropped)", p.display()),
            Err(e) => eprintln!("pairing: quote book not seeded from {} ({e}) — opening with an empty book", p.display()),
        }
    }
    let mut backoff = RECONNECT_BASE_SEC;
    let mut connected_once = false;
    let mut sweep_tick = tokio::time::interval(Duration::from_secs(SWEEP_EVERY_SEC));
    sweep_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let mut last_stats = tokio::time::Instant::now();
    let mut lost_at = None;
    if lane.spine {
        eprintln!("alpaca spine: starting (wildcard trades → runner, quote book, GTH tape)");
    } else {
        eprintln!("pairing: starting (matcher + wildcard codes sidecar)");
    }
    loop {
        match session(&pairing, &args, &mut gth, recorder.as_mut(), &mut connected_once, &mut sweep_tick, &mut last_stats, &mut lane, &mut lost_at).await {
            Ok(()) => {}
            Err(e) => {
                {
                    let mut p = pairing.lock().unwrap();
                    if p.stopping {
                        break;
                    }
                    p.reconnects += 1;
                }
                lost_at.get_or_insert_with(tokio::time::Instant::now);
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
            housekeeping(&pairing, &mut gth, &mut last_stats, &mut lane, None);
        }
        backoff = (backoff * 2.0).min(RECONNECT_MAX_SEC);
    }
    if let Some(g) = gth.as_mut() {
        g.flush();
    }
    lane.persist();
    if let Some(r) = recorder.take() {
        let (f, d) = r.stop();
        eprintln!("pairing: Alpaca recorder frames={f} drops={d}");
    }
}

/// 1 s: matcher sweep, GTH flush, quote re-rank (RTH, every 5 s; the diff goes
/// out on `sub_tx` when connected — disconnected, the next session re-sends the
/// whole set), 60 s stats line + shadow minute line + heat persist.
fn housekeeping(pairing: &Arc<Mutex<Pairing>>, gth: &mut Option<GthTape>, last_stats: &mut tokio::time::Instant, lane: &mut QuoteLane, sub_tx: Option<&SubTx>) {
    let now = now_ns();
    if now - lane.last_rerank_ns >= RERANK_EVERY_NS && in_quote_hours(now) {
        lane.last_rerank_ns = now;
        let (unsub, sub) = lane.book.rerank(now);
        if let Some(tx) = sub_tx {
            // unsubscribe first: the server-side set never exceeds the cap
            if !unsub.is_empty() {
                let _ = tx.send(quotes_msg("unsubscribe", unsub));
            }
            if !sub.is_empty() {
                let _ = tx.send(quotes_msg("subscribe", sub));
            }
        }
    }
    let stats_due = last_stats.elapsed() >= Duration::from_secs(STATS_EVERY_SEC);
    if stats_due {
        *last_stats = tokio::time::Instant::now();
    }
    let book_json = lane.book.json();
    let (line, shadow_row) = {
        let mut p = pairing.lock().unwrap();
        p.sweep(now);
        if let Some(g) = gth.as_ref() {
            p.gth_stats = (g.written, g.dropped_non_session);
        }
        p.book_json = book_json.clone();
        if stats_due && lane.spine {
            let g = gth.as_ref().map(|g| (g.written, g.dropped_non_session)).unwrap_or((0, 0));
            let line = format!("alpaca spine: {} | gth_w={} gth_drop={} acked_wide={} reconnects={} rss={:.0}MB", lane.book.line(), g.0, g.1, p.acked_wide, p.reconnects, rss_mb());
            (Some(line), None)
        } else if stats_due {
            let g = gth.as_ref().map(|g| (g.written, g.dropped_non_session)).unwrap_or((0, 0));
            let line = format!("{} | {} | {}", p.stats_line(g, rss_mb()), p.shadow.line(), lane.book.line());
            (Some(line), Some((p.shadow.day.clone(), p.shadow.json())))
        } else {
            (None, None)
        }
    };
    if let Some(g) = gth.as_mut() {
        g.flush();
    }
    if let Some(l) = line {
        eprintln!("{l}");
    }
    if let (Some((day, shadow)), Some(dir)) = (shadow_row, &lane.shadow_dir) {
        if !day.is_empty() {
            let row = serde_json::json!({"ts": now / 1_000_000_000, "shadow": shadow, "book": book_json});
            let res = std::fs::create_dir_all(dir).and_then(|_| {
                let mut f = std::fs::OpenOptions::new().create(true).append(true).open(dir.join(format!("{day}.jsonl")))?;
                writeln!(f, "{row}")
            });
            if let Err(e) = res {
                eprintln!("pairing: shadow line to {} failed: {e}", dir.display());
            }
        }
    }
    if lane.last_persist.elapsed() >= Duration::from_secs(HEAT_PERSIST_EVERY_SEC) {
        lane.last_persist = tokio::time::Instant::now();
        lane.persist();
    }
}

#[allow(clippy::too_many_arguments)]
async fn session(
    pairing: &Arc<Mutex<Pairing>>,
    args: &SidecarArgs,
    gth: &mut Option<GthTape>,
    recorder: Option<&mut crate::record::Recorder>,
    connected_once: &mut bool,
    sweep_tick: &mut tokio::time::Interval,
    last_stats: &mut tokio::time::Instant,
    lane: &mut QuoteLane,
    lost_at: &mut Option<tokio::time::Instant>,
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
    // From here every send goes through the writer task: the read loop never
    // waits on the socket's send side.
    let (sub_tx, mut sub_rx) = tokio::sync::mpsc::unbounded_channel::<Message>();
    let _writer = AbortOnDrop(tokio::spawn(async move {
        while let Some(m) = sub_rx.recv().await {
            if let Err(e) = tx.send(m).await {
                eprintln!("pairing: sidecar send failed ({e}) — the read side will see the drop");
                break;
            }
        }
    }));
    let _ = sub_tx.send(Message::Binary(pack(&map(vec![("action", "subscribe".into()), ("trades", Value::Array(vec!["*".into()]))]))));
    let set = lane.book.set();
    eprintln!("pairing: subscribed trades=['*'] + {} quotes", set.len());
    if !set.is_empty() {
        let _ = sub_tx.send(quotes_msg("subscribe", set));
    }
    if let (Some(tx), Some(at)) = (&args.spine, lost_at.take()) {
        let _ = tx.send(Msg::Reconnected { gap_s: at.elapsed().as_secs_f64() }).await;
    }
    // The epoch boundary lives HERE, after re-subscribe: gap-fed dbn prints are
    // discarded rather than mis-ranked.
    if *connected_once && !lane.spine {
        let (d, a) = pairing.lock().unwrap().reset_matcher();
        let n = pairing.lock().unwrap().matcher_resets;
        eprintln!("pairing: MATCHER RESET #{n} after sidecar gap — new epoch; discarded pending d={d} a={a} (prints during the gap are unmeasured, never mismatched)");
    }
    *connected_once = true;
    let mut recorder = recorder;
    let mut acks = 0u64; // every quote diff is acked with the full list; log the first two
    let mut wide_acked = false; // the spine's liveness ack: once per session
    let mut ping = tokio::time::interval(Duration::from_secs(PING_EVERY_SEC));
    let mut last_rx = tokio::time::Instant::now();
    loop {
        tokio::select! {
            biased;
            _ = sweep_tick.tick() => {
                housekeeping(pairing, gth, last_stats, lane, Some(&sub_tx));
                if pairing.lock().unwrap().stopping { return Ok(()); }
            }
            _ = ping.tick(), if lane.spine => {
                if last_rx.elapsed() > Duration::from_secs(DEAD_AFTER_SEC) {
                    bail!("no frame and no pong for {:.0}s — the socket is open and dead", last_rx.elapsed().as_secs_f64());
                }
                let _ = sub_tx.send(Message::Ping(Vec::new()));
            }
            msg = rx.next() => {
                let Some(msg) = msg else { bail!("socket closed") };
                last_rx = tokio::time::Instant::now();
                let raw = match msg? {
                    Message::Binary(b) => b,
                    Message::Close(_) => bail!("server closed"),
                    _ => continue,
                };
                if let Some(r) = recorder.as_deref_mut() { r.record(&raw); }
                let now = now_ns();
                let items = frames(&raw);
                let mut batch = Vec::new();
                let mut first_ack = false;
                {
                    let mut p = pairing.lock().unwrap();
                    for item in &items {
                        match str_field(item, "T") {
                            Some("t") => {
                                let (Some(sym), Some(t)) = (str_field(item, "S"), field(item, "t").and_then(ts_ns)) else { continue };
                                let (Some(px), Some(sz)) = (field(item, "p").and_then(num_f64), field(item, "s").and_then(num_f64)) else { continue };
                                let (cond, exch) = (str_field(item, "c").unwrap_or(""), str_field(item, "x").unwrap_or(""));
                                on_trade(lane, &mut p, gth, &mut batch, sym, t, px, sz as i64, cond, exch, now);
                            }
                            Some("q") => {
                                let (Some(sym), Some(t)) = (str_field(item, "S"), field(item, "t").and_then(ts_ns)) else { continue };
                                let (Some(bid), Some(ask)) = (field(item, "bp").and_then(num_f64), field(item, "ap").and_then(num_f64)) else { continue };
                                lane.book.on_quote(sym, t, bid, ask);
                            }
                            Some("subscription") => {
                                let wide = matches!(field(item, "trades"), Some(Value::Array(a)) if a.iter().any(|s| s.as_str() == Some("*")));
                                p.acked_wide = wide;
                                let n_quotes = match field(item, "quotes") { Some(Value::Array(a)) => a.len(), _ => 0 };
                                lane.book.m.acked_quotes = Some(n_quotes);
                                acks += 1;
                                first_ack |= wide && !wide_acked;
                                wide_acked |= wide;
                                if acks <= 2 {
                                    eprintln!("pairing: subscription ack wide={wide} quotes={n_quotes}");
                                }
                            }
                            Some("error") => {
                                lane.book.m.server_errors += 1;
                                if lane.book.m.server_errors <= 20 {
                                    eprintln!("pairing: server error #{}: {item}", lane.book.m.server_errors);
                                }
                            }
                            _ => {}
                        }
                    }
                }
                if let Some(tx) = &args.spine {
                    // the live loop's backpressure, as the dbn transport has it
                    if first_ack && tx.send(Msg::Ack).await.is_err() {
                        return Ok(());
                    }
                    if !batch.is_empty() && tx.send(Msg::AlpacaTrades(batch)).await.is_err() {
                        return Ok(());
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
    fn spine_routes_rth_trades_to_the_runner_and_overnight_to_gth_only() {
        let dir = std::env::temp_dir().join(format!("gth_spine_test_{}", std::process::id()));
        let mut gth = Some(GthTape::new(dir.clone()).unwrap());
        let mut p = Pairing::new();
        let mut lane = QuoteLane { book: QuoteBook::new(), spine: true, hours: QuoteHours::default(), heat_path: None, shadow_dir: None, last_rerank_ns: 0, last_persist: tokio::time::Instant::now() };
        let et = |h, mi| New_York.with_ymd_and_hms(2026, 8, 18, h, mi, 0).unwrap().timestamp_nanos_opt().unwrap();
        let sym = "SPXW260818C06400000";
        let mut batch = Vec::new();
        // heat it past the candidate line, re-rank it into a slot, quote it
        lane.book.on_trade(sym, et(10, 0), 1_000, 100);
        lane.book.rerank(et(10, 0));
        lane.book.on_quote(sym, et(10, 1), 9.80, 10.00);
        on_trade(&mut lane, &mut p, &mut gth, &mut batch, sym, et(10, 1) + 5, 10.00, 3, "a", "C", 0);
        on_trade(&mut lane, &mut p, &mut gth, &mut batch, "SPY260918C00640000", et(10, 1) + 6, 1.25, 2, "", "X", 0);
        assert_eq!(batch.len(), 2);
        assert_eq!(batch[0].nbbo, Some(Quote { t_ns: et(10, 1), bid: 9.80, ask: 10.00 }));
        assert_eq!((batch[0].cond.as_str(), batch[0].exch.as_str(), batch[0].price, batch[0].size), ("a", "C", 10.00, 3));
        assert_eq!(batch[1].nbbo, None);
        // 16:15 and overnight: GTH tape only (it keeps the 03:00 print), never the runner
        on_trade(&mut lane, &mut p, &mut gth, &mut batch, sym, et(16, 15), 10.00, 1, "", "C", 0);
        on_trade(&mut lane, &mut p, &mut gth, &mut batch, sym, et(3, 0), 10.00, 1, "", "C", 0);
        assert_eq!(batch.len(), 2);
        assert_eq!(gth.as_ref().unwrap().buf.len(), 1);
        // the spine never feeds the matcher; the dbn spine's sidecar still does
        assert_eq!(p.alp_seen, 0);
        lane.spine = false;
        on_trade(&mut lane, &mut p, &mut gth, &mut batch, sym, et(11, 0), 10.00, 1, "", "C", 0);
        assert_eq!((p.alp_seen, batch.len()), (1, 2));
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
