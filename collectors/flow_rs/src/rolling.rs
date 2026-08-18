//! `signals/options_flow/rolling.py` — D-1 bursts + D-2 clusters. Every sum
//! runs in Python's iteration order (bucket dict = minute first-seen order),
//! because floating-point sums are order-dependent and the A/B compares them.

use crate::intraday::{ts_ms_to_iso, Event};
use crate::print::{is_index_megacap, Dir, Print};
use crate::pyfmt::{fixed, py_int, py_round0, py_sum, round_nd};
use std::collections::HashMap;

pub const BUCKET_MS: i64 = 60_000;
pub const WINDOW_BUCKETS: i64 = 30;
pub const BURST_SIDED_MIN_USD: f64 = 1_000_000.0;
pub const BURST_SIDED_MIN_CLASSIFIED: f64 = 0.5;
pub const BURST_UNSIGNED_MIN_USD: f64 = 3_000_000.0;
pub const BURST_INDEX_MIN_USD: f64 = 5_000_000.0;
pub const BURST_REARM_FACTOR: f64 = 1.5;
pub const CLUSTER_PRINT_MAX_USD: f64 = 100_000.0;
pub const CLUSTER_PRINT_MIN_USD: f64 = 2_000.0;
pub const CLUSTER_MIN_AGG_USD: f64 = 500_000.0;
pub const CLUSTER_INDEX_MIN_AGG_USD: f64 = 2_000_000.0;
pub const CLUSTER_MIN_PRINTS: i64 = 25;
pub const CLUSTER_REARM_FACTOR: f64 = 2.0;
const TS_CAP: usize = 400;

fn fmt_usd(v: f64) -> String {
    if v >= 1_000_000.0 {
        format!("${}M", fixed(v / 1_000_000.0, 1))
    } else {
        format!("${}k", fixed(v / 1_000.0, 0))
    }
}

#[derive(Default)]
struct SmallSide {
    prem: f64,
    n: i64,
    buy_prem: f64,
    sell_prem: f64,
    ts_ms: Vec<i64>,
    sizes: Vec<(i64, i64)>,        // clip size → count, first-seen order
    contracts: Vec<(String, f64)>, // occ → prem, first-seen order
}

#[derive(Default)]
struct Bucket {
    gross: f64,
    buy: f64,
    sell: f64,
    call_net: f64,
    put_net: f64,
    n_prints: i64,
    small: Vec<(char, SmallSide)>, // right → side, first-seen order
}

#[derive(Default)]
struct Sym {
    buckets: Vec<(i64, Bucket)>, // minute → bucket, first-seen order
}

#[derive(Default)]
pub struct RollingFlow {
    syms: Vec<(String, Sym)>,
    index: HashMap<String, usize>,
    burst_fired: HashMap<String, f64>,
    cluster_fired: HashMap<(String, char), f64>,
    classified_basis: &'static str,
}

impl RollingFlow {
    pub fn new() -> Self {
        Self {
            classified_basis: "none",
            ..Default::default()
        }
    }

