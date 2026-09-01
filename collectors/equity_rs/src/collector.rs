//! The runner: wildcard stream → NBBO → detectors → DB + proxy. Port of
//! `signals/equity_stream/runner.py` (hot path + flush semantics + replay clock).

use crate::bars::BarGrid;
use crate::db::{Cmd, Writer};
use crate::detect::{
    is_excluded_condition, side_vs_nbbo, Event, Nbbo, SpreadStats, SweepAggregator, TapeDetector,
    VelocityDetector,
};
use crate::msg::{as_f64, as_i64, field, str_field, to_json, ts_to_iso, ts_to_ms};
use crate::proxy::{Proxy, CH_BARS, CH_DAILY, CH_QUOTES, CH_TRADES, CH_UPDATED};
use crate::pyfmt::py_repr;
use crate::record::Recorder;
use crate::stats::{LagHist, MinuteSeries, SnapshotWriter};
use rmpv::Value;
use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

pub const FLUSH_INTERVAL_MS: f64 = 2_000.0;
pub const SPOT_FLUSH_INTERVAL_MS: f64 = 30_000.0;
const LAG_SAMPLE_EVERY: u64 = 16;

pub struct Collector {
    pub replay: bool,
    virtual_ms: f64,
    started: Instant,
    nbbo: HashMap<String, Nbbo>,
    last_price: HashMap<String, f64>,
    spot_dirty: HashMap<String, (f64, String)>,
    pending: Vec<Event>,
    pub tape: TapeDetector,
    pub sweeps: SweepAggregator,
    pub velocity: VelocityDetector,
    pub spreads: SpreadStats,
    pub bars: BarGrid,
    pub recorder: Option<Recorder>,
    pub proxy: Arc<Proxy>,
    pub db: Option<Writer>,
    next_flush_ms: Option<f64>,
    next_spot_ms: Option<f64>,
    // stats
    pub msgs: BTreeMap<String, u64>,
    pub frames: u64,
    pub persisted: u64,
    pub spots_flushed: u64,
    pub callback_errors: u64,
    pub vendor_errors: u64,
    pub reconnects: u64,
    /// Times the liveness watchdog caught a silently-stalled upstream (main.rs).
    pub stalls: u64,
    lag_samples: Vec<f64>,
    lag_tick: u64,
    // UI snapshot (stats.rs): last-second + ET-day latency histograms, per-minute series
    sec_hist: LagHist,
    day_hist: LagHist,
    minutes: MinuteSeries,
    day: Option<chrono::NaiveDate>,
    sec_ring: [u64; 60],
    ring_pos: usize,
    last_snap_total: u64,
    started_wall: f64,
    pub snapshot: Option<SnapshotWriter>,
}

