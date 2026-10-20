//! `OptionsFlowRunner.sweep_loop` (one cycle per virtual second) + the spine's
//! `_on_record` → `normalize_print` step, on the replay clock rules in
//! `tools/flow_collector_replay.py` / `References/FLOW_COLLECTOR_RS_SPEC.md`.

use crate::calendar::{is_early_close, is_trading_day};
use crate::db::Db;
use crate::gauge::{PressureGauge, WINDOWS};
use crate::intraday::{
    classify_direction, classify_midpoint, detect_block, tag_likely_spreads, Event,
};
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
    pub prints_coded: u64,
    pub repairs_applied: u64,
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
    lab_dir: PathBuf,
    lab: Option<(String, std::io::BufWriter<std::fs::File>)>,
    db: Db,
    pid: i64,
    pub stats: Stats,
    /// Live only: the condition-code pairing shared with the Alpaca sidecar.
    pub pairing: Option<std::sync::Arc<std::sync::Mutex<crate::pairing::Pairing>>>,
    /// print_id → event id backrefs for blocks emitted this cycle (sweeps keep theirs in the buffer).
    block_backrefs: Vec<(u64, String)>,
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
    pub fn new(symbology: HashMap<u32, Box<str>>, db: Db, lab_dir: PathBuf) -> Self {
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
            lab_dir,
            lab: None,
            db,
            pairing: None,
            block_backrefs: Vec::new(),
            pid: std::process::id() as i64,
            stats: Stats {
                records: 0,
                prints: 0,
                skipped_no_meta: 0,
                unresolved_symbols: 0,
                cycles: 0,
                events_written: 0,
                minute_rows_written: 0,
                prints_coded: 0,
                repairs_applied: 0,
            },
        }
    }

    /// Live symbology: `Live.symbology_map[instrument_id] = stype_out_symbol`.
    pub fn map_symbol(&mut self, instrument_id: u32, raw_symbol: &str) {
        self.symbology.insert(instrument_id, raw_symbol.into());
    }

    pub fn mapped(&self) -> usize {
        self.symbology.len()
    }

    /// `DatabentoFlowStream._on_record` for a tcbbo record (`today` = the
    /// clock's ET date, for DTE — `runner.meta_for`).
    #[allow(clippy::too_many_arguments)]
    pub fn on_record(
        &mut self,
        instrument_id: u32,
        ts_event: u64,
        ts_recv: u64,
        price: i64,
        size: u32,
        publisher_id: u16,
        bid_px: i64,
        ask_px: i64,
        today: NaiveDate,
    ) {
        self.stats.records += 1;
        let Some(raw) = self.symbology.get(&instrument_id) else {
            self.stats.unresolved_symbols += 1;
            return;
        };
        let occ = occ_from_osi(raw);
        // Pairing tee — after symbology, BEFORE the meta gate (the whole-market
        // population the batch join labels). ts_event = the OPRA participant
        // stamp both tapes share.
        let print_id = match &self.pairing {
            Some(p) => p
                .lock()
                .unwrap()
                .on_dbn(&occ, price, size, ts_event as i64, crate::alpaca_ws::now_ns())
                .unwrap_or(0),
            None => 0,
        };
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
            Some((
                bid_px as f64 / PX_SCALE,
                ask_px as f64 / PX_SCALE,
                (ts_recv / 1_000_000) as i64,
            ))
        } else {
            None
        };
        self.pending.push(Print {
            print_id,
            occ: meta.occ,
            underlying: meta.underlying,
            right: meta.right,
            strike: meta.strike,
            expiry: meta.expiry,
            dte: meta.dte,
            ts_ms,
            price: price_f,
            size: size as i64,
            exchange: if publisher_id == 0 {
                String::new()
            } else {
                publisher_id.to_string()
            },
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

    /// One `sweep_loop` iteration at time `now` (epoch seconds; whole seconds
    /// under the replay clock rule, the wall clock live).
    pub fn cycle(&mut self, now: f64) -> Result<()> {
        self.stats.cycles += 1;
        let now_et = et_of(now.floor() as i64);
        let trading_day = is_trading_day(now_et.date_naive());
        // drain → on_prints (a print on a non-session date is never real: OPRA
        // Saturday tests stream over the production feed) → collected_prints → batch
        let mut batch = std::mem::take(&mut self.pending);
        if !trading_day {
            batch.clear();
        }
        let mut shadows = HashMap::new();
        if let Some(pairing) = self.pairing.clone() {
            let (repairs, codes) = {
                let mut p = pairing.lock().unwrap();
                shadows = p.take_shadows();
                (p.drain_repairs(), p.take_resolved())
            };
            // Late-enrichment repairs FIRST each cycle: anything queued during
            // cycle N was inserted by N's end, so this never races an insert.
            if !repairs.is_empty() {
                let n = self.db.flag_events_spread(&repairs)?;
                self.stats.repairs_applied += n as u64;
                eprintln!("options_flow pairing repair: {} late MULTILEG codes → {n} event rows flagged spread", repairs.len());
            }
            if !codes.is_empty() {
                for p in batch.iter_mut() {
                    if let Some(c) = codes.get(&p.print_id) {
                        p.conditions = if c.is_empty() { Vec::new() } else { vec![c.clone()] };
                        self.stats.prints_coded += 1;
                    }
                }
                self.buffer.stamp_conditions(&codes);
            }
        }
        let today_et = now_et.format("%Y-%m-%d").to_string();
        if self.buffer_day != today_et {
            self.buffer = SweepBuffer::new();
            self.rolling = RollingFlow::new();
            self.gauges = WINDOWS.iter().map(|w| PressureGauge::new(w.0)).collect();
            self.gauge_final.clear();
            self.gauge_seen.clear();
            self.half_day = is_early_close(now_et.date_naive());
            self.buffer_day = today_et.clone();
        }

        let mut block_events: Vec<Event> = Vec::new();
        if !batch.is_empty() {
            tag_likely_spreads(&mut batch);
            let now_min = now_et.hour() as i64 * 60 + now_et.minute() as i64;
            let lab_open = (565..=605).contains(&now_min) || (925..=965).contains(&now_min);
            let mut lab_lines: Vec<String> = if lab_open {
                Vec::with_capacity(batch.len())
            } else {
                Vec::new()
            };
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
                    if p.print_id != 0 {
                        self.block_backrefs.push((p.print_id, blk.id.clone()));
                    }
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
            // Stage-A shadow: score each print's Alpaca twin against the dbn
            // direction just stamped. What's left in `shadows` is counted unclaimed.
            if let Some(pairing) = &self.pairing {
                let mut pr = pairing.lock().unwrap();
                pr.shadow.roll(&today_et);
                for p in batch.iter() {
                    let sh = if p.print_id != 0 { shadows.remove(&p.print_id) } else { None };
                    pr.shadow.add(p.direction, p.price * p.size as f64 * 100.0, sh);
                }
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
        if let (Some(pairing), false) = (&self.pairing, shadows.is_empty()) {
            pairing.lock().unwrap().shadow.unclaimed += shadows.len() as u64;
        }

        let now_mono = now;
        let now_ms = (now * 1000.0) as i64;
        if now_mono - self.rolling_last_check >= 60.0 {
            self.rolling_last_check = now_mono;
            let mut evs = self.rolling.check_bursts(now_ms);
            evs.extend(self.rolling.check_clusters(now_ms));
            self.insert(&evs)?;
        }
        if now_mono - self.gauge_last_flush >= 10.0 && trading_day {
            self.gauge_last_flush = now_mono;
            let now_min = now_et.hour() as i64 * 60 + now_et.minute() as i64;
            for gi in 0..self.gauges.len() {
                let win = self.gauges[gi].window;
                if self.gauge_final.contains(&win) {
                    continue;
                }
                if self.half_day && win == "close" {
                    self.db
                        .record_day(&today_et, win, "half", 0, 0, None, self.pid, true)?;
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
                let rows: Vec<(String, String, i64, f64, f64, f64, i64, Option<String>)> = self
                    .gauges[gi]
                    .minute_rows()
                    .into_iter()
                    .map(|(s, m, c)| {
                        (
                            s.to_string(),
                            today_et.clone(),
                            m,
                            c.bull_usd,
                            c.bear_usd,
                            c.unclassified_usd,
                            c.n_prints,
                            c.coverage.clone(),
                        )
                    })
                    .collect();
                if !rows.is_empty() {
                    self.stats.minute_rows_written += rows.len() as u64;
                    self.db.upsert_minutes(&rows, win)?;
                }
                if !rows.is_empty() || finalize {
                    let st = self.gauges[gi].window_stats();
                    self.db.record_day(
                        &today_et,
                        win,
                        "full",
                        st.minutes_observed,
                        st.syms,
                        st.coverage_modes.as_deref(),
                        self.pid,
                        finalize,
                    )?;
                    if finalize {
                        self.gauge_final.push(win);
                    }
                }
            }
        }

        let mut events = block_events;
        events.extend(self.buffer.close_ready(now_ms, &today_et));
        if let Some(pairing) = &self.pairing {
            let mut refs = std::mem::take(&mut self.block_backrefs);
            refs.append(&mut self.buffer.backrefs);
            if !refs.is_empty() {
                pairing.lock().unwrap().attach_events(refs, crate::alpaca_ws::now_ns());
            }
        } else {
            self.block_backrefs.clear();
            self.buffer.backrefs.clear();
        }
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
            let f = std::fs::OpenOptions::new()
                .append(true)
                .create(true)
                .open(self.lab_dir.join(format!("{date}.jsonl")))?;
            self.lab = Some((
                date.to_string(),
                std::io::BufWriter::with_capacity(1 << 20, f),
            ));
        }
        let (_, w) = self.lab.as_mut().unwrap();
        for l in lines {
            w.write_all(l.as_bytes())?;
            w.write_all(b"\n")?;
        }
        // Python appends+closes per cycle; live readers (the classification lab)
        // expect the file current to the second, so flush every write.
        w.flush()?;
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
