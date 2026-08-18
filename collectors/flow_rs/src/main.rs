//! flow_rs — the OPRA options-flow collector in Rust.
//!   `flow_rs replay` — a recorded tcbbo file through the detectors under the
//!     virtual clock, diffed against Python with `tools/flow_ab.py` (stage 1);
//!   `flow_rs live`   — the Databento live socket, wall clock, liveness +
//!     reconnect + stats line (stage 3). Contract: `References/FLOW_COLLECTOR_RS_SPEC.md`.

mod calendar;
mod db;
mod gauge;
mod intraday;
mod alpaca_ws;
mod live;
mod occ;
mod print;
mod pyfmt;
mod rolling;
mod pairing;
mod record;
mod runner;
mod stats;
mod sweeps;

use anyhow::{Context, Result};
use chrono::{NaiveDate, NaiveTime, TimeZone};
use chrono_tz::America::New_York;
use clap::{Parser, Subcommand};
use dbn::decode::{DbnMetadata, DecodeRecordRef};
use dbn::{Cmbp1Msg, InstrumentDefMsg};
use std::collections::HashMap;
use std::path::PathBuf;

const END_GRACE_S: i64 = 120;

#[derive(Parser, Debug)]
#[command(about = "OPRA options-flow collector (Python-parity port)")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand, Debug)]
enum Cmd {
    /// Replay a recorded tcbbo session through the detectors (virtual clock)
    Replay(ReplayArgs),
    /// The live Databento OPRA session (wall clock); writes into the app DB
    Live(LiveCli),
}

#[derive(Parser, Debug)]
struct LiveCli {
    /// trading.db to write (production: data/trading.db)
    #[arg(long)]
    db: PathBuf,
    /// Databento API key (default: $DATABENTO_API_KEY)
    #[arg(long, env = "DATABENTO_API_KEY", hide_env_values = true)]
    key: String,
    /// Intraday replay start, RFC3339 (≤~24h back; the floor snaps to a UTC
    /// midnight). Transport SMOKE test only: the gateway replays interleaved per
    /// instrument, not in time order (measured 2026-08-17). Stops when the replay
    /// catches up with real time; NEVER reconnects.
    #[arg(long)]
    replay_from: Option<String>,
    /// Stop after this many seconds (smoke / shadow runs)
    #[arg(long)]
    for_secs: Option<u64>,
    /// Tee every record the gateway sends into this zstd DBN file (transport
    /// A/B: `flow_rs replay --tape <it>` vs the archived session → IDENTICAL)
    #[arg(long)]
    record: Option<PathBuf>,
    /// print-lab JSONL dir (default: <db dir>/research/print_lab)
    #[arg(long)]
    lab_dir: Option<PathBuf>,
    /// Condition-code pairing: run the Alpaca OPRA wildcard-trades sidecar
    /// (creds $APCA_API_KEY_ID / $APCA_API_SECRET_KEY) and stamp codes onto the
    /// spine's prints (`OPTIONS_FLOW_PAIRING_SHADOW=1` in the Python collector).
    #[arg(long)]
    pairing: bool,
    #[arg(long, env = "APCA_API_KEY_ID", default_value = "", hide_env_values = true)]
    alpaca_key: String,
    #[arg(long, env = "APCA_API_SECRET_KEY", default_value = "", hide_env_values = true)]
    alpaca_secret: String,
    /// UI stats snapshot dir (default: <db dir>/collector_stats) — flow.json every second.
    #[arg(long)]
    stats_dir: Option<PathBuf>,
    /// GTH tape dir (overnight index prints off the Alpaca lane; default <db dir>/gth_tape)
    #[arg(long)]
    gth_dir: Option<PathBuf>,
    /// Tee the Alpaca sidecar's raw frames to <dir>/<ET date>.mpk.zst (two-tape replay input)
    #[arg(long)]
    record_alpaca_dir: Option<PathBuf>,
}

#[derive(Parser, Debug)]
struct ReplayArgs {
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
    /// print-lab JSONL dir (default: <db dir>/research/print_lab)
    #[arg(long)]
    lab_dir: Option<PathBuf>,
}

fn et_ns(date: &str, hhmm: &str) -> Result<u64> {
    let d = NaiveDate::parse_from_str(date, "%Y-%m-%d")?;
    let t = NaiveTime::parse_from_str(hhmm, "%H:%M")?;
    let dt = New_York
        .from_local_datetime(&d.and_time(t))
        .single()
        .context("ambiguous ET time")?;
    Ok(dt.timestamp() as u64 * 1_000_000_000)
}

