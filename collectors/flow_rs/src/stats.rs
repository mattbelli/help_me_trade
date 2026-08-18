//! Live stats snapshot for the UI (`/api/collectors/stats` → the toolbar feed
//! panel). Every second the collector writes `<stats dir>/<name>.json`
//! (tmp + rename, so readers never see a torn file): current rates, latency
//! percentiles for the last second AND for the ET day (a log-spaced histogram —
//! 40 buckets per decade from 0.1 ms to 100 s, so a day of tens of millions of
//! samples costs 1.9 KB and p99 is within ~6% of exact), a per-minute series
//! for the sparkline, and the collector's counters. Same file in both crates.

use serde_json::Value;
use std::collections::VecDeque;
use std::path::{Path, PathBuf};

const PER_DECADE: f64 = 40.0;
const MIN_MS: f64 = 0.1;
const N_BUCKETS: usize = 240; // 6 decades: 0.1 ms .. 100 s
const MINUTES_KEEP: usize = 24 * 60;

#[derive(Clone)]
pub struct LagHist {
    buckets: Vec<u64>,
    pub n: u64,
    pub max_ms: f64,
}

impl Default for LagHist {
    fn default() -> Self {
        Self { buckets: vec![0; N_BUCKETS], n: 0, max_ms: 0.0 }
    }
}

impl LagHist {
    pub fn add(&mut self, ms: f64) {
        if !ms.is_finite() {
            return;
        }
        let x = ms.max(MIN_MS);
        let i = ((x / MIN_MS).log10() * PER_DECADE).floor() as usize;
        self.buckets[i.min(N_BUCKETS - 1)] += 1;
        self.n += 1;
        if ms > self.max_ms {
            self.max_ms = ms;
        }
    }

    /// Geometric midpoint of the bucket holding the q-th sample (NaN when empty).
    pub fn pct(&self, q: f64) -> f64 {
        if self.n == 0 {
            return f64::NAN;
        }
        let target = ((self.n as f64) * q).ceil().max(1.0) as u64;
        let mut acc = 0u64;
        for (i, c) in self.buckets.iter().enumerate() {
            acc += c;
            if acc >= target {
                let lo = MIN_MS * 10f64.powf(i as f64 / PER_DECADE);
                let hi = MIN_MS * 10f64.powf((i + 1) as f64 / PER_DECADE);
                return (lo * hi).sqrt();
            }
        }
        f64::NAN
    }

    pub fn reset(&mut self) {
        self.buckets.iter_mut().for_each(|b| *b = 0);
        self.n = 0;
        self.max_ms = 0.0;
    }

    pub fn json(&self) -> Value {
        fn f(x: f64) -> Value {
            if x.is_finite() { Value::from((x * 100.0).round() / 100.0) } else { Value::Null }
        }
        serde_json::json!({
            "n": self.n,
            "p50": f(self.pct(0.5)), "p90": f(self.pct(0.9)), "p95": f(self.pct(0.95)), "p99": f(self.pct(0.99)),
            "max": f(if self.n > 0 { self.max_ms } else { f64::NAN }),
        })
    }
}

/// Per-minute series (ET-day scoped) for the sparkline: (epoch_min, rate/s, lag p95 ms).
#[derive(Default)]
pub struct MinuteSeries {
    rows: VecDeque<(i64, f64, f64)>,
    cur_min: i64,
    cur_msgs: u64,
    cur_hist: LagHist,
}

impl MinuteSeries {
    /// Fold one second's worth of samples in; rolls the minute when it changes.
    pub fn observe(&mut self, now_s: i64, msgs_this_sec: u64, sec_hist: &LagHist) {
        let m = now_s.div_euclid(60);
        if m != self.cur_min {
            if self.cur_min != 0 {
                let rate = self.cur_msgs as f64 / 60.0;
                self.rows.push_back((self.cur_min, rate, self.cur_hist.pct(0.95)));
                while self.rows.len() > MINUTES_KEEP {
                    self.rows.pop_front();
                }
            }
            self.cur_min = m;
            self.cur_msgs = 0;
            self.cur_hist.reset();
        }
        self.cur_msgs += msgs_this_sec;
        for (i, c) in sec_hist.buckets.iter().enumerate() {
            self.cur_hist.buckets[i] += c;
        }
        self.cur_hist.n += sec_hist.n;
        self.cur_hist.max_ms = self.cur_hist.max_ms.max(sec_hist.max_ms);
    }

    pub fn reset(&mut self) {
        self.rows.clear();
        self.cur_min = 0;
        self.cur_msgs = 0;
        self.cur_hist.reset();
    }

    pub fn json(&self) -> Value {
        Value::Array(
            self.rows
                .iter()
                .map(|(m, r, p)| serde_json::json!([m, (r * 10.0).round() / 10.0, if p.is_finite() { Value::from((p * 10.0).round() / 10.0) } else { Value::Null }]))
                .collect(),
        )
    }
}

pub struct SnapshotWriter {
    path: PathBuf,
    tmp: PathBuf,
    pub errors: u64,
}

impl SnapshotWriter {
    pub fn new(dir: &Path, name: &str) -> std::io::Result<Self> {
        std::fs::create_dir_all(dir)?;
        Ok(Self { path: dir.join(format!("{name}.json")), tmp: dir.join(format!(".{name}.json.tmp")), errors: 0 })
    }

    pub fn write(&mut self, v: &Value) {
        let res = std::fs::write(&self.tmp, v.to_string()).and_then(|_| std::fs::rename(&self.tmp, &self.path));
        if let Err(e) = res {
            self.errors += 1;
            if self.errors == 1 {
                eprintln!("stats snapshot: write {} failed: {e}", self.path.display());
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hist_percentiles_are_close() {
        let mut h = LagHist::default();
        for i in 1..=1000 {
            h.add(i as f64); // 1..1000 ms uniform
        }
        let p50 = h.pct(0.5);
        let p95 = h.pct(0.95);
        assert!((p50 - 500.0).abs() / 500.0 < 0.07, "{p50}");
        assert!((p95 - 950.0).abs() / 950.0 < 0.07, "{p95}");
        assert_eq!(h.max_ms, 1000.0);
        assert!(LagHist::default().pct(0.5).is_nan());
        h.add(0.0);
        h.add(1e9); // clamps into the last bucket
        assert_eq!(h.n, 1002);
    }

    #[test]
    fn minute_series_rolls() {
        let mut m = MinuteSeries::default();
        let mut sec = LagHist::default();
        sec.add(5.0);
        for s in 0..125 {
            m.observe(60_000 + s, 100, &sec);
        }
        let rows = m.json();
        assert_eq!(rows.as_array().unwrap().len(), 2); // two full minutes closed
        assert_eq!(rows[0][1], 100.0);
    }
}
