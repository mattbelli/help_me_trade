//! The Alpaca quote book — Stage A (shadow) of making Alpaca the flow spine.
//! Alpaca's wildcard trades are the whole tape but carry no quote, and option
//! quote subscriptions cap at exactly 1,000 contracts on the one OPRA socket.
//! So: heat-following allocation of those 1,000 slots (decayed premium per
//! contract off the trade tape we already read), a small per-contract quote
//! ring so a trade is signed against the book BEFORE it by OPRA timestamp (the
//! tcbbo meaning — arrival order is not safe, the quote a trade caused can
//! arrive first), and a whole-market tick test for everything unquoted.
//!
//! Every Alpaca trade gets a `Shadow` (quote + tick direction) that rides the
//! pairing matcher to its dbn twin; the runner scores it against tcbbo's
//! direction into `ShadowStats`. Nothing here touches what the spine writes.

use crate::gauge::GAUGE_UNDERLYINGS;
use crate::intraday::classify_direction;
use crate::print::Dir;
use crate::runner::QUOTE_STALE_MS;
use chrono::{Datelike, NaiveTime, TimeZone};
use chrono_tz::America::New_York;
use std::collections::{HashMap, HashSet};
use std::path::Path;

pub const QUOTE_CAP: usize = 1_000; // measured: 2,000 → 405 "symbol limit exceeded"
pub const GAUGE_RESERVE: usize = 300; // slots held for gauge::GAUGE_UNDERLYINGS
pub const CANDIDATE_USD: f64 = 25_000.0;
pub const RERANK_EVERY_NS: i64 = 5_000_000_000;
const HALF_LIFE_NS: f64 = 300e9;
const EVICT_RATIO: f64 = 1.5;
const MIN_DWELL_NS: i64 = 60_000_000_000;
const POOL_FLOOR_USD: f64 = 1_000.0; // a candidate decayed below this leaves the pool (re-enters on heat)
const RING: usize = 4;
const STALE_NS: i64 = QUOTE_STALE_MS * 1_000_000;
const BUCKET_EDGES_USD: [f64; 3] = [25_000.0, 100_000.0, 250_000.0];
/// Alpaca spine: sign an unquoted print by the tick test (basis "tick")? Off —
/// the 10-23 shadow measured tick vs dbn_nbbo at 67% agreement (25% undetermined)
/// against 99.7% for the pre-trade quote, so an unquoted print stays undetermined.
pub const TICK_FALLBACK: bool = false;
pub const BUCKET_LABELS: [&str; 4] = ["lt25k", "25k_100k", "100k_250k", "ge250k"];

fn decay(dt_ns: i64) -> f64 {
    (-(dt_ns.max(0) as f64) * std::f64::consts::LN_2 / HALF_LIFE_NS).exp()
}

fn root(sym: &str) -> &str {
    let n = sym.bytes().take_while(|c| c.is_ascii_alphabetic()).count();
    &sym[..n]
}

fn is_gauge(sym: &str) -> bool {
    GAUGE_UNDERLYINGS.contains(&root(sym))
}

/// Compact OCC → expiry as yymmdd (the 6 digits after the root).
fn expiry_yymmdd(sym: &str) -> Option<u32> {
    let b = sym.as_bytes();
    if b.len() < 16 {
        return None;
    }
    let d = &b[b.len() - 15..b.len() - 9];
    d.iter().all(|c| c.is_ascii_digit()).then(|| d.iter().fold(0u32, |a, c| a * 10 + (c - b'0') as u32))
}

fn et_yymmdd(t_ns: i64) -> u32 {
    let d = New_York.timestamp_nanos(t_ns).date_naive();
    (d.year() as u32 % 100) * 10_000 + d.month() * 100 + d.day()
}

/// Quotes stream 09:30–16:15 ET only; outside it the set is held as is.
pub fn in_quote_hours(now_ns: i64) -> bool {
    let dt = New_York.timestamp_nanos(now_ns);
    let tod = dt.time();
    crate::calendar::is_trading_day(dt.date_naive())
        && NaiveTime::from_hms_opt(9, 30, 0).unwrap() <= tod
        && tod < NaiveTime::from_hms_opt(16, 15, 0).unwrap()
}

/// `in_quote_hours` per trade: one integer compare against the cached
/// [09:30, 16:15) ET window of the trade's day (the Alpaca spine's Runner gate).
#[derive(Default)]
pub struct QuoteHours {
    lo: i64,
    hi: i64,
}