fn load_symbology(path: &PathBuf) -> Result<HashMap<u32, Box<str>>> {
    let mut dec = dbn::decode::dbn::Decoder::from_zstd_file(path)
        .with_context(|| format!("open {}", path.display()))?;
    let mut map: HashMap<u32, Box<str>> = HashMap::with_capacity(2_200_000);
    while let Some(rec) = dec.decode_record_ref()? {
        if let Some(d) = rec.get::<InstrumentDefMsg>() {
            map.insert(d.hd.instrument_id, d.raw_symbol()?.into());
        }
    }
    Ok(map)
}

fn lab_dir_for(db: &std::path::Path, override_: Option<PathBuf>) -> PathBuf {
    override_.unwrap_or_else(|| {
        db.parent()
            .unwrap_or(std::path::Path::new("."))
            .join("research")
            .join("print_lab")
    })
}

fn main() -> Result<()> {
    match Cli::parse().cmd {
        Cmd::Replay(args) => replay(args),
        Cmd::Live(args) => {
            let replay_from = args
                .replay_from
                .as_deref()
                .map(|s| chrono::DateTime::parse_from_rfc3339(s).map(|d| d.timestamp()))
                .transpose()
                .context("--replay-from must be RFC3339, e.g. 2026-08-17T13:30:00Z")?;
            let lab_dir = lab_dir_for(&args.db, args.lab_dir.clone());
            let db = db::Db::open(&args.db)?;
            let mut runner = runner::Runner::new(HashMap::with_capacity(2_200_000), db, lab_dir);
            let sidecar = if args.pairing {
                if args.alpaca_key.is_empty() || args.alpaca_secret.is_empty() {
                    anyhow::bail!("--pairing needs APCA_API_KEY_ID / APCA_API_SECRET_KEY");
                }
                let gth_dir = args.gth_dir.clone().unwrap_or_else(|| {
                    args.db.parent().map(|d| d.join("gth_tape")).unwrap_or_else(|| PathBuf::from("data/gth_tape"))
                });
                Some(alpaca_ws::SidecarArgs {
                    key: args.alpaca_key,
                    secret: args.alpaca_secret,
                    url: alpaca_ws::OPRA_WS_URL.to_string(),
                    gth_dir: Some(gth_dir),
                    record_dir: args.record_alpaca_dir,
                })
            } else {
                None
            };
            let code = live::run(
                &mut runner,
                live::LiveArgs {
                    key: args.key,
                    replay_from,
                    for_secs: args.for_secs,
                    record: args.record,
                    sidecar,
                    stats_dir: Some(args.stats_dir.clone().unwrap_or_else(|| {
                        args.db.parent().map(|d| d.join("collector_stats")).unwrap_or_else(|| PathBuf::from("data/collector_stats"))
                    })),
                },
            )?;
            std::process::exit(code);
        }
    }
}

fn replay(args: ReplayArgs) -> Result<()> {
    let t0 = std::time::Instant::now();
    let symbology = load_symbology(&args.defs)?;
    eprintln!(
        "symbology: {} instruments from {} in {:.1}s",
        symbology.len(),
        args.defs.display(),
        t0.elapsed().as_secs_f64()
    );

    let start_ns = args
        .start
        .as_deref()
        .map(|s| et_ns(&args.date, s))
        .transpose()?;
    let end_ns = args
        .end
        .as_deref()
        .map(|s| et_ns(&args.date, s))
        .transpose()?;
    let lab_dir = lab_dir_for(&args.db, args.lab_dir.clone());
    let db = db::Db::open(&args.db)?;
    let mut runner = runner::Runner::new(symbology, db, lab_dir);

    let t1 = std::time::Instant::now();
    let mut dec = dbn::decode::dbn::Decoder::from_zstd_file(&args.tape)
        .with_context(|| format!("open {}", args.tape.display()))?;
    let _ = dec.metadata();
    // Clock rule: T0 = floor(first ts_recv / 1e9) + 1; before ingesting a record
    // with ts_recv >= T·1e9 run the cycle at T (drain, then one sweep cycle) and
    // T += 1; records are ingested in FILE order. After the last record: the
    // cycle that ingested it plus END_GRACE_S - 1 more.
    let mut now: Option<i64> = None;
    let mut start_now: i64 = 0;
    while let Some(rec) = dec.decode_record_ref()? {
        let Some(m) = rec.get::<Cmbp1Msg>() else {
            continue;
        };
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
            runner.cycle(t as f64)?;
            t += 1;
        }
        now = Some(t);
        let lvl = &m.levels[0];
        runner.on_record(
            m.hd.instrument_id,
            m.hd.ts_event,
            ts,
            m.price,
            m.size,
            m.hd.publisher_id,
            lvl.bid_px,
            lvl.ask_px,
            runner::today_et(t),
        );
    }
    let Some(mut t) = now else {
        eprintln!("replay finished: records=0 prints=0 (empty tape)");
        return Ok(());
    };
    let tape_end_now = t;
    while t - tape_end_now < END_GRACE_S {
        runner.cycle(t as f64)?;
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
