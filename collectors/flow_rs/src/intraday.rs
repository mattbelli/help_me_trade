//! `signals/options_flow/intraday.py` + `detect.py` formatting helpers, ported
//! line for line: `tag_likely_spreads`, `classify_direction`, `classify_midpoint`,
//! `detect_block`, `_fmt_premium` / `_fmt_strike` / `_expiry_month` /
//! `_right_letter`, `_ts_ms_to_iso`, the caveat templates.

use crate::print::{is_index_megacap, Dir, Print};
use crate::pyfmt::{fixed, int_commas, py_g6};
use std::collections::HashMap;

pub const QUOTE_EPS: f64 = 0.01;
pub const MID_SPREAD_MAX_FRAC: f64 = 0.50;
pub const SPREAD_CLUSTER_WINDOW_MS: i64 = 1000;
pub const SPREAD_CLUSTER_MIN_STRIKES: usize = 2;
pub const BLOCK_MIN_CONTRACTS: i64 = 250;
pub const BLOCK_MIN_PREMIUM_USD: f64 = 100_000.0;
pub const ZERO_DTE_EXCLUDE: bool = true;

pub const CAVEAT_DIRECTION: &str = "direction inferred from quote rule (~83% class accuracy, Savickas-Wilson 2003); cannot distinguish new position vs close vs hedge vs spread leg";
pub const CAVEAT_UNDETERMINED: &str = "cannot distinguish new position vs close vs hedge vs spread leg; direction undetermined (no quote or midpoint print)";

pub fn caveat_for_direction(d: Dir) -> &'static str {
    if d != Dir::Undetermined {
        CAVEAT_DIRECTION
    } else {
        CAVEAT_UNDETERMINED
    }
}

/// `datetime.fromtimestamp(ts_ms/1000, utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{ts_ms % 1000:03d}Z"`
pub fn ts_ms_to_iso(ts_ms: i64) -> String {
    let secs = ts_ms.div_euclid(1000);
    let ms = ts_ms.rem_euclid(1000);
    let dt = chrono::DateTime::from_timestamp(secs, 0).expect("ts in range");
    format!("{}.{:03}Z", dt.format("%Y-%m-%dT%H:%M:%S"), ms)
}

pub fn fmt_premium(premium: f64) -> String {
    if premium >= 1_000_000.0 {
        format!("${}M", fixed(premium / 1_000_000.0, 1))
    } else if premium >= 1_000.0 {
        format!("${}k", fixed(premium / 1_000.0, 1))
    } else {
        format!("${}", fixed(premium, 0))
    }
}

pub fn fmt_strike(strike: f64) -> String {
    if strike == strike.trunc() {
        format!("${}", strike.trunc() as i64)
    } else {
        format!("${}", py_g6(strike))
    }
}

const MONTHS: [&str; 12] = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

pub fn expiry_month(expiry: &str) -> &'static str {
    expiry
        .get(5..7)
        .and_then(|s| s.parse::<usize>().ok())
        .filter(|m| (1..=12).contains(m))
        .map(|m| MONTHS[m - 1])
        .unwrap_or("???")
}

pub fn right_letter(right: &str) -> &'static str {
    let r = right.trim().to_ascii_lowercase();
    if r.starts_with('c') {
        "C"
    } else if r.starts_with('p') {
        "P"
    } else {
        "?"
    }
}

pub fn classify_direction(price: f64, bid: f64, ask: f64) -> Dir {
    if bid <= 0.0 || ask <= 0.0 || bid >= ask {
        return Dir::Undetermined;
    }
    if price >= ask - QUOTE_EPS {
        Dir::Buy
    } else if price <= bid + QUOTE_EPS {
        Dir::Sell
    } else {
        Dir::Undetermined
    }
}

pub fn classify_midpoint(price: f64, bid: f64, ask: f64) -> &'static str {
    if bid <= 0.0 || ask <= 0.0 || ask <= bid {
        return "unclassified";
    }
    let mid = (bid + ask) / 2.0;
    if (ask - bid) > MID_SPREAD_MAX_FRAC * mid {
        return "wide_spread";
    }
    if price > mid {
        "buy"
    } else if price < mid {
        "sell"
    } else {
        "unclassified"
    }
}

