//! SQLite writer — a dedicated thread so the tape never waits on disk. Rows
//! mirror `db/equity_tape.py::insert_event` and `db/fusion.py::put_spots`
//! exactly; the schema is owned by Python (`tools/equity_db_init.py`).

use crate::bars::BarFlush;
use crate::detect::Event;
use crate::pyfmt::now_iso;
use anyhow::{bail, Context, Result};
use rusqlite::{params, Connection, OptionalExtension};
use std::path::Path;
use std::sync::mpsc::{self, Receiver, Sender, SyncSender};
use std::thread::JoinHandle;

pub enum Cmd {
    Events(Vec<Event>),
    Spots(Vec<(String, String, String)>),
    Bars(BarFlush),
    Shutdown(Sender<()>),
}

/// The 5-min bar store is ours (Rust owns this schema; Python only reads it —
/// `db/equity_bars_5min.py`). Its own file: ~10k symbols × 78 buckets a day is
/// too much churn for trading.db.
const BARS_DDL: &str = "
CREATE TABLE IF NOT EXISTS equity_bars_5min (
    session TEXT NOT NULL,
    symbol  TEXT NOT NULL,
    bucket  INTEGER NOT NULL,
    high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (session, symbol, bucket)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS equity_bars_5min_flush (
    session TEXT PRIMARY KEY,
    through_bucket INTEGER NOT NULL,
    ts TEXT NOT NULL
);
";
const BARS_KEEP_DAYS: i64 = 30;

pub struct Writer {
    tx: SyncSender<Cmd>,
    handle: Option<JoinHandle<()>>,
    pub errors: std::sync::Arc<std::sync::atomic::AtomicU64>,
}

impl Writer {
    pub fn open(path: &Path, bars_path: &Path) -> Result<Self> {
        let conn = Connection::open(path).with_context(|| format!("open {}", path.display()))?;
        conn.busy_timeout(std::time::Duration::from_secs(30))?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "synchronous", "NORMAL")?;
        // Schema is Python's; refuse to run against a DB that lacks it.
        for t in ["equity_tape_events", "bus_ingest_sequences", "fusion_spot_cache"] {
            let ok: Option<i64> = conn
                .query_row("SELECT 1 FROM sqlite_master WHERE name=?1", [t], |r| r.get(0))
                .optional()?;
            if ok.is_none() {
                bail!("table {t} missing in {} — run: .venv/bin/python tools/equity_db_init.py <db>", path.display());
            }
        }
        // db/equity_tape.py::init_db seeds the sequence row; mirror it.
        conn.execute(
            "INSERT OR IGNORE INTO bus_ingest_sequences (store, next_seq)
             SELECT 'equity_tape', COALESCE(MAX(ingest_seq), 0) + 1 FROM equity_tape_events
",
            [],
        )?;
        let bars = Connection::open(bars_path).with_context(|| format!("open {}", bars_path.display()))?;
        bars.busy_timeout(std::time::Duration::from_secs(30))?;
        bars.pragma_update(None, "journal_mode", "WAL")?;
        bars.pragma_update(None, "synchronous", "NORMAL")?;
        bars.execute_batch(BARS_DDL)?;
        bars.execute(
            "DELETE FROM equity_bars_5min WHERE session < date('now', ?1)",
            [format!("-{BARS_KEEP_DAYS} days")],
        )?;
        let (tx, rx) = mpsc::sync_channel::<Cmd>(4096);
        let errors = std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0));
        let errs = errors.clone();
        let handle = std::thread::Builder::new()
            .name("equity-db-writer".into())
            .spawn(move || run(conn, bars, rx, errs))?;
        Ok(Self { tx, handle: Some(handle), errors })
    }

    pub fn send(&self, cmd: Cmd) -> bool {
        self.tx.send(cmd).is_ok()
    }

    /// Drain everything queued, then stop the thread.
    pub fn shutdown(mut self) {
        let (ack_tx, ack_rx) = mpsc::channel();
        let _ = self.tx.send(Cmd::Shutdown(ack_tx));
        let _ = ack_rx.recv();
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
    }
}