    pub fn add_print(&mut self, p: &Print) {
        if p.hygiene_excluded() {
            return;
        }
        if p.price <= 0.0 || p.size <= 0 {
            return;
        }
        let prem = p.price * p.size as f64 * 100.0;
        if prem < CLUSTER_PRINT_MIN_USD {
            return;
        }
        if p.spread_tagged() {
            return;
        }
        if p.underlying.is_empty() {
            return;
        }
        if is_index_megacap(&p.underlying) && p.dte <= 1 {
            return;
        }
        let minute = p.ts_ms.div_euclid(BUCKET_MS);
        let si = match self.index.get(&p.underlying) {
            Some(&i) => i,
            None => {
                self.index.insert(p.underlying.clone(), self.syms.len());
                self.syms.push((p.underlying.clone(), Sym::default()));
                self.syms.len() - 1
            }
        };
        let sym = &mut self.syms[si].1;
        let bi = match sym.buckets.iter().position(|(m, _)| *m == minute) {
            Some(i) => i,
            None => {
                sym.buckets.push((minute, Bucket::default()));
                // _evict: drop minutes < now_minute - WINDOW_BUCKETS - 1 (called only on a new bucket)
                sym.buckets
                    .retain(|(m, _)| *m >= minute - WINDOW_BUCKETS - 1);
                sym.buckets.iter().position(|(m, _)| *m == minute).unwrap()
            }
        };
        let b = &mut sym.buckets[bi].1;
        let right = p.right_letter();
        if p.direction != Dir::Undetermined {
            self.classified_basis = p.basis;
        }
        b.gross += prem;
        b.n_prints += 1;
        match p.direction {
            Dir::Buy => {
                b.buy += prem;
                if right == 'c' {
                    b.call_net += prem
                } else if right == 'p' {
                    b.put_net += prem
                }
            }
            Dir::Sell => {
                b.sell += prem;
                if right == 'c' {
                    b.call_net -= prem
                } else if right == 'p' {
                    b.put_net -= prem
                }
            }
            Dir::Undetermined => {}
        }
        if prem < CLUSTER_PRINT_MAX_USD {
            let s = match b.small.iter().position(|(r, _)| *r == right) {
                Some(i) => &mut b.small[i].1,
                None => {
                    b.small.push((right, SmallSide::default()));
                    &mut b.small.last_mut().unwrap().1
                }
            };
            s.prem += prem;
            s.n += 1;
            match p.direction {
                Dir::Buy => s.buy_prem += prem,
                Dir::Sell => s.sell_prem += prem,
                Dir::Undetermined => {}
            }
            if s.ts_ms.len() < TS_CAP {
                s.ts_ms.push(p.ts_ms);
            }
            match s.sizes.iter_mut().find(|(k, _)| *k == p.size) {
                Some((_, c)) => *c += 1,
                None => s.sizes.push((p.size, 1)),
            }
            if !p.occ.is_empty() {
                match s.contracts.iter_mut().find(|(k, _)| k == &p.occ) {
                    Some((_, v)) => *v += prem,
                    None => s.contracts.push((p.occ.clone(), prem)),
                }
            }
        }
    }

    fn window(sym: &Sym, now_ms: i64) -> Vec<&Bucket> {
        let now_minute = now_ms.div_euclid(BUCKET_MS);
        sym.buckets
            .iter()
            .filter(|(m, _)| now_minute - WINDOW_BUCKETS < *m && *m <= now_minute)
            .map(|(_, b)| b)
            .collect()
    }

    fn event_date(now_ms: i64) -> String {
        chrono::DateTime::from_timestamp(now_ms.div_euclid(1000), 0)
            .unwrap()
            .format("%Y-%m-%d")
            .to_string()
    }

