//! `OptionsFlowRunner.sweep_loop` (one cycle per virtual second) + the spine's
//! `_on_record` → `normalize_print` step, on the replay clock rules in
//! `tools/flow_collector_replay.py` / `References/FLOW_COLLECTOR_RS_SPEC.md`.

use crate::db::Db;
use crate::gauge::{PressureGauge, WINDOWS};
use crate::intraday::{classify_direction, classify_midpoint, detect_block, tag_likely_spreads, Event};
use crate::occ::{meta_from_occ, occ_from_osi, ts_ms_from_ts_recv};
use crate::print::{Dir, Print};
use crate::rolling::RollingFlow;
use crate::sweeps::SweepBuffer;
use anyhow::Result;
use chrono::{Datelike, NaiveDate, TimeZone, Timelike, Utc};
use chrono_tz::America::New_York;
use std::collections::HashMap;
use std::io::Write;
use std::path::PathBuf;

pub const PX_SCALE: f64 = 1e9;
pub const UNDEF_PX: i64 = i64::MAX;
pub const QUOTE_BASIS: &str = "dbn_nbbo";
pub const QUOTE_STALE_MS: i64 = 5_000;
pub const COVERAGE: &str = "market_wide";

pub struct Stats {
    pub records: u64,
    pub prints: u64,
    pub skipped_no_meta: u64,
    pub unresolved_symbols: u64,
    pub cycles: u64,
    pub events_written: u64,
    pub minute_rows_written: u64,
}

pub struct Runner {
    symbology: HashMap<u32, Box<str>>,
    pending: Vec<Print>,
    // sweep_loop state
    buffer: SweepBuffer,
    rolling: RollingFlow,
    gauges: Vec<PressureGauge>,
    gauge_final: Vec<&'static str>,
    gauge_seen: Vec<&'static str>,
    rolling_last_check: f64,
    gauge_last_flush: f64,
    buffer_day: String,
    half_day: bool,
    early_close: bool,
    lab_dir: PathBuf,
    lab: Option<(String, std::io::BufWriter<std::fs::File>)>,
    db: Db,
    pid: i64,
    pub stats: Stats,
}

fn et_of(secs: i64) -> chrono::DateTime<chrono_tz::Tz> {
    Utc.timestamp_opt(secs, 0).unwrap().with_timezone(&New_York)
}

/// `_minutes_into_session(ts)`: ET minutes since 09:30 for a `...Z` ISO ts.
fn minutes_into_session(ts: &str) -> Option<i64> {
    let dt = chrono::DateTime::parse_from_rfc3339(&ts.replace('Z', "+00:00")).ok()?;
    let et = dt.with_timezone(&New_York);
    Some((et.hour() as i64 * 60 + et.minute() as i64) - (9 * 60 + 30))
}

impl Runner {
    pub fn new(symbology: HashMap<u32, Box<str>>, db: Db, lab_dir: PathBuf, early_close: bool) -> Self {
        Self {
            symbology,
            pending: Vec::new(),
            buffer: SweepBuffer::new(),
            rolling: RollingFlow::new(),
            gauges: WINDOWS.iter().map(|w| PressureGauge::new(w.0)).collect(),
            gauge_final: Vec::new(),
            gauge_seen: Vec::new(),
            rolling_last_check: 0.0,
            gauge_last_flush: 0.0,
            buffer_day: String::new(),
            half_day: false,
            early_close,
            lab_dir,
            lab: None,
            db,
            pid: std::process::id() as i64,
            stats: Stats {
                records: 0,
                prints: 0,
                skipped_no_meta: 0,
                unresolved_symbols: 0,
                cycles: 0,
                events_written: 0,
                minute_rows_written: 0,
            },
        }
    }