impl QuoteHours {
    pub fn contains(&mut self, t_ns: i64) -> bool {
        if self.lo <= t_ns && t_ns < self.hi {
            return true;
        }
        if !in_quote_hours(t_ns) {
            return false;
        }
        let day = New_York.timestamp_nanos(t_ns).date_naive();
        let at = |h, m| New_York.from_local_datetime(&day.and_hms_opt(h, m, 0).unwrap()).single().and_then(|x| x.timestamp_nanos_opt());
        if let (Some(lo), Some(hi)) = (at(9, 30), at(16, 15)) {
            (self.lo, self.hi) = (lo, hi);
        }
        true
    }
}

// --- the quote ring -----------------------------------------------------------------------------

#[derive(Default, Clone, Copy, Debug, PartialEq)]
pub struct Quote {
    pub t_ns: i64,
    pub bid: f64,
    pub ask: f64,
}

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Miss {
    NoRing,     // contract not quoted (or no quote yet)
    NoneBefore, // every ring quote is at/after the trade
    Stale,
    BadBook, // crossed, locked or zero
}

/// The last RING quotes by arrival.
#[derive(Default)]
struct Ring {
    q: [Quote; RING],
    n: usize,
    head: usize,
}

impl Ring {
    fn push(&mut self, q: Quote) {
        self.q[self.head] = q;
        self.head = (self.head + 1) % RING;
        self.n = (self.n + 1).min(RING);
    }

    fn iter(&self) -> impl Iterator<Item = &Quote> {
        self.q.iter().take(self.n)
    }

    /// The latest quote with `t < trade_t`, then judged like tcbbo's book:
    /// stale (> QUOTE_STALE_MS) or not 0 < bid < ask → no usable quote.
    fn pre_trade(&self, trade_t: i64) -> Result<Quote, Miss> {
        if self.n == 0 {
            return Err(Miss::NoRing);
        }
        let q = self.iter().filter(|q| q.t_ns < trade_t).max_by_key(|q| q.t_ns).ok_or(Miss::NoneBefore)?;
        if trade_t - q.t_ns > STALE_NS {
            return Err(Miss::Stale);
        }
        if !(0.0 < q.bid && q.bid < q.ask) {
            return Err(Miss::BadBook);
        }
        Ok(*q)
    }

    /// The ordering check: the latest-ARRIVED quote, whatever its timestamp.
    fn latest_arrived(&self, trade_t: i64) -> Option<(f64, f64)> {
        if self.n == 0 {
            return None;
        }
        let q = &self.q[(self.head + RING - 1) % RING];
        ((trade_t - q.t_ns).abs() <= STALE_NS && 0.0 < q.bid && q.bid < q.ask).then_some((q.bid, q.ask))
    }
}

// --- the per-trade shadow ------------------------------------------------------------------------

/// An Alpaca trade's own direction. `quote`/`arrival` None = no usable quote.
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub struct Shadow {
    pub quote: Option<Dir>,
    pub arrival: Option<Dir>,
    pub tick: Dir,
}

impl Default for Shadow {
    fn default() -> Self {
        Self { quote: None, arrival: None, tick: Dir::Undetermined }
    }
}

struct Contract {
    heat: f64,
    heat_t: i64,
    cand: bool,
    /// Tick test state; `last_cents` 0 = no print yet today.
    last_cents: i64,
    tick: Dir,
}

struct Slot {
    since_ns: i64,
    ring: Ring,
}

/// Cumulative-for-day book counters (reset on the ET day roll of the trade tape).
#[derive(Default, Clone)]
pub struct BookStats {
    pub reranks: u64,
    pub subs: u64,
    pub unsubs: u64,
    pub server_errors: u64,
    pub acked_quotes: Option<usize>,
    pub quotes: u64,
    pub stray_quotes: u64,
    pub usable: u64,
    pub no_ring: u64,
    pub none_before: u64,
    pub stale: u64,
    pub bad_book: u64,
}

pub struct QuoteBook {
    contracts: HashMap<Box<str>, Contract>,
    pool: HashSet<Box<str>>,
    slots: HashMap<Box<str>, Slot>,
    day: u32,
    day_lo: i64,
    day_hi: i64,
    pub m: BookStats,
    last_line: BookStats,
}

impl Default for QuoteBook {
    fn default() -> Self {
        Self::new()
    }
}

impl QuoteBook {
    pub fn new() -> Self {
        Self {
            contracts: HashMap::new(),
            pool: HashSet::new(),
            slots: HashMap::new(),
            day: 0,
            day_lo: 0,
            day_hi: 0,
            m: BookStats::default(),
            last_line: BookStats::default(),
        }
    }

    pub fn set(&self) -> Vec<String> {
        self.slots.keys().map(|k| k.to_string()).collect()
    }

