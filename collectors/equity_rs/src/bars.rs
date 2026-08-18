//! Per-symbol 5-minute RTH bars off the raw print stream — the hot half of
//! `signals/abnormality/detector.py::AbnormalityDetector.on_print`. Rust folds
//! prints → 1-min → 5-min buckets (0–77 from 09:30 ET) and hands the completed
//! buckets to the DB writer at every 5-min boundary (+2 s, so stragglers land);
//! the Python sidecar (`signals/equity_stream/sidecar.py`) does the judgment
//! (baselines, fire rules, cooldown) off those rows.
//!
//! One deliberate fix vs the Python detector: a minute folded at a boundary is
//! reset (`m1_minute = -1`), so it can never be folded a second time when the
//! next print of that minute arrives (Python double-counted that volume).

use chrono::{Datelike, NaiveDate, TimeZone, Timelike};
use chrono_tz::America::New_York;
use std::collections::HashMap;

pub const N_BUCKETS: usize = 78; // 09:30–16:00 ET at 5 min; early closes just stop short
const OPEN_MIN: i64 = 9 * 60 + 30;
const SESSION_MIN: i64 = 6 * 60 + 30;
const BOUNDARY_GRACE_S: u32 = 2;

pub struct BarRow {
    pub symbol: String,
    pub bucket: i64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

pub struct BarFlush {
    pub session: String,
    /// Every bucket < this one is complete in the rows written so far.
    pub through_bucket: i64,
    pub rows: Vec<BarRow>,
}

struct Sym {
    m1_minute: i64,
    m1_h: f64,
    m1_l: f64,
    m1_c: f64,
    m1_v: f64,
    m5_h: [f64; N_BUCKETS],
    m5_l: [f64; N_BUCKETS],
    m5_c: [f64; N_BUCKETS],
    m5_v: [f64; N_BUCKETS],
    dirty: u128,
}

impl Sym {
    fn new() -> Self {
        Self {
            m1_minute: -1,
            m1_h: 0.0,
            m1_l: 0.0,
            m1_c: 0.0,
            m1_v: 0.0,
            m5_h: [f64::NAN; N_BUCKETS],
            m5_l: [f64::NAN; N_BUCKETS],
            m5_c: [f64::NAN; N_BUCKETS],
            m5_v: [f64::NAN; N_BUCKETS],
            dirty: 0,
        }
    }

    /// Fold the in-progress 1-min bar into its 5-min slot, then reset it.
    fn fold_minute(&mut self) {
        if self.m1_minute < 0 {
            return;
        }
        let bucket = (self.m1_minute / 5) as usize;
        self.m1_minute = -1;
        if bucket >= N_BUCKETS {
            return;
        }
        if self.m5_h[bucket].is_nan() {
            self.m5_h[bucket] = self.m1_h;
            self.m5_l[bucket] = self.m1_l;
            self.m5_v[bucket] = self.m1_v;
        } else {
            if self.m1_h > self.m5_h[bucket] {
                self.m5_h[bucket] = self.m1_h;
            }
            if self.m1_l < self.m5_l[bucket] {
                self.m5_l[bucket] = self.m1_l;
            }
            self.m5_v[bucket] += self.m1_v;
        }
        self.m5_c[bucket] = self.m1_c; // last print in this minute is the running close
        self.dirty |= 1u128 << bucket;
    }
}

#[derive(Default)]
pub struct BarGrid {
    session: Option<NaiveDate>,
    syms: HashMap<String, Sym>,
    last_boundary: i64,
    pub rows_flushed: u64,
}

/// (ET session date, minutes since 09:30 ET, second-of-minute) for a unix-ms stamp.
fn et_parts(ts_ms: f64) -> Option<(NaiveDate, i64, u32)> {
    let dt = New_York.timestamp_millis_opt(ts_ms as i64).single()?;
    let minutes = (dt.hour() as i64 * 60 + dt.minute() as i64) - OPEN_MIN;
    Some((dt.date_naive(), minutes, dt.second()))
}

impl BarGrid {
    /// O(1) hot path — update the symbol's current 1-minute bar.
    pub fn on_print(&mut self, symbol: &str, price: f64, size: i64, ts_ms: f64) {
        if price <= 0.0 || size <= 0 {
            return;
        }
        let Some((date, minutes, _)) = et_parts(ts_ms) else { return };
        if !(0..SESSION_MIN).contains(&minutes) {
            return;
        }
        match self.session {
            Some(s) if s == date => {}
            Some(s) if date < s => return, // a stale print from a previous session: ignore
            _ => self.reset(date),
        }
        let st = self.syms.entry(symbol.to_string()).or_insert_with(Sym::new);
        if st.m1_minute != minutes {
            st.fold_minute();
            st.m1_minute = minutes;
            st.m1_h = price;
            st.m1_l = price;
            st.m1_c = price;
            st.m1_v = size as f64;
        } else {
            if price > st.m1_h {
                st.m1_h = price;
            }
            if price < st.m1_l {
                st.m1_l = price;
            }
            st.m1_c = price;
            st.m1_v += size as f64;
        }
    }