    pub fn check_bursts(&mut self, now_ms: i64) -> Vec<Event> {
        let mut out = Vec::new();
        let event_date = Self::event_date(now_ms);
        for (sym_name, sym) in &self.syms {
            let win = Self::window(sym, now_ms);
            if win.is_empty() {
                continue;
            }
            let gross: f64 = py_sum(win.iter().map(|b| b.gross));
            if gross <= 0.0 {
                continue;
            }
            let buy: f64 = py_sum(win.iter().map(|b| b.buy));
            let sell: f64 = py_sum(win.iter().map(|b| b.sell));
            let call_net: f64 = py_sum(win.iter().map(|b| b.call_net));
            let put_net: f64 = py_sum(win.iter().map(|b| b.put_net));
            let n_prints: i64 = win.iter().map(|b| b.n_prints).sum();
            let classified = buy + sell;
            let classified_frac = classified / gross;
            let net = buy - sell;
            let is_index = is_index_megacap(sym_name);
            let sided_floor = if is_index {
                BURST_INDEX_MIN_USD
            } else {
                BURST_SIDED_MIN_USD
            };
            let sided = classified_frac >= BURST_SIDED_MIN_CLASSIFIED && net.abs() >= sided_floor;
            let unsigned = !is_index
                && classified_frac < BURST_SIDED_MIN_CLASSIFIED
                && gross >= BURST_UNSIGNED_MIN_USD;
            if !(sided || unsigned) {
                continue;
            }
            let magnitude = if sided { net.abs() } else { gross };
            if let Some(last) = self.burst_fired.get(sym_name) {
                if magnitude < last * BURST_REARM_FACTOR {
                    continue;
                }
            }
            self.burst_fired.insert(sym_name.clone(), magnitude);
            let (side, reason, caveat) = if sided {
                let side = if net > 0.0 { Dir::Buy } else { Dir::Sell };
                let word = if net > 0.0 { "buy-side" } else { "sell-side" };
                (
                    side,
                    format!(
                        "{}: {} net {} options premium in 30 min ({} prints, {}% classified; calls {} {}, puts {} {})",
                        sym_name, fmt_usd(net.abs()), word, n_prints, py_int(classified_frac * 100.0),
                        fmt_usd(call_net.abs()), if call_net >= 0.0 { "+" } else { "-" },
                        fmt_usd(put_net.abs()), if put_net >= 0.0 { "+" } else { "-" },
                    ),
                    format!(
                        "30-min rolling net of side-classified premium; {}% of premium had no quote to classify against. Facts about the tape, not a signal.",
                        py_int((1.0 - classified_frac) * 100.0)
                    ),
                )
            } else {
                (
                    Dir::Undetermined,
                    format!(
                        "{}: {} gross options premium in 30 min ({} prints) with only {}% side-classified",
                        sym_name, fmt_usd(gross), n_prints, py_int(classified_frac * 100.0)
                    ),
                    "UNSIGNED burst — premium is flowing but aggressor side is unknown (no quote coverage yet); side may resolve if quote escalation catches later prints. Facts, not a signal.".to_string(),
                )
            };
            let detail = serde_json::json!({
                "window_min": 30,
                "gross_premium": round_nd(gross, 2),
                "net_premium": round_nd(net, 2),
                "classified_frac": round_nd(classified_frac, 4),
                "call_net": round_nd(call_net, 2),
                "put_net": round_nd(put_net, 2),
            });
            out.push(Event {
                id: format!(
                    "of_{}_{}_burst_{}",
                    event_date,
                    sym_name,
                    now_ms.div_euclid(BUCKET_MS)
                ),
                ts: ts_ms_to_iso(now_ms),
                underlying: sym_name.clone(),
                occ_symbol: String::new(),
                right: None,
                strike: None,
                expiry: None,
                dte: None,
                event_type: "flow_burst",
                contracts: None,
                premium_usd: round_nd(magnitude, 2),
                direction: side,
                direction_basis: (if sided { self.classified_basis } else { "none" }).to_string(),
                mid_side: None,
                n_prints,
                exchanges: vec![],
                reason,
                caveat,
                detail: Some(detail.to_string()),
            });
        }
        out
    }