    /// `DatabentoFlowStream._on_record` for a tcbbo record (`today` = the
    /// clock's ET date, for DTE — `runner.meta_for`).
    #[allow(clippy::too_many_arguments)]
    pub fn on_record(&mut self, instrument_id: u32, ts_recv: u64, price: i64, size: u32, publisher_id: u16, bid_px: i64, ask_px: i64, today: NaiveDate) {
        self.stats.records += 1;
        let Some(raw) = self.symbology.get(&instrument_id) else {
            self.stats.unresolved_symbols += 1;
            return;
        };
        let occ = occ_from_osi(raw);
        let Some(meta) = meta_from_occ(&occ, today) else {
            self.stats.skipped_no_meta += 1;
            return;
        };
        let price_f = price as f64 / PX_SCALE;
        if price_f <= 0.0 || size == 0 {
            return; // normalize_print → None
        }
        let ts_ms = ts_ms_from_ts_recv(ts_recv);
        let nbbo = if bid_px != UNDEF_PX && ask_px != UNDEF_PX && 0 < bid_px && bid_px < ask_px {
            Some((bid_px as f64 / PX_SCALE, ask_px as f64 / PX_SCALE, (ts_recv / 1_000_000) as i64))
        } else {
            None
        };
        self.pending.push(Print {
            occ: meta.occ,
            underlying: meta.underlying,
            right: meta.right,
            strike: meta.strike,
            expiry: meta.expiry,
            dte: meta.dte,
            ts_ms,
            price: price_f,
            size: size as i64,
            exchange: if publisher_id == 0 { String::new() } else { publisher_id.to_string() },
            conditions: Vec::new(),
            nbbo,
            direction: Dir::Undetermined,
            basis: "none",
            mid_side: "unclassified",
            spread_leg: false,
            likely_spread: false,
        });
        self.stats.prints += 1;
    }

    fn insert(&mut self, events: &[Event]) -> Result<()> {
        self.stats.events_written += events.len() as u64;
        self.db.insert_events(events, minutes_into_session)
    }

