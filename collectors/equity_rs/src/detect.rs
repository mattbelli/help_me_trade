//! Tape detectors — line-for-line ports of `signals/equity_stream/detectors.py`
//! and `signals/equity_tape.py` (blocks / dark pool). Same thresholds, same
//! event dicts (ids, reasons, caveats, detail), same rounding.

use crate::pyfmt::{fixed, fixed_commas, int_commas, iso_from_unix, py_floordiv, round_nd};
use serde_json::{json, Value as Json};
use std::collections::{BTreeSet, HashMap, VecDeque};
use std::path::PathBuf;

// --- constants (signals/equity_stream/constants.py + equity_tape.py) -------------
pub const NBBO_FRESH_MS: f64 = 2_000.0;
pub const SWEEP_WINDOW_MS: f64 = 1_000.0;
pub const SWEEP_MIN_NOTIONAL_USD: f64 = 500_000.0;
pub const SWEEP_MIN_VENUES: usize = 3;
pub const SWEEP_COOLDOWN_SECONDS: f64 = 30.0;
pub const VELOCITY_BUCKET_SECONDS: i64 = 10;
pub const VELOCITY_WARMUP_BUCKETS: i64 = 15;
pub const VELOCITY_SPIKE_RATIO: f64 = 10.0;
pub const VELOCITY_MIN_PRINTS: i64 = 30;
pub const VELOCITY_EMA_ALPHA: f64 = 0.05;
pub const VELOCITY_COOLDOWN_SECONDS: f64 = 300.0;
pub const VELOCITY_EMA_SPIKE_CAP_RATIO: f64 = 3.0;
pub const VELOCITY_MIXED_NET_FRACTION: f64 = 0.2;
pub const SPREAD_SAMPLE_EVERY: u64 = 16;
pub const SPREAD_BASELINE_SAMPLES: usize = 64;
pub const BLOCK_MIN_NOTIONAL_USD: f64 = 1_000_000.0;
pub const ADV_NOTIONAL_FRACTION: f64 = 0.0001;
pub const NO_PROFILE_MIN_NOTIONAL_USD: f64 = 10_000_000.0;
pub const TAPE_COOLDOWN_SECONDS: f64 = 60.0;
pub const DARK_POOL_EXCHANGE: &str = "D";
pub const TAPE_MAX_OFF_MARKET_PCT: f64 = 0.10;
const PROFILE_TTL_SECONDS: f64 = 3600.0;
const PROFILE_MAX_AGE_DAYS: f64 = 7.0;
pub const TAPE_PENDING_MAX: usize = 256;

/// brokers/market_stream.py TRADE_EXCLUDE_CONDITIONS
pub fn is_excluded_condition(c: &str) -> bool {
    matches!(
        c,
        "4" | "7" | "9" | "B" | "C" | "G" | "H" | "M" | "N" | "P" | "Q" | "R" | "T" | "U" | "V" | "W" | "Z"
    )
}

/// One `equity_tape_events` row (the Python event dict).
#[derive(Debug, Clone)]
pub struct Event {
    pub id: String,
    pub ts: String,
    pub symbol: String,
    pub event_type: &'static str,
    pub exchange: Option<String>,
    pub price: f64,
    pub size: i64,
    pub notional_usd: f64,
    pub direction: &'static str,
    pub direction_basis: &'static str,
    pub conditions: Vec<String>,
    pub reason: String,
    pub caveat: &'static str,
    pub detail: Json,
}

/// (bid, ask, ts_ms)
pub type Nbbo = (f64, f64, f64);

pub fn side_vs_nbbo(price: f64, nbbo: Option<&Nbbo>, now_ms: f64) -> (&'static str, &'static str) {
    if let Some(&(bid, ask, ts)) = nbbo {
        if bid > 0.0 && ask > bid && now_ms - ts <= NBBO_FRESH_MS {
            if price >= ask {
                return ("buy_side", "nbbo_at_print");
            }
            if price <= bid {
                return ("sell_side", "nbbo_at_print");
            }
            return ("undetermined", "nbbo_at_print");
        }
    }
    ("undetermined", "stale_quote")
}

fn iso_from_ms(ts_ms: f64) -> String {
    iso_from_unix(ts_ms / 1000.0)
}

// --- ISO sweeps ---------------------------------------------------------------------

struct SweepBucket {
    start_ms: f64,
    end_ms: f64,
    buy_notional: f64,
    sell_notional: f64,
    gross: f64,
    shares: i64,
    prints: i64,
    venues: BTreeSet<String>,
    px_min: f64,
    px_max: f64,
    last_price: f64,
    stale_quote: bool,
}