    /// New ET day on the trade tape: tick state resets, only quoted contracts
    /// and live candidates keep their entries (the map is whole-market).
    fn roll_day(&mut self, t_ns: i64) {
        if self.day_lo <= t_ns && t_ns < self.day_hi {
            return;
        }
        let d = New_York.timestamp_nanos(t_ns).date_naive();
        let bound = |day: chrono::NaiveDate| {
            New_York.from_local_datetime(&day.and_hms_opt(0, 0, 0).unwrap()).earliest().and_then(|x| x.timestamp_nanos_opt()).unwrap_or(0)
        };
        let yymmdd = et_yymmdd(t_ns);
        if yymmdd < self.day {
            return; // a late print from yesterday: leave today's state alone
        }
        self.day_lo = bound(d);
        self.day_hi = bound(d + chrono::Duration::days(1));
        if yymmdd == self.day {
            return;
        }
        self.day = yymmdd;
        let (slots, pool) = (&self.slots, &self.pool);
        self.contracts.retain(|k, _| slots.contains_key(k) || pool.contains(k));
        for c in self.contracts.values_mut() {
            c.last_cents = 0;
            c.tick = Dir::Undetermined;
        }
        self.m = BookStats { acked_quotes: self.m.acked_quotes, ..Default::default() };
        self.last_line = BookStats::default();
    }

    /// Per Alpaca trade, O(1): heat + candidacy, tick test, pre-trade quote.
    /// Returns the shadow directions and the usable pre-trade book itself.
    pub fn on_trade(&mut self, sym: &str, t_ns: i64, cents: i64, size: i64) -> (Shadow, Option<Quote>) {
        self.roll_day(t_ns);
        let prem = (cents * size) as f64; // $ = price × size × 100 = cents × size
        if !self.contracts.contains_key(sym) {
            self.contracts.insert(sym.into(), Contract { heat: 0.0, heat_t: t_ns, cand: false, last_cents: 0, tick: Dir::Undetermined });
        }
        let c = self.contracts.get_mut(sym).unwrap();
        c.heat = c.heat * decay(t_ns - c.heat_t) + prem;
        c.heat_t = c.heat_t.max(t_ns);
        if !c.cand && (prem >= CANDIDATE_USD || c.heat >= CANDIDATE_USD) {
            c.cand = true;
            self.pool.insert(sym.into());
        }
        let tick = if c.last_cents == 0 {
            Dir::Undetermined
        } else if cents > c.last_cents {
            Dir::Buy
        } else if cents < c.last_cents {
            Dir::Sell
        } else {
            c.tick
        };
        if cents > 0 {
            c.last_cents = cents;
            c.tick = tick;
        }
        let px = cents as f64 / 100.0;
        let mut book = None;
        let (quote, arrival) = match self.slots.get(sym) {
            None => {
                self.m.no_ring += 1;
                (None, None)
            }
            Some(s) => {
                let quote = match s.ring.pre_trade(t_ns) {
                    Ok(q) => {
                        self.m.usable += 1;
                        book = Some(q);
                        Some(classify_direction(px, q.bid, q.ask))
                    }
                    Err(miss) => {
                        match miss {
                            Miss::NoRing => self.m.no_ring += 1,
                            Miss::NoneBefore => self.m.none_before += 1,
                            Miss::Stale => self.m.stale += 1,
                            Miss::BadBook => self.m.bad_book += 1,
                        }
                        None
                    }
                };
                (quote, s.ring.latest_arrived(t_ns).map(|(b, a)| classify_direction(px, b, a)))
            }
        };
        (Shadow { quote, arrival, tick }, book)
    }

    pub fn on_quote(&mut self, sym: &str, t_ns: i64, bid: f64, ask: f64) {
        self.m.quotes += 1;
        match self.slots.get_mut(sym) {
            Some(s) => s.ring.push(Quote { t_ns, bid, ask }),
            None => self.m.stray_quotes += 1, // in flight across an unsubscribe
        }
    }

    fn heat_at(&self, sym: &str, now_ns: i64) -> f64 {
        self.contracts.get(sym).map(|c| c.heat * decay(now_ns - c.heat_t)).unwrap_or(0.0)
    }