    fn reset(&mut self, date: NaiveDate) {
        self.session = Some(date);
        self.syms.clear();
        self.last_boundary = -1;
    }

    /// Timer path (1 s live tick / replay clock). At each new 5-min boundary
    /// (+grace) fold every in-progress minute and hand back the completed
    /// buckets (< the current one) that changed since the last flush.
    pub fn maybe_flush(&mut self, now_ms: f64) -> Option<BarFlush> {
        let session = self.session?;
        let (date, minutes, second) = et_parts(now_ms)?;
        if date != session || minutes < 0 || second < BOUNDARY_GRACE_S {
            return None;
        }
        let bucket = (minutes / 5).min(N_BUCKETS as i64);
        if bucket == self.last_boundary {
            return None;
        }
        self.last_boundary = bucket;
        let done_mask: u128 = if bucket >= 128 { u128::MAX } else { (1u128 << bucket) - 1 };
        let mut rows = Vec::new();
        for (symbol, st) in self.syms.iter_mut() {
            st.fold_minute();
            let mut bits = st.dirty & done_mask;
            st.dirty &= !done_mask;
            while bits != 0 {
                let b = bits.trailing_zeros() as usize;
                bits &= bits - 1;
                rows.push(BarRow {
                    symbol: symbol.clone(),
                    bucket: b as i64,
                    high: st.m5_h[b],
                    low: st.m5_l[b],
                    close: st.m5_c[b],
                    volume: st.m5_v[b],
                });
            }
        }
        self.rows_flushed += rows.len() as u64;
        Some(BarFlush {
            session: format!("{:04}-{:02}-{:02}", session.year(), session.month(), session.day()),
            through_bucket: bucket,
            rows,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ms(hhmmss: &str) -> f64 {
        // 2026-08-18 is EDT (UTC-4)
        let (h, m, s) = (&hhmmss[0..2], &hhmmss[3..5], &hhmmss[6..8]);
        let dt = New_York.with_ymd_and_hms(2026, 8, 18, h.parse().unwrap(), m.parse().unwrap(), s.parse().unwrap()).unwrap();
        dt.timestamp_millis() as f64
    }

    #[test]
    fn folds_and_never_double_counts() {
        let mut g = BarGrid::default();
        g.on_print("AAA", 10.0, 100, ms("09:31:10"));
        g.on_print("AAA", 11.0, 50, ms("09:31:50"));
        let f = g.maybe_flush(ms("09:31:55")).unwrap(); // first tick: marker only, nothing complete
        assert!(f.rows.is_empty() && f.through_bucket == 0);
        // boundary at 09:35:02: bucket 1 → bucket 0 complete
        let f = g.maybe_flush(ms("09:35:02")).unwrap();
        assert_eq!(f.through_bucket, 1);
        assert_eq!(f.rows.len(), 1);
        assert_eq!((f.rows[0].bucket, f.rows[0].high, f.rows[0].low, f.rows[0].close, f.rows[0].volume), (0, 11.0, 10.0, 11.0, 150.0));
        // a straggler for minute 09:31 after the boundary → bucket 0 dirty again, re-emitted next boundary
        g.on_print("AAA", 9.0, 10, ms("09:31:59"));
        g.on_print("AAA", 12.0, 5, ms("09:36:00"));
        assert!(g.maybe_flush(ms("09:35:30")).is_none()); // same boundary
        let f = g.maybe_flush(ms("09:40:03")).unwrap();
        let b0 = f.rows.iter().find(|r| r.bucket == 0).unwrap();
        assert_eq!((b0.high, b0.low, b0.close, b0.volume), (11.0, 9.0, 9.0, 160.0));
        let b1 = f.rows.iter().find(|r| r.bucket == 1).unwrap();
        assert_eq!((b1.high, b1.volume), (12.0, 5.0));
    }

    #[test]
    fn premarket_and_stale_ignored() {
        let mut g = BarGrid::default();
        g.on_print("AAA", 10.0, 100, ms("09:29:59"));
        assert!(g.maybe_flush(ms("09:35:02")).is_none()); // no session yet
        g.on_print("AAA", 10.0, 100, ms("15:59:59"));
        let f = g.maybe_flush(ms("16:00:02")).unwrap();
        assert_eq!(f.through_bucket, 78);
        assert_eq!(f.rows[0].bucket, 77);
        g.on_print("AAA", 10.0, 100, ms("16:00:00")); // after the bell → dropped
        assert!(g.maybe_flush(ms("16:05:02")).is_none()); // post-close: one boundary (78), then quiet
    }
}
