//! `signals/options_flow/opening_pressure.py` — index pressure gauges (open /
//! close windows, 1-min cells, bull/bear/unclassified premium).

use crate::print::{Dir, Print};
use crate::rolling::CLUSTER_PRINT_MIN_USD;
use std::collections::BTreeMap;

pub const WINDOWS: [(&str, i64, i64); 2] = [("open", 9 * 60 + 30, 30), ("close", 15 * 60 + 30, 30)];
pub const GAUGE_UNDERLYINGS: [&str; 13] = [
    "SPY", "QQQ", "IWM", "DIA", "SPX", "SPXW", "TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META",
    "GOOGL",
];

#[derive(Default, Clone)]
pub struct MinuteCell {
    pub bull_usd: f64,
    pub bear_usd: f64,
    pub unclassified_usd: f64,
    pub n_prints: i64,
    pub coverage: Option<String>,
}

pub struct PressureGauge {
    pub window: &'static str,
    start_min: i64,
    length: i64,
    /// sym → minute → cell. Reads sort by sym then minute (`minute_rows`), so
    /// ordered maps are exact here.
    cells: BTreeMap<String, BTreeMap<i64, MinuteCell>>,
}

pub struct WindowStats {
    pub syms: i64,
    pub minutes_observed: i64,
    pub coverage_modes: Option<String>,
}

impl PressureGauge {
    pub fn new(window: &'static str) -> Self {
        let (_, start, len) = WINDOWS
            .iter()
            .find(|w| w.0 == window)
            .expect("known window");
        Self {
            window,
            start_min: *start,
            length: *len,
            cells: BTreeMap::new(),
        }
    }

    /// `p`'s ET minute-of-day is passed in (the ET conversion lives in the runner).
    pub fn add_print(&mut self, p: &Print, et_minute_of_day: i64, mode: &str) {
        if !GAUGE_UNDERLYINGS.contains(&p.underlying.as_str()) {
            return;
        }
        if p.hygiene_excluded() || p.spread_tagged() {
            return;
        }
        if p.price <= 0.0 || p.size <= 0 {
            return;
        }
        let prem = p.price * p.size as f64 * 100.0;
        if prem < CLUSTER_PRINT_MIN_USD {
            return;
        }
        let offset = et_minute_of_day - self.start_min;
        if !(0 <= offset && offset < self.length) {
            return;
        }
        let cell = self
            .cells
            .entry(p.underlying.clone())
            .or_default()
            .entry(offset)
            .or_default();
        match &cell.coverage {
            None => cell.coverage = Some(mode.to_string()),
            Some(c) if c != mode => cell.coverage = Some("mixed".to_string()),
            _ => {}
        }
        let right = p.right_letter();
        match (p.direction, right) {
            (Dir::Buy, 'c') | (Dir::Sell, 'p') => cell.bull_usd += prem,
            (Dir::Buy, 'p') | (Dir::Sell, 'c') => cell.bear_usd += prem,
            _ => cell.unclassified_usd += prem,
        }
        cell.n_prints += 1;
    }

    /// (underlying, minute, cell) sorted by sym then minute.
    pub fn minute_rows(&self) -> Vec<(&str, i64, &MinuteCell)> {
        let mut rows = Vec::new();
        for (sym, cells) in &self.cells {
            for (m, c) in cells {
                rows.push((sym.as_str(), *m, c));
            }
        }
        rows
    }

    pub fn window_stats(&self) -> WindowStats {
        let mut minutes = std::collections::BTreeSet::new();
        let mut modes = std::collections::BTreeSet::new();
        for cells in self.cells.values() {
            for (m, c) in cells {
                minutes.insert(*m);
                if let Some(cv) = &c.coverage {
                    modes.insert(cv.clone());
                }
            }
        }
        WindowStats {
            syms: self.cells.len() as i64,
            minutes_observed: minutes.len() as i64,
            coverage_modes: if modes.is_empty() {
                None
            } else {
                Some(modes.into_iter().collect::<Vec<_>>().join(","))
            },
        }
    }
}