#[derive(Default)]
pub struct SweepAggregator {
    active: HashMap<String, SweepBucket>,
    cooldown_until_ms: HashMap<String, f64>,
    pub emitted: u64,
    pub suppressed: u64,
}

impl SweepAggregator {
    pub fn on_iso_print(
        &mut self,
        symbol: &str,
        price: f64,
        size: i64,
        exchange: &str,
        ts_ms: f64,
        nbbo: Option<&Nbbo>,
    ) -> Option<Event> {
        let mut closed = None;
        if let Some(b) = self.active.get(symbol) {
            if ts_ms - b.start_ms > SWEEP_WINDOW_MS {
                let b = self.active.remove(symbol).unwrap();
                closed = self.finalize(symbol, b);
            }
        }
        let bucket = self.active.entry(symbol.to_string()).or_insert_with(|| SweepBucket {
            start_ms: ts_ms,
            end_ms: ts_ms,
            buy_notional: 0.0,
            sell_notional: 0.0,
            gross: 0.0,
            shares: 0,
            prints: 0,
            venues: BTreeSet::new(),
            px_min: price,
            px_max: price,
            last_price: price,
            stale_quote: false,
        });
        let notional = price * size as f64;
        let (direction, basis) = side_vs_nbbo(price, nbbo, ts_ms);
        if direction == "buy_side" {
            bucket.buy_notional += notional;
        } else if direction == "sell_side" {
            bucket.sell_notional += notional;
        }
        if basis == "stale_quote" {
            bucket.stale_quote = true;
        }
        bucket.gross += notional;
        bucket.shares += size;
        bucket.prints += 1;
        bucket.venues.insert(exchange.to_string());
        bucket.px_min = bucket.px_min.min(price);
        bucket.px_max = bucket.px_max.max(price);
        bucket.last_price = price;
        bucket.end_ms = ts_ms;
        closed
    }

    pub fn flush(&mut self, now_ms: f64) -> Vec<Event> {
        let mut out = Vec::new();
        // Python iterates `list(self._active)` — insertion order of the dict.
        // Order only affects insert order (ingest_seq), not the A/B; sort for
        // determinism across runs.
        let mut expired: Vec<String> = self
            .active
            .iter()
            .filter(|(_, b)| now_ms - b.start_ms > SWEEP_WINDOW_MS)
            .map(|(s, _)| s.clone())
            .collect();
        expired.sort();
        for symbol in expired {
            let b = self.active.remove(&symbol).unwrap();
            if let Some(ev) = self.finalize(&symbol, b) {
                out.push(ev);
            }
        }
        out
    }

    fn finalize(&mut self, symbol: &str, b: SweepBucket) -> Option<Event> {
        let gross = b.gross;
        if gross < SWEEP_MIN_NOTIONAL_USD || b.venues.len() < SWEEP_MIN_VENUES {
            return None;
        }
        let until = self.cooldown_until_ms.get(symbol).copied().unwrap_or(0.0);
        if b.start_ms < until {
            self.suppressed += 1;
            return None;
        }
        self.cooldown_until_ms
            .insert(symbol.to_string(), b.start_ms + SWEEP_COOLDOWN_SECONDS * 1000.0);
        let (buy, sell) = (b.buy_notional, b.sell_notional);
        let classified = buy + sell;
        let direction = if classified > 0.0 && (buy - sell).abs() >= classified * 0.5 {
            if buy > sell { "buy_side" } else { "sell_side" }
        } else {
            "undetermined"
        };
        let basis = if !b.stale_quote { "nbbo_at_print" } else { "nbbo_partial" };
        let start_iso = iso_from_ms(b.start_ms);
        let span_ms = (b.end_ms - b.start_ms) as i64;
        self.emitted += 1;
        let venues: Vec<String> = b.venues.iter().cloned().collect();
        Some(Event {
            id: format!("eqsw_{}_{}_{}", &start_iso[..10], symbol, b.start_ms as i64),
            ts: start_iso,
            symbol: symbol.to_string(),
            event_type: "equity_sweep",
            exchange: Some("multi".into()),
            price: round_nd(b.last_price, 4),
            size: b.shares,
            notional_usd: round_nd(gross, 2),
            direction,
            direction_basis: basis,
            conditions: vec!["F".into()],
            reason: format!(
                "${}M ISO sweep · {} prints across {} venues in {}ms",
                fixed_commas(gross / 1e6, 1),
                b.prints,
                venues.len(),
                span_ms.max(1)
            ),
            caveat: "Intermarket-sweep prints clustered across venues; side judged against the NBBO at each print. A fact about the tape, not a buy/sell signal.",
            detail: json!({
                "venues": venues,
                "prints": b.prints,
                "px_min": round_nd(b.px_min, 4),
                "px_max": round_nd(b.px_max, 4),
                "window_ms": span_ms,
                "buy_notional": round_nd(buy, 2),
                "sell_notional": round_nd(sell, 2),
            }),
        })
    }
}