    /// Re-rank the slots; returns (unsubscribe, subscribe), to be sent in that
    /// order so the server-side set never exceeds QUOTE_CAP. Hysteresis is an
    /// incumbency bonus: incumbents rank at heat × EVICT_RATIO, and one inside
    /// its dwell ranks above everything.
    pub fn rerank(&mut self, now_ns: i64) -> (Vec<String>, Vec<String>) {
        self.m.reranks += 1;
        let today = et_yymmdd(now_ns);
        let mut dropped = Vec::new();
        for k in &self.pool {
            if !self.slots.contains_key(k) && self.heat_at(k, now_ns) < POOL_FLOOR_USD {
                dropped.push(k.clone());
            }
        }
        for k in dropped {
            self.pool.remove(&k);
            if let Some(c) = self.contracts.get_mut(&k) {
                c.cand = false;
            }
        }
        // (effective, incumbent, gauge, sym)
        let mut ranked: Vec<(f64, bool, bool, &str)> = Vec::with_capacity(self.pool.len() + self.slots.len());
        for (k, s) in &self.slots {
            if expiry_yymmdd(k).is_some_and(|e| e < today) {
                continue;
            }
            let eff = if now_ns - s.since_ns < MIN_DWELL_NS { f64::INFINITY } else { self.heat_at(k, now_ns) * EVICT_RATIO };
            ranked.push((eff, true, is_gauge(k), k));
        }
        for k in &self.pool {
            if self.slots.contains_key(k) || expiry_yymmdd(k).is_some_and(|e| e < today) {
                continue;
            }
            let h = self.heat_at(k, now_ns);
            if h > 0.0 {
                ranked.push((h, false, is_gauge(k), k));
            }
        }
        ranked.sort_by(|a, b| b.0.total_cmp(&a.0).then(b.1.cmp(&a.1)).then(a.3.cmp(b.3)));
        let mut take = vec![false; ranked.len()];
        let mut n = 0;
        let mut n_gauge = 0;
        for (i, r) in ranked.iter().enumerate() {
            if r.0.is_infinite() && n < QUOTE_CAP {
                take[i] = true;
                n += 1;
                n_gauge += r.2 as usize;
            }
        }
        for (i, r) in ranked.iter().enumerate() {
            if n_gauge >= GAUGE_RESERVE || n >= QUOTE_CAP {
                break;
            }
            if !take[i] && r.2 {
                take[i] = true;
                n += 1;
                n_gauge += 1;
            }
        }
        for (i, _) in ranked.iter().enumerate() {
            if n >= QUOTE_CAP {
                break;
            }
            if !take[i] {
                take[i] = true;
                n += 1;
            }
        }
        let desired: HashSet<&str> = ranked.iter().zip(&take).filter(|(_, t)| **t).map(|(r, _)| r.3).collect();
        let unsub: Vec<String> = self.slots.keys().filter(|k| !desired.contains(&***k)).map(|k| k.to_string()).collect();
        let sub: Vec<String> = desired.iter().filter(|k| !self.slots.contains_key(**k)).map(|k| k.to_string()).collect();
        for k in &unsub {
            self.slots.remove(k.as_str());
        }
        for k in &sub {
            self.slots.insert(k.as_str().into(), Slot { since_ns: now_ns, ring: Ring::default() });
        }
        debug_assert!(self.slots.len() <= QUOTE_CAP);
        self.m.subs += sub.len() as u64;
        self.m.unsubs += unsub.len() as u64;
        (unsub, sub)
    }

    /// The quoted set with heat → `path` (atomic), so a restart or a new
    /// morning opens with quotes instead of an empty book.
    pub fn save(&self, path: &Path, now_ns: i64) -> std::io::Result<()> {
        let mut rows: Vec<(f64, &str)> = self.slots.keys().map(|k| (self.heat_at(k, now_ns), &**k)).collect();
        rows.sort_by(|a, b| b.0.total_cmp(&a.0).then(a.1.cmp(b.1)));
        let rows: Vec<serde_json::Value> = rows.iter().map(|(h, s)| serde_json::json!({"s": s, "h": (h * 100.0).round() / 100.0})).collect();
        let body = serde_json::json!({"saved_ns": now_ns, "contracts": rows}).to_string();
        let tmp = path.with_extension("json.tmp");
        std::fs::write(&tmp, body)?;
        std::fs::rename(&tmp, path)
    }