/// F-2 clustering, in place. Groups by underlying (any order — only flags are
/// set), stable-sorts each group by `ts_ms`, and for every anchor looks forward
/// ≤ 1000 ms: prints sharing a clip size across ≥ 2 strikes are `likely_spread`.
pub fn tag_likely_spreads(batch: &mut [Print]) {
    for p in batch.iter_mut() {
        if p.multileg() {
            p.spread_leg = true;
        }
    }
    // Group in batch order (Python appends in batch order, then stable-sorts).
    let mut idx_of: HashMap<String, usize> = HashMap::new();
    let mut groups: Vec<Vec<usize>> = Vec::new();
    for (i, p) in batch.iter().enumerate() {
        let g = match idx_of.get(&p.underlying) {
            Some(&g) => g,
            None => {
                idx_of.insert(p.underlying.clone(), groups.len());
                groups.push(Vec::new());
                groups.len() - 1
            }
        };
        groups[g].push(i);
    }
    let mut flags: Vec<bool> = vec![false; batch.len()];
    for mut group in groups {
        group.sort_by_key(|&i| batch[i].ts_ms); // stable
        let n = group.len();
        for i in 0..n {
            let anchor = batch[group[i]].ts_ms;
            let mut window: Vec<usize> = Vec::new();
            for &gj in &group[i..] {
                if batch[gj].ts_ms - anchor > SPREAD_CLUSTER_WINDOW_MS {
                    break;
                }
                window.push(gj);
            }
            if window.len() < 2 {
                continue;
            }
            // by_size: dict[int, list] in first-seen order (order irrelevant to the flags)
            let mut by_size: Vec<(i64, Vec<usize>)> = Vec::new();
            for &w in &window {
                let sz = batch[w].size;
                if sz > 0 {
                    match by_size.iter_mut().find(|(s, _)| *s == sz) {
                        Some((_, v)) => v.push(w),
                        None => by_size.push((sz, vec![w])),
                    }
                }
            }
            for (_, members) in by_size {
                let mut strikes: Vec<u64> =
                    members.iter().map(|&m| batch[m].strike.to_bits()).collect();
                strikes.sort_unstable();
                strikes.dedup();
                if strikes.len() >= SPREAD_CLUSTER_MIN_STRIKES {
                    for m in members {
                        flags[m] = true;
                    }
                }
            }
        }
    }
    for (p, f) in batch.iter_mut().zip(flags) {
        if f {
            p.likely_spread = true;
        }
    }
}

/// A §6 event row as `db/options_flow.insert_event` binds it (only the columns
/// this lane writes; `detail` is JSON text or None).
#[derive(Clone, Debug)]
pub struct Event {
    pub id: String,
    pub ts: String,
    pub underlying: String,
    pub occ_symbol: String,
    pub right: Option<&'static str>,
    pub strike: Option<f64>,
    pub expiry: Option<String>,
    pub dte: Option<i64>,
    pub event_type: &'static str,
    pub contracts: Option<i64>,
    pub premium_usd: f64,
    pub direction: Dir,
    pub direction_basis: String,
    pub mid_side: Option<&'static str>,
    pub n_prints: i64,
    pub exchanges: Vec<String>,
    pub reason: String,
    pub caveat: String,
    pub detail: Option<String>,
}

pub fn detect_block(p: &Print, event_date: &str) -> Option<Event> {
    if ZERO_DTE_EXCLUDE && is_index_megacap(&p.underlying) && p.dte <= 1 {
        return None;
    }
    if p.spread_tagged() || p.multileg() || p.hygiene_excluded() {
        return None;
    }
    if p.size < BLOCK_MIN_CONTRACTS || p.price <= 0.0 {
        return None;
    }
    let premium = p.premium();
    if premium < BLOCK_MIN_PREMIUM_USD {
        return None;
    }
    let reason = format!(
        "{} {} {}{}: block {} contracts, {} premium, {} (1 print)",
        p.underlying,
        expiry_month(&p.expiry),
        fmt_strike(p.strike),
        right_letter(p.right),
        int_commas(p.size),
        fmt_premium(premium),
        p.direction.as_str(),
    );
    Some(Event {
        id: format!(
            "of_{}_{}_{}_blk_{}",
            event_date, p.underlying, p.occ, p.ts_ms
        ),
        ts: ts_ms_to_iso(p.ts_ms),
        underlying: p.underlying.clone(),
        occ_symbol: p.occ.clone(),
        right: Some(p.right),
        strike: Some(p.strike),
        expiry: Some(p.expiry.clone()),
        dte: Some(p.dte),
        event_type: "block",
        contracts: Some(p.size),
        premium_usd: premium,
        direction: p.direction,
        direction_basis: p.basis.to_string(),
        mid_side: Some(p.mid_side),
        n_prints: 1,
        exchanges: if p.exchange.is_empty() {
            vec![]
        } else {
            vec![p.exchange.clone()]
        },
        reason,
        caveat: caveat_for_direction(p.direction).to_string(),
        detail: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fmt() {
        assert_eq!(fmt_premium(1_500_000.0), "$1.5M");
        assert_eq!(fmt_premium(25_050.0), "$25.1k");
        assert_eq!(fmt_premium(999.4), "$999");
        assert_eq!(fmt_strike(50.0), "$50");
        assert_eq!(fmt_strike(52.5), "$52.5");
        assert_eq!(expiry_month("2026-12-18"), "Dec");
        assert_eq!(ts_ms_to_iso(1786973401328), "2026-08-17T13:30:01.328Z");
    }
}