    pub fn check_clusters(&mut self, now_ms: i64) -> Vec<Event> {
        let mut out = Vec::new();
        let event_date = Self::event_date(now_ms);
        for (sym_name, sym) in &self.syms {
            let win = Self::window(sym, now_ms);
            if win.is_empty() {
                continue;
            }
            let is_index = is_index_megacap(sym_name);
            let floor = if is_index {
                CLUSTER_INDEX_MIN_AGG_USD
            } else {
                CLUSTER_MIN_AGG_USD
            };
            for right in ['c', 'p'] {
                let mut agg = 0.0f64;
                let mut n = 0i64;
                let mut buy_prem = 0.0f64;
                let mut sell_prem = 0.0f64;
                let mut ts_all: Vec<i64> = Vec::new();
                let mut sizes: Vec<(i64, i64)> = Vec::new();
                let mut contracts: Vec<(&str, f64)> = Vec::new();
                for b in &win {
                    let Some((_, s)) = b.small.iter().find(|(r, _)| *r == right) else {
                        continue;
                    };
                    agg += s.prem;
                    n += s.n;
                    buy_prem += s.buy_prem;
                    sell_prem += s.sell_prem;
                    ts_all.extend_from_slice(&s.ts_ms);
                    for (k, v) in &s.sizes {
                        match sizes.iter_mut().find(|(kk, _)| kk == k) {
                            Some((_, c)) => *c += v,
                            None => sizes.push((*k, *v)),
                        }
                    }
                    for (k, v) in &s.contracts {
                        match contracts.iter_mut().find(|(kk, _)| kk == k) {
                            Some((_, c)) => *c += v,
                            None => contracts.push((k.as_str(), *v)),
                        }
                    }
                }
                if agg < floor || n < CLUSTER_MIN_PRINTS {
                    continue;
                }
                let key = (sym_name.clone(), right);
                if let Some(last) = self.cluster_fired.get(&key) {
                    if agg < last * CLUSTER_REARM_FACTOR {
                        continue;
                    }
                }
                self.cluster_fired.insert(key, agg);

                let modal_share = if !sizes.is_empty() && n != 0 {
                    sizes.iter().map(|(_, c)| *c).max().unwrap() as f64 / n as f64
                } else {
                    0.0
                };
                let top_contract_share = if !contracts.is_empty() && agg > 0.0 {
                    contracts
                        .iter()
                        .map(|(_, v)| *v)
                        .fold(f64::NEG_INFINITY, f64::max)
                        / agg
                } else {
                    0.0
                };
                let mut cv: Option<f64> = None;
                if ts_all.len() >= 3 {
                    ts_all.sort();
                    let gaps: Vec<i64> = ts_all
                        .windows(2)
                        .filter(|w| w[1] > w[0])
                        .map(|w| w[1] - w[0])
                        .collect();
                    if gaps.len() >= 2 {
                        let mean = gaps.iter().sum::<i64>() as f64 / gaps.len() as f64;
                        if mean > 0.0 {
                            let var = py_sum(gaps.iter().map(|g| (*g as f64 - mean).powf(2.0)))
                                / gaps.len() as f64;
                            cv = Some(var.sqrt() / mean);
                        }
                    }
                }
                let classified = buy_prem + sell_prem;
                let classified_frac = if agg > 0.0 { classified / agg } else { 0.0 };
                let net = buy_prem - sell_prem;
                let (side, basis) = if classified_frac >= 0.5 && net != 0.0 {
                    (
                        if net > 0.0 { Dir::Buy } else { Dir::Sell },
                        self.classified_basis,
                    )
                } else {
                    (Dir::Undetermined, "none")
                };
                let word = if right == 'c' { "calls" } else { "puts" };
                let span_min = if !ts_all.is_empty() {
                    let mx = *ts_all.iter().max().unwrap();
                    let mn = *ts_all.iter().min().unwrap();
                    py_round0((mx - mn) as f64 / 60_000.0).max(1)
                } else {
                    30
                };
                let mut reason = format!(
                    "{}: {} of {} in {} prints < {} over {} min",
                    sym_name,
                    fmt_usd(agg),
                    word,
                    n,
                    fmt_usd(CLUSTER_PRINT_MAX_USD),
                    span_min
                );
                if modal_share >= 0.5 {
                    reason.push_str(&format!(
                        ", {}% at one clip size",
                        py_int(modal_share * 100.0)
                    ));
                }
                let mut caveat = "aggregate of many small prints; cannot distinguish coordinated slicing from a coincident retail crowd".to_string();
                if side != Dir::Undetermined {
                    caveat.push_str(&format!(
                        "; side classified on {}% of premium",
                        py_int(classified_frac * 100.0)
                    ));
                } else {
                    caveat.push_str("; aggressor side unknown");
                }
                caveat.push_str(". Facts, not a signal.");
                let detail = serde_json::json!({
                    "window_min": 30,
                    "classified_frac": round_nd(classified_frac, 4),
                    "net_premium": round_nd(net, 2),
                    "modal_size_share": round_nd(modal_share, 4),
                    "n_unique_sizes": sizes.len(),
                    "interarrival_cv": cv.map(|c| round_nd(c, 4)),
                    "top_contract_share": round_nd(top_contract_share, 4),
                    "n_contracts": contracts.len(),
                });
                out.push(Event {
                    id: format!(
                        "of_{}_{}_{}_cluster_{}",
                        event_date,
                        sym_name,
                        right,
                        now_ms.div_euclid(BUCKET_MS)
                    ),
                    ts: ts_ms_to_iso(now_ms),
                    underlying: sym_name.clone(),
                    occ_symbol: String::new(),
                    right: Some(if right == 'c' { "call" } else { "put" }),
                    strike: None,
                    expiry: None,
                    dte: None,
                    event_type: "flow_cluster",
                    contracts: None,
                    premium_usd: round_nd(agg, 2),
                    direction: side,
                    direction_basis: basis.to_string(),
                    mid_side: None,
                    n_prints: n,
                    exchanges: vec![],
                    reason,
                    caveat,
                    detail: Some(detail.to_string()),
                });
            }
        }
        out
    }
}