    /// Seed the slots from `save`'s file, dropping expired contracts. Heat is
    /// restored at its saved value (not decayed over the closed hours), so the
    /// seeded set survives the open until today's tape out-heats it.
    pub fn seed(&mut self, path: &Path, now_ns: i64) -> std::io::Result<(usize, usize)> {
        let v: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(path)?)?;
        let today = et_yymmdd(now_ns);
        let (mut kept, mut expired) = (0, 0);
        for row in v["contracts"].as_array().into_iter().flatten() {
            let (Some(s), Some(h)) = (row["s"].as_str(), row["h"].as_f64()) else { continue };
            if self.slots.len() >= QUOTE_CAP {
                break;
            }
            match expiry_yymmdd(s) {
                Some(e) if e >= today => {}
                _ => {
                    expired += 1;
                    continue;
                }
            }
            self.contracts.insert(s.into(), Contract { heat: h, heat_t: now_ns, cand: true, last_cents: 0, tick: Dir::Undetermined });
            self.pool.insert(s.into());
            self.slots.insert(s.into(), Slot { since_ns: now_ns, ring: Ring::default() });
            kept += 1;
        }
        Ok((kept, expired))
    }

    pub fn json(&self) -> serde_json::Value {
        let m = &self.m;
        serde_json::json!({
            "set": self.slots.len(), "gauge_set": self.slots.keys().filter(|k| is_gauge(k)).count(),
            "pool": self.pool.len(), "contracts": self.contracts.len(),
            "reranks": m.reranks, "subs": m.subs, "unsubs": m.unsubs, "server_errors": m.server_errors,
            "acked_quotes": m.acked_quotes, "quotes": m.quotes, "stray_quotes": m.stray_quotes,
            "pretrade": { "usable": m.usable, "no_ring": m.no_ring, "none_before": m.none_before, "stale": m.stale, "bad_book": m.bad_book },
        })
    }

    /// The 60 s stats-line tail: churn since the last line + pre-trade outcomes.
    pub fn line(&mut self) -> String {
        let (m, l) = (&self.m, &self.last_line);
        let quoted = (m.usable + m.none_before + m.stale + m.bad_book).max(1) as f64;
        let s = format!(
            "quotes: set={} gauge={} pool={} subs+={} unsubs+={} q_msgs+={} stray+={} err={} acked={} | pretrade usable={} none_before={:.1}% stale={:.1}% bad_book={:.1}% unquoted={}",
            self.slots.len(), self.slots.keys().filter(|k| is_gauge(k)).count(), self.pool.len(),
            m.subs - l.subs, m.unsubs - l.unsubs, m.quotes - l.quotes, m.stray_quotes - l.stray_quotes,
            m.server_errors, m.acked_quotes.map(|n| n.to_string()).unwrap_or_else(|| "-".into()),
            m.usable, 100.0 * m.none_before as f64 / quoted, 100.0 * m.stale as f64 / quoted, 100.0 * m.bad_book as f64 / quoted, m.no_ring,
        );
        self.last_line = self.m.clone();
        s
    }
}

// --- the comparison against tcbbo ----------------------------------------------------------------

#[derive(Default, Clone, Copy, Debug, PartialEq)]
pub struct Cell {
    pub n: u64,
    pub usd: f64,
}

impl Cell {
    fn add(&mut self, usd: f64) {
        self.n += 1;
        self.usd += usd;
    }

    fn json(&self) -> serde_json::Value {
        serde_json::json!([self.n, self.usd.round()])
    }
}

/// One premium bucket. The direction cells count only prints where BOTH sides
/// exist: paired (an Alpaca twin by the cycle) and dbn determined.
#[derive(Default, Clone, Debug)]
pub struct Bucket {
    pub prints: Cell,
    pub paired: Cell,
    pub covered: Cell, // paired + a usable pre-trade Alpaca quote
    pub dbn_det: Cell,
    /// [agree, disagree, alpaca undetermined, no usable quote]
    pub quote: [Cell; 4],
    pub arrival: [Cell; 4],
    /// [agree, disagree, undetermined]
    pub tick: [Cell; 3],
}

fn score(cells: &mut [Cell], ours: Option<Dir>, dbn: Dir, usd: f64) {
    let i = match ours {
        None => 3,
        Some(Dir::Undetermined) => 2,
        Some(d) if d == dbn => 0,
        Some(_) => 1,
    };
    cells[i].add(usd);
}

fn pct(a: f64, b: f64) -> f64 {
    if b > 0.0 {
        (1000.0 * a / b).round() / 10.0
    } else {
        0.0
    }
}

/// Cumulative-for-day shadow scores (the runner adds after stamping dbn).
#[derive(Default, Clone, Debug)]
pub struct ShadowStats {
    pub day: String,
    pub b: [Bucket; 4],
    /// Matched shadows no cycled print claimed: the twin arrived after its
    /// print was cycled, or the print was meta-gated / off-session.
    pub unclaimed: u64,
}

impl ShadowStats {
    pub fn roll(&mut self, day: &str) {
        if self.day != day {
            *self = Self { day: day.to_string(), ..Default::default() };
        }
    }