// --- print velocity ---------------------------------------------------------------

struct VelState {
    idx: i64,
    count: i64,
    gross: f64,
    net: f64,
    shares: i64,
    last_price: f64,
    baseline: f64,
    buckets_seen: i64,
    cooldown_until_ms: f64,
}

#[derive(Default)]
pub struct VelocityDetector {
    sym: HashMap<String, VelState>,
    pub emitted: u64,
}

impl VelocityDetector {
    pub fn on_print(&mut self, symbol: &str, price: f64, size: i64, ts_ms: f64, signed_notional: f64) -> Option<Event> {
        let bucket_ms = VELOCITY_BUCKET_SECONDS as f64 * 1000.0;
        let idx = py_floordiv(ts_ms, bucket_ms) as i64;
        let Some(st) = self.sym.get_mut(symbol) else {
            self.sym.insert(
                symbol.to_string(),
                VelState {
                    idx,
                    count: 1,
                    gross: price * size as f64,
                    net: signed_notional,
                    shares: size,
                    last_price: price,
                    baseline: 0.0,
                    buckets_seen: 0,
                    cooldown_until_ms: 0.0,
                },
            );
            return None;
        };
        if idx == st.idx {
            st.count += 1;
            st.gross += price * size as f64;
            st.net += signed_notional;
            st.shares += size;
            st.last_price = price;
            return None;
        }
        let event = Self::close_bucket(symbol, st, &mut self.emitted);
        Self::decay_gap(st, idx);
        st.idx = idx;
        st.count = 1;
        st.gross = price * size as f64;
        st.net = signed_notional;
        st.shares = size;
        st.last_price = price;
        event
    }

    pub fn flush(&mut self, now_ms: f64) -> Vec<Event> {
        let current_idx = py_floordiv(now_ms, VELOCITY_BUCKET_SECONDS as f64 * 1000.0) as i64;
        let mut out = Vec::new();
        let mut keys: Vec<&String> = self.sym.keys().collect();
        keys.sort();
        let keys: Vec<String> = keys.into_iter().cloned().collect();
        for symbol in keys {
            let st = self.sym.get_mut(&symbol).unwrap();
            if current_idx <= st.idx || st.count == 0 {
                continue;
            }
            if let Some(ev) = Self::close_bucket(&symbol, st, &mut self.emitted) {
                out.push(ev);
            }
            Self::decay_gap(st, current_idx);
            st.idx = current_idx;
            st.count = 0;
            st.gross = 0.0;
            st.net = 0.0;
            st.shares = 0;
        }
        out
    }

    fn decay_gap(st: &mut VelState, new_idx: i64) {
        let gap = (new_idx - st.idx - 1).clamp(0, 60);
        for _ in 0..gap {
            st.baseline *= 1.0 - VELOCITY_EMA_ALPHA;
            st.buckets_seen += 1;
        }
    }

    fn close_bucket(symbol: &str, st: &mut VelState, emitted: &mut u64) -> Option<Event> {
        let count = st.count;
        let baseline = st.baseline;
        let eligible = st.buckets_seen >= VELOCITY_WARMUP_BUCKETS
            && baseline > 0.0
            && count >= VELOCITY_MIN_PRINTS
            && count as f64 >= baseline * VELOCITY_SPIKE_RATIO;
        let bucket_start_ms = st.idx as f64 * VELOCITY_BUCKET_SECONDS as f64 * 1000.0;
        let mut event = None;
        if eligible && bucket_start_ms >= st.cooldown_until_ms {
            st.cooldown_until_ms = bucket_start_ms + VELOCITY_COOLDOWN_SECONDS * 1000.0;
            event = Some(Self::build_event(symbol, st, bucket_start_ms));
            *emitted += 1;
        }
        let mut contribution = count as f64;
        if baseline > 0.0 {
            contribution = contribution.min(baseline * VELOCITY_EMA_SPIKE_CAP_RATIO);
        }
        st.baseline = baseline * (1.0 - VELOCITY_EMA_ALPHA) + contribution * VELOCITY_EMA_ALPHA;
        st.buckets_seen += 1;
        event
    }