    /// One `sweep_loop` iteration at virtual time `now` (whole seconds).
    pub fn cycle(&mut self, now: i64) -> Result<()> {
        self.stats.cycles += 1;
        // drain → collected_prints → batch (trading-day gate assumed true: the tape is a session)
        let mut batch = std::mem::take(&mut self.pending);
        let now_et = et_of(now);
        let today_et = now_et.format("%Y-%m-%d").to_string();
        if self.buffer_day != today_et {
            self.buffer = SweepBuffer::new();
            self.rolling = RollingFlow::new();
            self.gauges = WINDOWS.iter().map(|w| PressureGauge::new(w.0)).collect();
            self.gauge_final.clear();
            self.gauge_seen.clear();
            self.half_day = self.early_close;
            self.buffer_day = today_et.clone();
        }

        let mut block_events: Vec<Event> = Vec::new();
        if !batch.is_empty() {
            tag_likely_spreads(&mut batch);
            let now_min = now_et.hour() as i64 * 60 + now_et.minute() as i64;
            let lab_open = (565..=605).contains(&now_min) || (925..=965).contains(&now_min);
            let mut lab_lines: Vec<String> = if lab_open { Vec::with_capacity(batch.len()) } else { Vec::new() };
            for p in batch.iter_mut() {
                let mut lab_quote: Option<(f64, f64, i64)> = None;
                if let Some((bid, ask, q_ts)) = p.nbbo {
                    if (p.ts_ms - q_ts).abs() <= QUOTE_STALE_MS {
                        lab_quote = Some((bid, ask, q_ts));
                        p.direction = classify_direction(p.price, bid, ask);
                        p.mid_side = classify_midpoint(p.price, bid, ask);
                        if p.direction != Dir::Undetermined {
                            p.basis = QUOTE_BASIS;
                        }
                    }
                }
                if let Some(blk) = detect_block(p, &today_et) {
                    block_events.push(blk);
                }
                if lab_open {
                    let mut row = serde_json::Map::new();
                    row.insert("ts_ms".into(), p.ts_ms.into());
                    row.insert("occ".into(), p.occ.as_str().into());
                    row.insert("u".into(), p.underlying.as_str().into());
                    row.insert("price".into(), p.price.into());
                    row.insert("size".into(), p.size.into());
                    row.insert("dte".into(), p.dte.into());
                    row.insert("dir".into(), p.direction.as_str().into());
                    row.insert("basis".into(), p.basis.into());
                    match lab_quote {
                        Some((b, a, q)) => {
                            row.insert("bid".into(), b.into());
                            row.insert("ask".into(), a.into());
                            row.insert("q_ts_ms".into(), q.into());
                        }
                        None => {
                            row.insert("bid".into(), serde_json::Value::Null);
                            row.insert("ask".into(), serde_json::Value::Null);
                            row.insert("q_ts_ms".into(), serde_json::Value::Null);
                        }
                    }
                    if p.mid_side != "unclassified" {
                        row.insert("mid".into(), p.mid_side.into());
                    }
                    if p.spread_tagged() {
                        row.insert("spread".into(), true.into());
                    }
                    if p.hygiene_excluded() {
                        row.insert("hyg".into(), true.into());
                    }
                    lab_lines.push(serde_json::Value::Object(row).to_string());
                }
            }
            if lab_open {
                self.lab_write(&today_et, &lab_lines)?;
            }
            self.buffer.add(&batch);
            for p in &batch {
                self.rolling.add_print(p);
                let et_min = {
                    let dt = et_of(p.ts_ms.div_euclid(1000));
                    dt.hour() as i64 * 60 + dt.minute() as i64
                };
                for g in self.gauges.iter_mut() {
                    g.add_print(p, et_min, COVERAGE);
                }
            }
        }

        let now_mono = now as f64;
        if now_mono - self.rolling_last_check >= 60.0 {
            self.rolling_last_check = now_mono;
            let now_ms = now * 1000;
            let mut evs = self.rolling.check_bursts(now_ms);
            evs.extend(self.rolling.check_clusters(now_ms));
            self.insert(&evs)?;
        }
        if now_mono - self.gauge_last_flush >= 10.0 {
            self.gauge_last_flush = now_mono;
            let now_min = now_et.hour() as i64 * 60 + now_et.minute() as i64;
            for gi in 0..self.gauges.len() {
                let win = self.gauges[gi].window;
                if self.gauge_final.contains(&win) {
                    continue;
                }
                if self.half_day && win == "close" {
                    self.db.record_day(&today_et, win, "half", 0, 0, None, self.pid, true)?;
                    self.gauge_final.push(win);
                    continue;
                }
                let (_, start_min, length) = WINDOWS.iter().find(|w| w.0 == win).unwrap();
                let cutoff = start_min + length + 5;
                if *start_min <= now_min && now_min <= cutoff && !self.gauge_seen.contains(&win) {
                    self.gauge_seen.push(win);
                }
                let finalize = now_min > cutoff;
                if finalize && !self.gauge_seen.contains(&win) {
                    self.gauge_final.push(win);
                    continue;
                }
                let rows: Vec<(String, String, i64, f64, f64, f64, i64, Option<String>)> = self.gauges[gi]
                    .minute_rows()
                    .into_iter()
                    .map(|(s, m, c)| (s.to_string(), today_et.clone(), m, c.bull_usd, c.bear_usd, c.unclassified_usd, c.n_prints, c.coverage.clone()))
                    .collect();
                if !rows.is_empty() {
                    self.stats.minute_rows_written += rows.len() as u64;
                    self.db.upsert_minutes(&rows, win)?;
                }
                if !rows.is_empty() || finalize {
                    let st = self.gauges[gi].window_stats();
                    self.db.record_day(&today_et, win, "full", st.minutes_observed, st.syms, st.coverage_modes.as_deref(), self.pid, finalize)?;
                    if finalize {
                        self.gauge_final.push(win);
                    }
                }
            }
        }

        let mut events = block_events;
        events.extend(self.buffer.close_ready(now * 1000, &today_et));
        if events.is_empty() {
            return Ok(());
        }
        self.insert(&events)
    }

    fn lab_write(&mut self, date: &str, lines: &[String]) -> Result<()> {
        if lines.is_empty() {
            return Ok(());
        }
        let reopen = match &self.lab {
            Some((d, _)) => d != date,
            None => true,
        };
        if reopen {
            if let Some((_, mut w)) = self.lab.take() {
                w.flush()?;
            }
            std::fs::create_dir_all(&self.lab_dir)?;
            let f = std::fs::OpenOptions::new().append(true).create(true).open(self.lab_dir.join(format!("{date}.jsonl")))?;
            self.lab = Some((date.to_string(), std::io::BufWriter::with_capacity(1 << 20, f)));
        }
        let (_, w) = self.lab.as_mut().unwrap();
        for l in lines {
            w.write_all(l.as_bytes())?;
            w.write_all(b"\n")?;
        }
        Ok(())
    }

    pub fn finish(&mut self) -> Result<(Vec<(String, i64)>, i64)> {
        if let Some((_, mut w)) = self.lab.take() {
            w.flush()?;
        }
        self.db.counts()
    }
}

/// The virtual date for `meta_for` at tape time `now`.
pub fn today_et(now: i64) -> NaiveDate {
    let d = et_of(now);
    NaiveDate::from_ymd_opt(d.year(), d.month(), d.day()).unwrap()
}