    pub fn add(&mut self, dbn: Dir, usd: f64, shadow: Option<Shadow>) {
        let i = BUCKET_EDGES_USD.iter().take_while(|e| usd >= **e).count();
        let b = &mut self.b[i];
        b.prints.add(usd);
        let Some(s) = shadow else { return };
        b.paired.add(usd);
        if s.quote.is_some() {
            b.covered.add(usd);
        }
        if dbn == Dir::Undetermined {
            return;
        }
        b.dbn_det.add(usd);
        score(&mut b.quote, s.quote, dbn, usd);
        score(&mut b.arrival, s.arrival, dbn, usd);
        score(&mut b.tick[..], Some(s.tick), dbn, usd);
    }

    pub fn json(&self) -> serde_json::Value {
        let mut out = serde_json::Map::new();
        for (label, b) in BUCKET_LABELS.iter().zip(&self.b) {
            let arr = |cs: &[Cell]| serde_json::Value::Array(cs.iter().map(Cell::json).collect());
            out.insert(
                label.to_string(),
                serde_json::json!({
                    "prints": b.prints.json(), "paired": b.paired.json(), "covered": b.covered.json(), "dbn_det": b.dbn_det.json(),
                    "quote": arr(&b.quote), "arrival": arr(&b.arrival), "tick": arr(&b.tick),
                }),
            );
        }
        serde_json::json!({"day": self.day, "unclaimed": self.unclaimed, "cells": "[n, usd]; quote/arrival = [agree, disagree, undet, no_quote], tick = [agree, disagree, undet] on paired dbn-determined prints", "buckets": out})
    }