impl Collector {
    pub fn new(replay: bool, profile_dir: PathBuf, proxy: Arc<Proxy>, db: Writer) -> Self {
        Self {
            replay,
            virtual_ms: 0.0,
            started: Instant::now(),
            nbbo: HashMap::new(),
            last_price: HashMap::new(),
            spot_dirty: HashMap::new(),
            pending: Vec::new(),
            tape: TapeDetector::new(profile_dir, !replay),
            sweeps: SweepAggregator::default(),
            velocity: VelocityDetector::default(),
            spreads: SpreadStats::default(),
            bars: BarGrid::default(),
            recorder: None,
            proxy,
            db: Some(db),
            next_flush_ms: None,
            next_spot_ms: None,
            msgs: BTreeMap::new(),
            frames: 0,
            persisted: 0,
            spots_flushed: 0,
            callback_errors: 0,
            vendor_errors: 0,
            reconnects: 0,
            stalls: 0,
            lag_samples: Vec::with_capacity(8192),
            lag_tick: 0,
            sec_hist: LagHist::default(),
            day_hist: LagHist::default(),
            minutes: MinuteSeries::default(),
            day: None,
            sec_ring: [0; 60],
            ring_pos: 0,
            last_snap_total: 0,
            started_wall: SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0),
            snapshot: None,
        }
    }

    // -- clocks ---------------------------------------------------------------------------

    pub fn now_ms(&self) -> f64 {
        if self.replay {
            self.virtual_ms
        } else {
            SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64() * 1000.0).unwrap_or(0.0)
        }
    }

    /// Seconds clock for the block cooldown: monotonic live, virtual under replay.
    fn cooldown_s(&self) -> f64 {
        if self.replay { self.virtual_ms / 1000.0 } else { self.started.elapsed().as_secs_f64() }
    }

    fn mono_s(&self) -> f64 {
        self.started.elapsed().as_secs_f64()
    }

    /// Replay only — same deadline rule as `EquityTapeRunner._on_clock`.
    fn on_clock(&mut self, ns: i64) {
        let v = ns as f64 / 1e6;
        self.virtual_ms = v;
        let Some(next_flush) = self.next_flush_ms else {
            self.next_flush_ms = Some(v + FLUSH_INTERVAL_MS);
            self.next_spot_ms = Some(v + SPOT_FLUSH_INTERVAL_MS);
            return;
        };
        if v >= next_flush {
            self.flush_events(v);
            self.next_flush_ms = Some(v + FLUSH_INTERVAL_MS);
        }
        if v >= self.next_spot_ms.unwrap_or(f64::INFINITY) {
            self.flush_spots();
            self.next_spot_ms = Some(v + SPOT_FLUSH_INTERVAL_MS);
        }
        self.flush_bars(v);
    }

    // -- frame dispatch (one msgpack array per frame) ---------------------------------------

    pub fn on_frame(&mut self, raw: &[u8]) {
        self.frames += 1;
        if let Some(rec) = &self.recorder {
            rec.record(raw);
        }
        let mut now_ms = self.now_ms();
        let batch = match rmpv::decode::read_value(&mut &raw[..]) {
            Ok(Value::Array(items)) => items,
            _ => {
                *self.msgs.entry("undecodable".into()).or_default() += 1;
                return;
            }
        };
        for m in &batch {
            let t = str_field(m, "T").unwrap_or("?");
            *self.msgs.entry(t.to_string()).or_default() += 1;
            match t {
                "t" => {
                    self.sample_lag(m, now_ms);
                    self.on_trade(m, now_ms);
                }
                "q" => {
                    self.sample_lag(m, now_ms);
                    self.on_quote(m, now_ms);
                }
                "d" | "db" => {
                    if let Some(sym) = str_field(m, "S") {
                        if self.proxy.wants(CH_DAILY, sym) {
                            self.proxy.publish(CH_DAILY, sym, &to_json(m));
                        }
                    }
                }
                "b" | "u" => {
                    let ch = if t == "u" { CH_UPDATED } else { CH_BARS };
                    if let Some(sym) = str_field(m, "S") {
                        if self.proxy.wants(ch, sym) {
                            self.proxy.publish(ch, sym, &to_json(m));
                        }
                    }
                }
                "clock" => {
                    if let Some(ns) = field(m, "ns").and_then(as_i64) {
                        self.on_clock(ns);
                        now_ms = self.now_ms();
                    }
                }
                "error" => {
                    self.vendor_errors += 1;
                    eprintln!("equity stream vendor error frame: {m}");
                }
                "subscription" => eprintln!("equity stream subscription ack: {m}"),
                _ => {}
            }
        }
    }

    fn sample_lag(&mut self, m: &Value, now_ms: f64) {
        self.lag_tick += 1;
        if self.lag_tick % LAG_SAMPLE_EVERY != 0 {
            return;
        }
        if let Some(ts) = ts_to_ms(field(m, "t")) {
            let lag = now_ms - ts;
            if self.lag_samples.len() < 8192 {
                self.lag_samples.push(lag);
            }
            self.sec_hist.add(lag);
            self.day_hist.add(lag);
        }
    }

    /// Once a second (live): the UI snapshot — rates, latency now + today, counters.
    pub fn write_snapshot(&mut self) {
        let Some(w) = self.snapshot.as_mut() else { return };
        let now = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
        let now_s = now.floor() as i64;
        let et_day = chrono::TimeZone::timestamp_opt(&chrono_tz::America::New_York, now_s, 0).single().map(|d| chrono::Datelike::num_days_from_ce(&d));
        let et_day = et_day.map(|n| chrono::NaiveDate::from_num_days_from_ce_opt(n).unwrap());
        if et_day != self.day {
            self.day = et_day;
            self.day_hist.reset();
            self.minutes.reset();
        }
        let total: u64 = self.msgs.values().sum();
        let this_sec = total.saturating_sub(self.last_snap_total);
        self.last_snap_total = total;
        self.sec_ring[self.ring_pos] = this_sec;
        self.ring_pos = (self.ring_pos + 1) % 60;
        let rate_60: f64 = self.sec_ring.iter().sum::<u64>() as f64 / 60.0;
        self.minutes.observe(now_s, this_sec, &self.sec_hist);
        let by_type: serde_json::Map<String, serde_json::Value> =
            self.msgs.iter().map(|(k, v)| (k.clone(), serde_json::Value::from(*v))).collect();
        let v = serde_json::json!({
            "name": "equity",
            "ts": now,
            "pid": std::process::id(),
            "uptime_s": (now - self.started_wall).round(),
            "connected": self.proxy.upstream_healthy(),
            "msgs_total": total,
            "rate_1s": this_sec,
            "rate_60s": (rate_60 * 10.0).round() / 10.0,
            "by_type": by_type,
            "lag_ms": { "now": self.sec_hist.json(), "day": self.day_hist.json() },
            "minutes": self.minutes.json(),
            "events": { "persisted": self.persisted, "sweeps": self.sweeps.emitted, "velocity": self.velocity.emitted,
                        "spots": self.spots_flushed, "bars_rows": self.bars.rows_flushed },
            "proxy": { "clients": self.proxy.client_count(), "subs_trades": self.proxy.union_size(CH_TRADES),
                       "subs_quotes": self.proxy.union_size(CH_QUOTES), "subs_daily": self.proxy.union_size(CH_DAILY),
                       "drops": self.proxy.total_drops.load(std::sync::atomic::Ordering::Relaxed) },
            "recorder": self.recorder.as_ref().map(|r| serde_json::json!({
                "frames": r.frames.load(std::sync::atomic::Ordering::Relaxed),
                "drops": r.drops.load(std::sync::atomic::Ordering::Relaxed) })),
            "reconnects": self.reconnects,
            "stalls": self.stalls,
            "vendor_errors": self.vendor_errors,
            "frames": self.frames,
            "rss_mb": rss_mb(),
        });
        w.write(&v);
        self.sec_hist.reset();
    }

    pub fn lag_percentiles(&mut self) -> (f64, f64) {
        let mut s = std::mem::take(&mut self.lag_samples);
        if s.is_empty() {
            return (f64::NAN, f64::NAN);
        }
        s.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let n = s.len();
        (s[n / 2], s[(n - 1).min((n as f64 * 0.95) as usize)])
    }

    // -- hot path -----------------------------------------------------------------------------

    fn on_quote(&mut self, m: &Value, now_ms: f64) {
        let symbol = str_field(m, "S").unwrap_or("");
        let (Some(bid), Some(ask)) = (field(m, "bp").and_then(as_f64), field(m, "ap").and_then(as_f64)) else {
            return;
        };
        if symbol.is_empty() {
            return;
        }
        let ts_ms = ts_to_ms(field(m, "t")).filter(|v| *v != 0.0).unwrap_or(now_ms);
        self.nbbo.insert(symbol.to_string(), (bid, ask, ts_ms));
        self.spreads.on_quote(symbol, bid, ask);
        if self.proxy.wants(CH_QUOTES, symbol) {
            self.proxy.publish(CH_QUOTES, symbol, &to_json(m));
        }
    }

    fn on_trade(&mut self, m: &Value, now_ms: f64) {
        let symbol = str_field(m, "S").unwrap_or("");
        let Some(price_v) = field(m, "p").filter(|v| !v.is_nil()) else { return };
        if symbol.is_empty() {
            return;
        }
        let Some(price_f) = as_f64(price_v) else { return }; // Python: float() raises → callback error
        let size: i64 = match field(m, "s") {
            None | Some(Value::Nil) => 0,
            Some(v) => as_i64(v).unwrap_or(0),
        };
        let conditions: Vec<String> = match field(m, "c") {
            Some(Value::Array(items)) => items
                .iter()
                .map(|c| c.as_str().map(str::to_string).unwrap_or_else(|| c.to_string()))
                .collect(),
            _ => vec![],
        };
        let ts_ms = ts_to_ms(field(m, "t")).filter(|v| *v != 0.0).unwrap_or(now_ms);
        let nbbo = self.nbbo.get(symbol).copied();
        let exchange_raw = str_field(m, "x").unwrap_or("");

        // Large-print tap BEFORE the exclude-condition drop.
        if size != 0 && price_f > 0.0 {
            let iso = ts_to_iso(field(m, "t"));
            let trade_id = match field(m, "i") {
                None | Some(Value::Nil) => None,
                Some(Value::String(s)) => Some(s.as_str().unwrap_or("").to_string()),
                Some(v) => Some(v.to_string()),
            };
            let prev = self.last_price.get(symbol).copied();
            let (now_s, mono_s) = (self.cooldown_s(), self.mono_s());
            self.tape.on_print(symbol, price_f, size, exchange_raw, &conditions, &iso, trade_id, prev, now_s, mono_s);
            if let Some(t_ms) = ts_to_ms(field(m, "t")) {
                self.bars.on_print(symbol, price_f, size, t_ms);
            }

            if conditions.iter().any(|c| c == "F") {
                let ex = if exchange_raw.is_empty() { "?" } else { exchange_raw };
                if let Some(closed) = self.sweeps.on_iso_print(symbol, price_f, size, ex, ts_ms, nbbo.as_ref()) {
                    self.pending.push(closed);
                }
            }
        }

        let excluded = conditions.iter().any(|c| is_excluded_condition(c));
        if !excluded && size != 0 && price_f > 0.0 {
            let (direction, _basis) = side_vs_nbbo(price_f, nbbo.as_ref(), ts_ms);
            let mut signed = price_f * size as f64;
            if direction == "sell_side" {
                signed = -signed;
            } else if direction != "buy_side" {
                signed = 0.0;
            }
            if let Some(spike) = self.velocity.on_print(symbol, price_f, size, ts_ms, signed) {
                self.pending.push(spike);
            }
            self.last_price.insert(symbol.to_string(), price_f);
            let iso = ts_to_iso(field(m, "t"));
            if !iso.is_empty() {
                self.spot_dirty.insert(symbol.to_string(), (price_f, iso));
            }
        }

        if self.proxy.wants(CH_TRADES, symbol) {
            self.proxy.publish(CH_TRADES, symbol, &to_json(m));
        }
    }

    // -- flushes ------------------------------------------------------------------------------

    fn gather_events(&mut self, now_ms: f64) -> Vec<Event> {
        let mut events: Vec<Event> = self.tape.pending.drain(..).collect();
        events.extend(self.sweeps.flush(now_ms));
        events.extend(self.velocity.flush(now_ms));
        events.append(&mut self.pending);
        events
    }

    pub fn flush_events(&mut self, now_ms: f64) {
        let events = self.gather_events(now_ms);
        if events.is_empty() {
            return;
        }
        self.persisted += events.len() as u64;
        if let Some(db) = &self.db {
            db.send(Cmd::Events(events));
        }
    }

    pub fn flush_spots(&mut self) {
        if self.spot_dirty.is_empty() {
            return;
        }
        let dirty = std::mem::take(&mut self.spot_dirty);
        let rows: Vec<(String, String, String)> =
            dirty.into_iter().map(|(s, (p, ts))| (s, py_repr(p), ts)).collect();
        self.spots_flushed += rows.len() as u64;
        if let Some(db) = &self.db {
            db.send(Cmd::Spots(rows));
        }
    }

    /// Timer path: hand the completed 5-min buckets to the writer at each boundary.
    pub fn flush_bars(&mut self, now_ms: f64) {
        if let Some(flush) = self.bars.maybe_flush(now_ms) {
            if let Some(db) = &self.db {
                db.send(Cmd::Bars(flush));
            }
        }
    }

    pub fn stats_line(&mut self, interval_s: f64, last_total: &mut u64) -> String {
        let total: u64 = self.msgs.values().sum();
        let rate = (total - *last_total) as f64 / interval_s;
        *last_total = total;
        let (p50, p95) = self.lag_percentiles();
        let wideners: Vec<String> = self
            .spreads
            .top_wideners(5)
            .into_iter()
            .map(|(s, r, bps)| format!("{s}:{r:.0}x@{bps:.0}bps"))
            .collect();
        format!(
            "equity stream stats: msgs={} ({:.0}/s) t={} q={} reconnects={} stalls={} vendor_errors={} lag_p50_ms={:.0} lag_p95_ms={:.0} \
             | events persisted={} sweeps={} velocity={} spots={} bars={} | proxy clients={} subs t/q/db={}/{}/{} drops={} | rec frames={} drops={} | wideners: {}",
            total,
            rate,
            self.msgs.get("t").copied().unwrap_or(0),
            self.msgs.get("q").copied().unwrap_or(0),
            self.reconnects,
            self.stalls,
            self.vendor_errors,
            p50,
            p95,
            self.persisted,
            self.sweeps.emitted,
            self.velocity.emitted,
            self.spots_flushed,
            self.bars.rows_flushed,
            self.proxy.client_count(),
            self.proxy.union_size(CH_TRADES),
            self.proxy.union_size(CH_QUOTES),
            self.proxy.union_size(CH_DAILY),
            self.proxy.total_drops.load(std::sync::atomic::Ordering::Relaxed),
            self.recorder.as_ref().map(|r| r.frames.load(std::sync::atomic::Ordering::Relaxed)).unwrap_or(0),
            self.recorder.as_ref().map(|r| r.drops.load(std::sync::atomic::Ordering::Relaxed)).unwrap_or(0),
            if wideners.is_empty() { "-".to_string() } else { wideners.join(", ") },
        )
    }
}

fn rss_mb() -> f64 {
    std::fs::read_to_string("/proc/self/statm")
        .ok()
        .and_then(|s| s.split_whitespace().nth(1).and_then(|p| p.parse::<f64>().ok()))
        .map(|pages| (pages * 4096.0 / 1e6 * 10.0).round() / 10.0)
        .unwrap_or(0.0)
}