    fn build_event(symbol: &str, st: &VelState, bucket_start_ms: f64) -> Event {
        let count = st.count;
        let gross = st.gross;
        let net = st.net;
        let baseline = st.baseline;
        let ratio = if baseline > 0.0 { count as f64 / baseline } else { f64::INFINITY };
        let direction = if gross > 0.0 && net.abs() >= gross * VELOCITY_MIXED_NET_FRACTION {
            if net > 0.0 { "buy_side" } else { "sell_side" }
        } else {
            "mixed"
        };
        let start_iso = iso_from_ms(bucket_start_ms);
        Event {
            id: format!("eqvel_{}_{}_{}", &start_iso[..10], symbol, bucket_start_ms as i64),
            ts: start_iso,
            symbol: symbol.to_string(),
            event_type: "tape_velocity",
            exchange: None,
            price: round_nd(st.last_price, 4),
            size: st.shares,
            notional_usd: round_nd(gross, 2),
            direction,
            direction_basis: "nbbo_net_aggressor",
            conditions: vec![],
            reason: format!(
                "{} prints in {}s vs ~{} typical ({}x) · ${}M traded",
                count,
                VELOCITY_BUCKET_SECONDS,
                fixed(baseline, 1),
                fixed(ratio, 0),
                fixed_commas(gross / 1e6, 1)
            ),
            caveat: "Print-rate spike vs this name's own trailing baseline — attention, not direction; net-aggressor side is weak evidence. A fact about the tape, not a buy/sell signal.",
            detail: json!({
                "prints": count,
                "baseline_prints": round_nd(baseline, 2),
                "ratio": round_nd(ratio, 1),
                "bucket_seconds": VELOCITY_BUCKET_SECONDS,
                "net_aggressor_usd": round_nd(net, 2),
            }),
        }
    }
}

// --- spread stats (calibration only) -----------------------------------------------

#[derive(Default)]
pub struct SpreadStats {
    seen: u64,
    samples: HashMap<String, VecDeque<f64>>,
    max_ratio: HashMap<String, (f64, f64)>,
}

impl SpreadStats {
    pub fn on_quote(&mut self, symbol: &str, bid: f64, ask: f64) {
        self.seen += 1;
        if self.seen % SPREAD_SAMPLE_EVERY != 0 {
            return;
        }
        if bid <= 0.0 || ask <= bid {
            return;
        }
        let bps = (ask - bid) / ((ask + bid) / 2.0) * 1e4;
        let dq = self.samples.entry(symbol.to_string()).or_default();
        if dq.len() >= 8 {
            let mut sorted: Vec<f64> = dq.iter().copied().collect();
            sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let median = sorted[sorted.len() / 2];
            if median > 0.0 {
                let ratio = bps / median;
                let best = self.max_ratio.get(symbol);
                if best.map_or(true, |b| ratio > b.0) {
                    self.max_ratio.insert(symbol.to_string(), (ratio, bps));
                }
            }
        }
        if dq.len() >= SPREAD_BASELINE_SAMPLES {
            dq.pop_front();
        }
        dq.push_back(bps);
    }

    pub fn top_wideners(&mut self, n: usize) -> Vec<(String, f64, f64)> {
        let mut items: Vec<(String, f64, f64)> =
            self.max_ratio.iter().map(|(s, (r, b))| (s.clone(), *r, *b)).collect();
        items.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
        items.truncate(n);
        self.max_ratio.clear();
        items
    }
}

// --- blocks / dark pool (signals/equity_tape.py) ---------------------------------------

pub struct TapeDetector {
    pub pending: VecDeque<Event>,
    profile_dir: PathBuf,
    /// Live: ignore profiles older than 7 days (Python load_profile default).
    /// Replay: no age rule (runner._replay_median_shares).
    enforce_profile_age: bool,
    median_cache: HashMap<String, (f64, Option<f64>)>,
    cooldown: HashMap<(String, &'static str), f64>,
}

impl TapeDetector {
    pub fn new(profile_dir: PathBuf, enforce_profile_age: bool) -> Self {
        Self {
            pending: VecDeque::new(),
            profile_dir,
            enforce_profile_age,
            median_cache: HashMap::new(),
            cooldown: HashMap::new(),
        }
    }