    /// Per bucket: coverage of paired prints and agreement where both sides
    /// signed, by count / by premium.
    pub fn line(&self) -> String {
        let parts: Vec<String> = BUCKET_LABELS
            .iter()
            .zip(&self.b)
            .map(|(label, b)| {
                let agree = |c: &[Cell]| {
                    (pct(c[0].n as f64, (c[0].n + c[1].n) as f64), pct(c[0].usd, c[0].usd + c[1].usd))
                };
                let undet = |c: &[Cell]| pct(c[2].usd, b.dbn_det.usd);
                let (qn, qd) = agree(&b.quote);
                let (an, ad) = agree(&b.arrival);
                let (tn, td) = agree(&b.tick);
                format!(
                    "{label} n={} paired={:.1}% cov={:.1}%/{:.1}%$ q_agree={qn:.1}%/{qd:.1}%$ q_undet={:.1}%$ arr_agree={an:.1}%/{ad:.1}%$ tick_agree={tn:.1}%/{td:.1}%$ tick_undet={:.1}%$",
                    b.prints.n,
                    pct(b.paired.n as f64, b.prints.n as f64),
                    pct(b.covered.n as f64, b.paired.n as f64),
                    pct(b.covered.usd, b.paired.usd),
                    undet(&b.quote),
                    undet(&b.tick),
                )
            })
            .collect();
        format!("shadow: {} | unclaimed={}", parts.join(" | "), self.unclaimed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const S: i64 = 1_000_000_000;
    // Tue 2026-08-18 10:00 ET
    fn t0() -> i64 {
        New_York.with_ymd_and_hms(2026, 8, 18, 10, 0, 0).unwrap().timestamp_nanos_opt().unwrap()
    }

    impl Ring {
        fn pt(&self, trade_t: i64) -> Result<(f64, f64), Miss> {
            self.pre_trade(trade_t).map(|q| (q.bid, q.ask))
        }
    }

    fn ring(qs: &[(i64, f64, f64)]) -> Ring {
        let mut r = Ring::default();
        for &(t_ns, bid, ask) in qs {
            r.push(Quote { t_ns, bid, ask });
        }
        r
    }

    #[test]
    fn ring_picks_latest_pre_trade_by_timestamp_not_arrival() {
        // the quote the trade caused (t=105) arrived AFTER an older one but before the trade msg
        let r = ring(&[(90, 1.00, 1.10), (100, 1.05, 1.15), (105, 1.10, 1.20)]);
        assert_eq!(r.pt(103), Ok((1.05, 1.15)));
        assert_eq!(r.latest_arrived(103), Some((1.10, 1.20)));
        // out-of-order arrival: max by t, not by position
        let r = ring(&[(100, 1.05, 1.15), (90, 1.00, 1.10)]);
        assert_eq!(r.pt(103), Ok((1.05, 1.15)));
        // equal timestamp is not "before"
        assert_eq!(ring(&[(103, 1.0, 1.1)]).pt(103), Err(Miss::NoneBefore));
        assert_eq!(Ring::default().pt(103), Err(Miss::NoRing));
        // ring keeps only the last RING by arrival
        let r = ring(&[(1, 1.0, 1.1), (200, 1.0, 1.1), (201, 1.0, 1.1), (202, 1.0, 1.1), (203, 1.0, 1.1)]);
        assert_eq!(r.pt(100), Err(Miss::NoneBefore));
    }

    #[test]
    fn ring_rejects_stale_crossed_and_zero_books() {
        assert_eq!(ring(&[(0, 1.0, 1.1)]).pt(STALE_NS + 1), Err(Miss::Stale));
        assert_eq!(ring(&[(0, 1.0, 1.1)]).pt(STALE_NS), Ok((1.0, 1.1)));
        assert_eq!(ring(&[(0, 1.1, 1.0)]).pt(1), Err(Miss::BadBook));
        assert_eq!(ring(&[(0, 1.0, 1.0)]).pt(1), Err(Miss::BadBook));
        assert_eq!(ring(&[(0, 0.0, 0.05)]).pt(1), Err(Miss::BadBook));
        // the latest pre-trade book is the one judged — an older good one does not rescue it
        assert_eq!(ring(&[(0, 1.0, 1.1), (5, 1.2, 1.1)]).pt(10), Err(Miss::BadBook));
        assert_eq!(ring(&[(0, 1.1, 1.0)]).latest_arrived(1), None);
        assert_eq!(ring(&[(STALE_NS + 10, 1.0, 1.1)]).latest_arrived(0), None);
    }

    #[test]
    fn book_signs_trades_with_the_pre_trade_quote() {
        let mut b = QuoteBook::new();
        let t = t0();
        b.slots.insert("SPY260818C00640000".into(), Slot { since_ns: t, ring: Ring::default() });
        b.on_quote("SPY260818C00640000", t, 1.00, 1.10);
        b.on_quote("SPY260818C00640000", t + 2, 1.10, 1.20); // caused by the trade, arrived first
        let (s, q) = b.on_trade("SPY260818C00640000", t + 1, 110, 10);
        assert_eq!(s.quote, Some(Dir::Buy)); // at the pre-trade ask
        assert_eq!(s.arrival, Some(Dir::Sell)); // at the post-trade bid
        assert_eq!(q, Some(Quote { t_ns: t, bid: 1.00, ask: 1.10 }));
        assert_eq!(b.on_trade("QQQ260818C00500000", t, 100, 1), (Shadow::default(), None));
        assert_eq!((b.m.usable, b.m.no_ring), (1, 1));
    }

    #[test]
    fn tick_test_inherits_zero_ticks_and_resets_daily() {
        let mut b = QuoteBook::new();
        let sym = "SPY260821C00640000";
        let t = t0();
        let ticks: Vec<Dir> = [100, 100, 105, 105, 103, 103, 103, 110].iter().enumerate().map(|(i, c)| b.on_trade(sym, t + i as i64, *c, 1).0.tick).collect();
        use Dir::*;
        assert_eq!(ticks, vec![Undetermined, Undetermined, Buy, Buy, Sell, Sell, Sell, Buy]);
        // per contract
        assert_eq!(b.on_trade("SPY260821P00600000", t + 10, 50, 1).0.tick, Undetermined);
        // next ET day: first print undetermined again, then normal
        let t1 = t + 24 * 3600 * S;
        assert_eq!(b.on_trade(sym, t1, 120, 1).0.tick, Undetermined);
        assert_eq!(b.on_trade(sym, t1 + 1, 120, 1).0.tick, Undetermined);
        assert_eq!(b.on_trade(sym, t1 + 2, 115, 1).0.tick, Sell);
        // a late print stamped yesterday does not roll the day back
        let _ = b.on_trade(sym, t + 3600 * S, 200, 1);
        assert_eq!(b.on_trade(sym, t1 + 3, 115, 1).0.tick, Sell);
    }

    fn heat(b: &mut QuoteBook, sym: &str, t: i64, usd: i64) {
        b.on_trade(sym, t, usd / 10, 10); // cents × size = usd
    }

    fn sym(root: &str, i: usize) -> String {
        format!("{root}260918C{:08}", 100_000 + i)
    }

    #[test]
    fn allocator_caps_diffs_and_reserves_gauge_slots() {
        let mut b = QuoteBook::new();
        let t = t0();
        // 1,200 hot single names + 400 gauge contracts, all candidates
        for i in 0..1200 {
            heat(&mut b, &sym("ABC", i), t, 1_000_000 + i as i64 * 1_000);
        }
        for i in 0..400 {
            heat(&mut b, &sym("SPY", i), t, 30_000);
        }
        heat(&mut b, &sym("XYZ", 0), t, 10_000); // below the candidate line → never quoted
        let (unsub, sub) = b.rerank(t);
        assert!(unsub.is_empty());
        assert_eq!(sub.len(), QUOTE_CAP);
        assert_eq!(b.slots.len(), QUOTE_CAP);
        assert_eq!(b.slots.keys().filter(|k| is_gauge(k)).count(), GAUGE_RESERVE);
        assert!(!b.slots.contains_key(sym("XYZ", 0).as_str()));
        // the 700 general slots went to the hottest names
        assert!(b.slots.contains_key(sym("ABC", 1199).as_str()) && !b.slots.contains_key(sym("ABC", 0).as_str()));
        // nothing changed → empty diff
        let (unsub, sub) = b.rerank(t + RERANK_EVERY_NS);
        assert!(unsub.is_empty() && sub.is_empty());
        assert_eq!((b.m.subs, b.m.unsubs), (1000, 0));
    }

    #[test]
    fn hysteresis_and_dwell() {
        let mut b = QuoteBook::new();
        let t = t0();
        for i in 0..QUOTE_CAP {
            heat(&mut b, &sym("ABC", i), t, 100_000);
        }
        b.rerank(t);
        // a newcomer at 1.4× can't evict anyone; at 2× it can — but only after the dwell
        heat(&mut b, &sym("NEW", 0), t, 140_000);
        let (u, s) = b.rerank(t + MIN_DWELL_NS);
        assert!(u.is_empty() && s.is_empty());
        heat(&mut b, &sym("NEW", 1), t + 1, 200_000);
        let (u, s) = b.rerank(t + MIN_DWELL_NS / 2);
        assert!(u.is_empty() && s.is_empty(), "inside the dwell nobody is evictable");
        let (u, s) = b.rerank(t + MIN_DWELL_NS);
        assert_eq!(s, vec![sym("NEW", 1)]);
        assert_eq!(u.len(), 1);
        assert_eq!(b.slots.len(), QUOTE_CAP);
        // an expired incumbent is dropped and its slot refilled
        let mut b = QuoteBook::new();
        let old = "ABC260817C00100000"; // expired the day before t0
        b.slots.insert(old.into(), Slot { since_ns: 0, ring: Ring::default() });
        heat(&mut b, &sym("ABC", 0), t, 50_000);
        let (u, s) = b.rerank(t);
        assert_eq!((u, s), (vec![old.to_string()], vec![sym("ABC", 0)]));
    }

    #[test]
    fn heat_file_round_trip_drops_expired() {
        let mut b = QuoteBook::new();
        let t = t0();
        for i in 0..3 {
            heat(&mut b, &sym("SPY", i), t, 50_000 * (i as i64 + 1));
        }
        b.rerank(t);
        b.slots.insert("SPY260817C00100000".into(), Slot { since_ns: t, ring: Ring::default() });
        let path = std::env::temp_dir().join(format!("flow_quote_heat_test_{}.json", std::process::id()));
        b.save(&path, t).unwrap();
        let mut b2 = QuoteBook::new();
        let (kept, expired) = b2.seed(&path, t + 3600 * S).unwrap();
        assert_eq!((kept, expired), (3, 1));
        assert_eq!(b2.slots.len(), 3);
        assert!((b2.heat_at(&sym("SPY", 2), t + 3600 * S) - 150_000.0).abs() < 1.0);
        // seeded slots are subscribed on connect and survive a quiet re-rank
        let (u, s) = b2.rerank(t + 3600 * S + MIN_DWELL_NS);
        assert!(u.is_empty() && s.is_empty());
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn shadow_stats_bucket_and_score() {
        use Dir::*;
        let mut st = ShadowStats::default();
        st.roll("2026-08-18");
        st.add(Buy, 300_000.0, Some(Shadow { quote: Some(Buy), arrival: Some(Sell), tick: Undetermined }));
        st.add(Sell, 50_000.0, Some(Shadow { quote: None, arrival: None, tick: Sell }));
        st.add(Undetermined, 10_000.0, Some(Shadow::default()));
        st.add(Buy, 10_000.0, None);
        let b3 = &st.b[3];
        assert_eq!((b3.quote[0].n, b3.arrival[1].n, b3.tick[2].n, b3.covered.n), (1, 1, 1, 1));
        let b1 = &st.b[1];
        assert_eq!((b1.quote[3].n, b1.tick[0].n, b1.covered.n), (1, 1, 0));
        assert_eq!((st.b[0].prints.n, st.b[0].paired.n, st.b[0].dbn_det.n), (2, 1, 0));
        st.roll("2026-08-19");
        assert_eq!(st.b[3].prints.n, 0);
    }
}
