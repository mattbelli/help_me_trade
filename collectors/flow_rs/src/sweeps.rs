//! `signals/options_flow/sweeps.py` — SWP-1 clustering, ported exactly
//! (per-occ buckets in first-seen order, stable sort by ts_ms on every
//! `close_ready`, window 500 ms + grace 1000 ms, ≥3 exchanges or split ≥5 prints).

use crate::intraday::{
    caveat_for_direction, expiry_month, fmt_premium, fmt_strike, right_letter, ts_ms_to_iso, Event,
};
use crate::print::{is_index_megacap, Dir, Print};
use crate::pyfmt::{int_commas, py_sum};
use std::collections::BTreeSet;

pub const SWEEP_WINDOW_MS: i64 = 500;
pub const SWEEP_MIN_EXCHANGES: usize = 3;
pub const SPLIT_MIN_PRINTS: usize = 5;
pub const SWEEP_CLOSE_GRACE_MS: i64 = 1_000;
pub const MIN_PREMIUM_USD: f64 = 25_000.0;
pub const MIN_PREMIUM_INDEX_USD: f64 = 250_000.0;
pub const PRESSURE_MIN_CLASSIFIED: f64 = 0.5;
const PER_OCC_CAP: usize = 500;

fn eligible(p: &Print) -> bool {
    if p.spread_tagged() || p.multileg() || p.hygiene_excluded() {
        return false;
    }
    if is_index_megacap(&p.underlying) && p.dte <= 1 {
        return false;
    }
    p.size > 0 && p.price > 0.0
}

fn cluster_direction(prints: &[&Print], premium: f64) -> (Dir, &'static str) {
    let (mut buy, mut sell) = (0.0f64, 0.0f64);
    let mut basis = "none";
    for p in prints {
        let prem = p.premium();
        match p.direction {
            Dir::Buy => buy += prem,
            Dir::Sell => sell += prem,
            Dir::Undetermined => continue,
        }
        if basis == "none" {
            basis = p.basis;
        }
    }
    let classified = buy + sell;
    if premium <= 0.0 || classified / premium < PRESSURE_MIN_CLASSIFIED {
        return (Dir::Undetermined, "none");
    }
    (if buy >= sell { Dir::Buy } else { Dir::Sell }, basis)
}

fn cluster_event(cluster: &[&Print], event_date: &str) -> Option<Event> {
    let exchanges: BTreeSet<&str> = cluster
        .iter()
        .map(|p| p.exchange.as_str())
        .filter(|e| !e.is_empty())
        .collect();
    let n_prints = cluster.len();
    let is_sweep = exchanges.len() >= SWEEP_MIN_EXCHANGES;
    let is_split = exchanges.len() <= 1 && n_prints >= SPLIT_MIN_PRINTS;
    if !(is_sweep || is_split) {
        return None;
    }
    let first = cluster[0];
    let contracts: i64 = cluster.iter().map(|p| p.size).sum();
    let premium = py_sum(cluster.iter().map(|p| p.premium())); // Python sum() → compensated
    let floor = if is_index_megacap(&first.underlying) {
        MIN_PREMIUM_INDEX_USD
    } else {
        MIN_PREMIUM_USD
    };
    if premium < floor {
        return None;
    }
    let iso_flagged = cluster.iter().any(|p| p.iso_flagged());
    let (direction, basis) = cluster_direction(cluster, premium);
    let anchor = first.ts_ms;
    let shape = if exchanges.len() >= SWEEP_MIN_EXCHANGES {
        format!("sweep across {} exchanges", exchanges.len())
    } else {
        "split order on one exchange".to_string()
    };
    let reason = format!(
        "{} {} {}{}: {} — {} contracts, {} premium, {} ({} prints{})",
        first.underlying,
        expiry_month(&first.expiry),
        fmt_strike(first.strike),
        right_letter(first.right),
        shape,
        int_commas(contracts),
        fmt_premium(premium),
        direction.as_str(),
        n_prints,
        if iso_flagged { ", ISO-flagged" } else { "" },
    );
    Some(Event {
        id: format!(
            "of_{}_{}_{}_swp_{}",
            event_date, first.underlying, first.occ, anchor
        ),
        ts: ts_ms_to_iso(anchor),
        underlying: first.underlying.clone(),
        occ_symbol: first.occ.clone(),
        right: Some(first.right),
        strike: Some(first.strike),
        expiry: Some(first.expiry.clone()),
        dte: Some(first.dte),
        event_type: "sweep",
        contracts: Some(contracts),
        premium_usd: premium,
        direction,
        direction_basis: basis.to_string(),
        mid_side: None,
        n_prints: n_prints as i64,
        exchanges: exchanges.into_iter().map(str::to_string).collect(),
        reason,
        caveat: caveat_for_direction(direction).to_string(),
        detail: None,
    })
}

#[derive(Default)]
pub struct SweepBuffer {
    /// occ → prints, in first-seen occ order (Python dict semantics).
    by_occ: Vec<(String, Vec<Print>)>,
    index: std::collections::HashMap<String, usize>,
}

impl SweepBuffer {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add(&mut self, prints: &[Print]) {
        for p in prints {
            if !eligible(p) || p.occ.is_empty() || p.ts_ms == 0 {
                continue;
            }
            let i = match self.index.get(&p.occ) {
                Some(&i) => i,
                None => {
                    self.index.insert(p.occ.clone(), self.by_occ.len());
                    self.by_occ.push((p.occ.clone(), Vec::new()));
                    self.by_occ.len() - 1
                }
            };
            let bucket = &mut self.by_occ[i].1;
            if bucket.len() < PER_OCC_CAP {
                bucket.push(p.clone());
            }
        }
    }

    pub fn close_ready(&mut self, now_ms: i64, event_date: &str) -> Vec<Event> {
        let mut events = Vec::new();
        let mut kept: Vec<(String, Vec<Print>)> = Vec::with_capacity(self.by_occ.len());
        for (occ, mut bucket) in std::mem::take(&mut self.by_occ) {
            bucket.sort_by_key(|p| p.ts_ms); // stable, persists (Python sorts in place)
            let n = bucket.len();
            let mut remaining: Vec<Print> = Vec::new();
            let mut i = 0;
            while i < n {
                let anchor = bucket[i].ts_ms;
                let mut j = i;
                while j < n && bucket[j].ts_ms - anchor <= SWEEP_WINDOW_MS {
                    j += 1;
                }
                let cluster: Vec<&Print> = bucket[i..j].iter().filter(|p| eligible(p)).collect();
                if now_ms - anchor > SWEEP_WINDOW_MS + SWEEP_CLOSE_GRACE_MS {
                    if !cluster.is_empty() {
                        if let Some(ev) = cluster_event(&cluster, event_date) {
                            events.push(ev);
                        }
                    }
                } else {
                    remaining.extend_from_slice(&bucket[i..]);
                    break;
                }
                i = j;
            }
            if !remaining.is_empty() {
                kept.push((occ, remaining));
            }
        }
        self.index.clear();
        for (i, (occ, _)) in kept.iter().enumerate() {
            self.index.insert(occ.clone(), i);
        }
        self.by_occ = kept;
        events
    }
}