    fn load_median(&self, symbol: &str) -> Option<f64> {
        let path: PathBuf = if let Some(rest) = symbol.strip_prefix('/') {
            self.profile_dir.join("futures").join(format!("{rest}.json"))
        } else {
            self.profile_dir.join(format!("{symbol}.json"))
        };
        let meta = std::fs::metadata(&path).ok()?;
        if !meta.is_file() {
            return None;
        }
        if self.enforce_profile_age {
            let age = meta.modified().ok()?.elapsed().ok()?.as_secs_f64();
            if age > PROFILE_MAX_AGE_DAYS * 86400.0 {
                return None;
            }
        }
        let text = std::fs::read_to_string(&path).ok()?;
        let v: Json = serde_json::from_str(&text).ok()?;
        match v.get("median_daily_total")? {
            Json::Number(n) => n.as_f64(),
            Json::String(s) => s.parse().ok(),
            _ => None,
        }
    }

    fn median_shares(&mut self, symbol: &str, mono_s: f64) -> Option<f64> {
        if let Some((at, v)) = self.median_cache.get(symbol) {
            if mono_s - at < PROFILE_TTL_SECONDS {
                return *v;
            }
        }
        let v = self.load_median(symbol);
        self.median_cache.insert(symbol.to_string(), (mono_s, v));
        v
    }

    pub fn notional_floor(&mut self, symbol: &str, price: f64, mono_s: f64) -> f64 {
        match self.median_shares(symbol, mono_s) {
            Some(m) if m > 0.0 => BLOCK_MIN_NOTIONAL_USD.max(m * price * ADV_NOTIONAL_FRACTION),
            _ => NO_PROFILE_MIN_NOTIONAL_USD,
        }
    }

    /// `now_s`: seconds clock for the cooldown (monotonic live, virtual/1000 replay).
    #[allow(clippy::too_many_arguments)]
    pub fn on_print(
        &mut self,
        symbol: &str,
        price: f64,
        size: i64,
        exchange: &str,
        conditions: &[String],
        ts: &str,
        trade_id: Option<String>,
        prev_price: Option<f64>,
        now_s: f64,
        mono_s: f64,
    ) -> Option<Event> {
        if price <= 0.0 || size <= 0 {
            return None;
        }
        let prev = prev_price?;
        if prev <= 0.0 {
            return None;
        }
        if (price - prev).abs() > prev * TAPE_MAX_OFF_MARKET_PCT {
            return None;
        }
        let notional = price * size as f64;
        if notional < BLOCK_MIN_NOTIONAL_USD {
            return None;
        }
        let floor = self.notional_floor(symbol, price, mono_s);
        if notional < floor {
            return None;
        }
        let ex = exchange.trim().to_uppercase();
        let kind: &'static str = if ex == DARK_POOL_EXCHANGE { "dark_pool" } else { "equity_block" };
        let key = (symbol.to_string(), kind);
        if let Some(last) = self.cooldown.get(&key) {
            if now_s - last < TAPE_COOLDOWN_SECONDS {
                return None;
            }
        }
        self.cooldown.insert(key, now_s);
        let direction = if price > prev {
            "buy_side"
        } else if price < prev {
            "sell_side"
        } else {
            "undetermined"
        };
        let ts_s = ts.trim().to_string();
        let date = if ts_s.len() >= 10 { ts_s[..10].to_string() } else { String::new() };
        let tid = trade_id.unwrap_or_else(|| ts_s.clone());
        let venue = if kind == "dark_pool" { "off-exchange (TRF)".to_string() } else { format!("exchange {ex}") };
        let reason = format!(
            "${}M print · {} sh @ ${} · {}",
            fixed_commas(notional / 1_000_000.0, 1),
            int_commas(size),
            fixed_commas(price, 2),
            venue
        );
        let caveat: &'static str = if kind == "dark_pool" {
            "Off-exchange print reported to FINRA TRF — may lag the fill by up to ~10s; side inferred from tick test (weak). A fact about the tape, not a buy/sell signal."
        } else {
            "Side inferred from tick test (weak). A fact about the tape, not a buy/sell signal."
        };
        let ev = Event {
            id: format!("eq_{}_{}_{}_{}", date, symbol, if kind == "dark_pool" { "dp" } else { "blk" }, tid),
            ts: ts_s,
            symbol: symbol.to_string(),
            event_type: kind,
            exchange: Some(ex),
            price: round_nd(price, 4),
            size,
            notional_usd: round_nd(notional, 2),
            direction,
            direction_basis: "tick",
            conditions: conditions.to_vec(),
            reason,
            caveat,
            detail: json!({"floor_usd": round_nd(floor, 2)}),
        };
        if self.pending.len() >= TAPE_PENDING_MAX {
            self.pending.pop_front(); // deque(maxlen=256) semantics
        }
        self.pending.push_back(ev.clone());
        Some(ev)
    }
}

