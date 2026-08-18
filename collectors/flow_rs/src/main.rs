//! flow_rs — the OPRA options-flow collector in Rust. Stage 1: file replay only,
//! diffed against the Python collector with `tools/flow_ab.py`. Contract and
//! clock rules: `References/FLOW_COLLECTOR_RS_SPEC.md`.

mod db;
mod gauge;
mod intraday;
mod occ;
mod print;
mod pyfmt;
mod rolling;
mod runner;
mod sweeps;

use anyhow::{Context, Result};
use chrono::{NaiveDate, NaiveTime, TimeZone};
use chrono_tz::America::New_York;
use clap::Parser;
use dbn::decode::{DbnMetadata, DecodeRecordRef};
use dbn::{Cmbp1Msg, InstrumentDefMsg};
use std::collections::HashMap;
use std::path::PathBuf;

const END_GRACE_S: i64 = 120;

#[derive(Parser, Debug)]
#[command(about = "Replay a Databento OPRA tcbbo session through the flow detectors (Python-parity port)")]
struct Args {
    /// Session date YYYY-MM-DD (the ET date; used for --start/--end and the log line)
    #[arg(long)]
    date: String,
    /// tcbbo file (data/databento/opra_tcbbo/<date>.dbn.zst)
    #[arg(long)]
    tape: PathBuf,
    /// definition file for instrument_id → raw_symbol (data/databento/opra_defs/<date>.dbn.zst)
    #[arg(long)]
    defs: PathBuf,
    /// scratch trading.db with the Python schema (tools/flow_db_init.py)
    #[arg(long)]
    db: PathBuf,
    /// HH:MM ET — skip records before this time of day
    #[arg(long)]
    start: Option<String>,
    /// HH:MM ET — stop at the first record at/after this time of day
    #[arg(long)]
    end: Option<String>,
    /// The session is a 13:00 ET early close (Python: is_early_close(date))
    #[arg(long, default_value_t = false)]
    early_close: bool,
    /// print-lab JSONL dir (default: <db dir>/research/print_lab)
    #[arg(long)]
    lab_dir: Option<PathBuf>,
}

fn et_ns(date: &str, hhmm: &str) -> Result<u64> {
    let d = NaiveDate::parse_from_str(date, "%Y-%m-%d")?;
    let t = NaiveTime::parse_from_str(hhmm, "%H:%M")?;
    let dt = New_York.from_local_datetime(&d.and_time(t)).single().context("ambiguous ET time")?;
    Ok(dt.timestamp() as u64 * 1_000_000_000)
}

fn load_symbology(path: &PathBuf) -> Result<HashMap<u32, Box<str>>> {
    let mut dec = dbn::decode::dbn::Decoder::from_zstd_file(path).with_context(|| format!("open {}", path.display()))?;
    let mut map: HashMap<u32, Box<str>> = HashMap::with_capacity(2_200_000);
    while let Some(rec) = dec.decode_record_ref()? {
        if let Some(d) = rec.get::<InstrumentDefMsg>() {
            map.insert(d.hd.instrument_id, d.raw_symbol()?.into());
        }
    }
    Ok(map)
}

fn main() -> Result<()> {
    let args = Args::parse();
    let t0 = std::time::Instant::now();
    let symbology = load_symbology(&args.defs)?;
    eprintln!("symbology: {} instruments from {} in {:.1}s", symbology.len(), args.defs.display(), t0.elapsed().as_secs_f64());

    let start_ns = args.start.as_deref().map(|s| et_ns(&args.date, s)).transpose()?;
    let end_ns = args.end.as_deref().map(|s| et_ns(&args.date, s)).transpose()?;
    let lab_dir = args.lab_dir.clone().unwrap_or_else(|| {
        args.db.parent().unwrap_or(std::path::Path::new(".")).join("research").join("print_lab")
    });
    let db = db::Db::open(&args.db)?;
    let mut runner = runner::Runner::new(symbology, db, lab_dir, args.early_close);

    let t1 = std::time::Instant::now();
    let mut dec = dbn::decode::dbn::Decoder::from_zstd_file(&args.tape).with_context(|| format!("open {}", args.tape.display()))?;
    let _ = dec.metadata();
    // Clock rule: T0 = floor(first ts_recv / 1e9) + 1; before ingesting a record
    // with ts_recv >= T·1e9 run the cycle at T (drain, then one sweep cycle) and
    // T += 1; records are ingested in FILE order. After the last record: the
    // cycle that ingested it plus END_GRACE_S - 1 more.
    let mut now: Option<i64> = None;
    let mut start_now: i64 = 0;
    while let Some(rec) = dec.decode_record_ref()? {
        let Some(m) = rec.get::<Cmbp1Msg>() else { continue };
        let ts = m.ts_recv;
        if let Some(s) = start_ns {
            if ts < s {
                continue;
            }
        }
        if let Some(e) = end_ns {
            if ts >= e {
                break;
            }
        }
        let mut t = match now {
            Some(t) => t,
            None => {
                start_now = (ts / 1_000_000_000) as i64 + 1;
                start_now
            }
        };
        while ts >= t as u64 * 1_000_000_000 {
            runner.cycle(t)?;
            t += 1;
        }
        now = Some(t);
        let lvl = &m.levels[0];
        runner.on_record(m.hd.instrument_id, ts, m.price, m.size, m.hd.publisher_id, lvl.bid_px, lvl.ask_px, runner::today_et(t));
    }
    let Some(mut t) = now else {
        eprintln!("replay finished: records=0 prints=0 (empty tape)");
        return Ok(());
    };
    let tape_end_now = t;
    while t - tape_end_now < END_GRACE_S {
        runner.cycle(t)?;
        t += 1;
    }
    let (events, minutes) = runner.finish()?;
    let elapsed = t1.elapsed().as_secs_f64();
    let s = &runner.stats;
    eprintln!(
        "replay finished: records={} prints={} no_meta={} unresolved={} cycles={} virtual={}..{} in {:.1}s ({:.0} rec/s) | events={:?} index_pressure_minutes={} (upserts: events={} minutes={})",
        s.records, s.prints, s.skipped_no_meta, s.unresolved_symbols, s.cycles, start_now, t - 1, elapsed,
        s.records as f64 / elapsed.max(1e-9), events, minutes, s.events_written, s.minute_rows_written
    );
    Ok(())
}