fn run(
    mut conn: Connection,
    mut bars: Connection,
    rx: Receiver<Cmd>,
    errors: std::sync::Arc<std::sync::atomic::AtomicU64>,
) {
    while let Ok(cmd) = rx.recv() {
        match cmd {
            Cmd::Events(events) => {
                for ev in &events {
                    if let Err(e) = insert_event(&mut conn, ev) {
                        errors.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        eprintln!("db: insert_event {} failed: {e:#}", ev.id);
                    }
                }
            }
            Cmd::Spots(rows) => {
                if let Err(e) = put_spots(&mut conn, &rows) {
                    errors.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    eprintln!("db: put_spots ({} rows) failed: {e:#}", rows.len());
                }
            }
            Cmd::Bars(flush) => {
                if let Err(e) = put_bars(&mut bars, &flush) {
                    errors.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    eprintln!("db: put_bars ({} rows) failed: {e:#}", flush.rows.len());
                }
            }
            Cmd::Shutdown(ack) => {
                let _ = ack.send(());
                break;
            }
        }
    }
}

/// `insert_event`: upsert on id; new ids take the next ingest_seq; ts/created_at
/// keep first-detection values.
fn insert_event(conn: &mut Connection, ev: &Event) -> Result<()> {
    let tx = conn.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let existing: Option<Option<i64>> = tx
        .query_row("SELECT ingest_seq FROM equity_tape_events WHERE id = ?1", [&ev.id], |r| r.get(0))
        .optional()?;
    let ingest_seq: i64 = match existing {
        Some(Some(seq)) => seq,
        Some(None) => 0, // Python would int(None) → TypeError; unreachable after backfill
        None => tx.query_row(
            "UPDATE bus_ingest_sequences SET next_seq = next_seq + 1
             WHERE store = 'equity_tape' RETURNING next_seq - 1",
            [],
            |r| r.get(0),
        )?,
    };
    let conditions = serde_json::to_string(&ev.conditions)?;
    let detail = serde_json::to_string(&ev.detail)?;
    tx.execute(
        "INSERT INTO equity_tape_events (
            id, ingest_seq, ts, symbol, event_type, exchange, price, size,
            notional_usd, direction, direction_basis, conditions,
            reason, caveat, detail, created_at
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)
        ON CONFLICT(id) DO UPDATE SET
            symbol = excluded.symbol,
            event_type = excluded.event_type,
            exchange = excluded.exchange,
            price = excluded.price,
            size = excluded.size,
            notional_usd = excluded.notional_usd,
            direction = excluded.direction,
            direction_basis = excluded.direction_basis,
            conditions = excluded.conditions,
            reason = excluded.reason,
            caveat = excluded.caveat,
            detail = excluded.detail",
        params![
            ev.id,
            ingest_seq,
            ev.ts,
            ev.symbol.trim().to_uppercase(),
            ev.event_type,
            ev.exchange,
            ev.price,
            ev.size,
            ev.notional_usd,
            ev.direction,
            ev.direction_basis,
            conditions,
            ev.reason,
            ev.caveat,
            detail,
            now_iso(),
        ],
    )?;
    tx.commit()?;
    Ok(())
}

/// `put_spots`: one transaction, keyed upsert per symbol.
fn put_spots(conn: &mut Connection, rows: &[(String, String, String)]) -> Result<()> {
    if rows.is_empty() {
        return Ok(());
    }
    let now = now_iso();
    let tx = conn.transaction()?;
    {
        let mut stmt = tx.prepare_cached(
            "INSERT INTO fusion_spot_cache(symbol, price, ts, updated_at)
             VALUES (?1, ?2, ?3, ?4)
             ON CONFLICT(symbol) DO UPDATE SET
                 price = excluded.price,
                 ts = excluded.ts,
                 updated_at = excluded.updated_at",
        )?;
        for (s, p, t) in rows {
            stmt.execute(params![s, p, t, now])?;
        }
    }
    tx.commit()?;
    Ok(())
}

/// One transaction per boundary: upsert the completed buckets, then advance the
/// per-session marker the sidecar waits on (rows first, marker last).
fn put_bars(conn: &mut Connection, flush: &BarFlush) -> Result<()> {
    let tx = conn.transaction()?;
    {
        let mut stmt = tx.prepare_cached(
            "INSERT INTO equity_bars_5min(session, symbol, bucket, high, low, close, volume)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
             ON CONFLICT(session, symbol, bucket) DO UPDATE SET
                 high = excluded.high, low = excluded.low,
                 close = excluded.close, volume = excluded.volume",
        )?;
        for r in &flush.rows {
            stmt.execute(params![flush.session, r.symbol, r.bucket, r.high, r.low, r.close, r.volume])?;
        }
    }
    tx.execute(
        "INSERT INTO equity_bars_5min_flush(session, through_bucket, ts) VALUES (?1, ?2, ?3)
         ON CONFLICT(session) DO UPDATE SET through_bucket = excluded.through_bucket, ts = excluded.ts",
        params![flush.session, flush.through_bucket, now_iso()],
    )?;
    tx.commit()?;
    Ok(())
}
