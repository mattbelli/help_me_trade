//! SQLite writes mirroring `db/options_flow.insert_event`, `db/index_pressure.
//! upsert_minutes` / `record_day` — same SQL, same bindings. Schema is Python's
//! (`tools/flow_db_init.py <db>` for a scratch DB); Rust never runs DDL.

use crate::intraday::Event;
use crate::pyfmt::now_iso;
use anyhow::{bail, Context, Result};
use rusqlite::{params, Connection, OptionalExtension};
use std::path::Path;

pub struct Db {
    conn: Connection,
}

impl Db {
    pub fn open(path: &Path) -> Result<Self> {
        let conn = Connection::open(path).with_context(|| format!("open {}", path.display()))?;
        conn.busy_timeout(std::time::Duration::from_secs(30))?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "synchronous", "NORMAL")?;
        for t in [
            "options_flow_events",
            "bus_ingest_sequences",
            "index_pressure_minutes",
            "index_pressure_days",
        ] {
            let ok: Option<i64> = conn
                .query_row("SELECT 1 FROM sqlite_master WHERE name=?1", [t], |r| {
                    r.get(0)
                })
                .optional()?;
            if ok.is_none() {
                bail!(
                    "table {t} missing in {} — run: .venv/bin/python tools/flow_db_init.py <db>",
                    path.display()
                );
            }
        }
        Ok(Self { conn })
    }

    /// Row counts for the summary line (same numbers the Python replay prints).
    pub fn counts(&self) -> Result<(Vec<(String, i64)>, i64)> {
        let mut stmt = self.conn.prepare(
            "SELECT event_type, COUNT(*) FROM options_flow_events GROUP BY 1 ORDER BY 1",
        )?;
        let ev = stmt
            .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        let minutes: i64 =
            self.conn
                .query_row("SELECT COUNT(*) FROM index_pressure_minutes", [], |r| {
                    r.get(0)
                })?;
        Ok((ev, minutes))
    }

    /// `insert_event` for a batch, one transaction (Python commits per event;
    /// the rows are identical, only the fsync count differs).
    pub fn insert_events(
        &mut self,
        events: &[Event],
        minutes_into_session: impl Fn(&str) -> Option<i64>,
    ) -> Result<()> {
        if events.is_empty() {
            return Ok(());
        }
        let tx = self
            .conn
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        for ev in events {
            let existing: Option<Option<i64>> = tx
                .query_row(
                    "SELECT ingest_seq FROM options_flow_events WHERE id = ?1",
                    [&ev.id],
                    |r| r.get(0),
                )
                .optional()?;
            let ingest_seq: i64 = match existing {
                Some(Some(seq)) => seq,
                Some(None) => bail!("event {} has NULL ingest_seq", ev.id),
                None => tx.query_row(
                    "UPDATE bus_ingest_sequences SET next_seq = next_seq + 1 WHERE store = 'options_flow' RETURNING next_seq - 1",
                    [],
                    |r| r.get(0),
                )?,
            };
            let created = now_iso();
            let exchanges = serde_json::to_string(&ev.exchanges)?;
            tx.execute(
                "INSERT INTO options_flow_events (
                    id, ingest_seq, ts, underlying, occ_symbol, right, strike, expiry, dte,
                    otm_pct, event_type, contracts, premium_usd, vol_oi_ratio,
                    vol_zscore, direction, direction_basis, spread_flag, n_prints,
                    exchanges, reason, caveat, ret_1d, ret_5d, created_at, detail,
                    coverage, minutes_into_session, mid_side,
                    rarity_p, rarity_n, rarity_cell, rarity_asof
                ) VALUES (
                    ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9,
                    NULL, ?10, ?11, ?12, NULL,
                    NULL, ?13, ?14, 0, ?15,
                    ?16, ?17, ?18, NULL, NULL, ?19, ?20,
                    'market_wide', ?21, ?22,
                    NULL, NULL, NULL, NULL
                )
                ON CONFLICT(id) DO UPDATE SET
                    underlying = excluded.underlying,
                    occ_symbol = excluded.occ_symbol,
                    right = excluded.right,
                    strike = excluded.strike,
                    expiry = excluded.expiry,
                    dte = excluded.dte,
                    otm_pct = excluded.otm_pct,
                    event_type = excluded.event_type,
                    contracts = excluded.contracts,
                    premium_usd = excluded.premium_usd,
                    vol_oi_ratio = excluded.vol_oi_ratio,
                    vol_zscore = excluded.vol_zscore,
                    direction = excluded.direction,
                    direction_basis = excluded.direction_basis,
                    spread_flag = excluded.spread_flag,
                    n_prints = excluded.n_prints,
                    exchanges = excluded.exchanges,
                    reason = excluded.reason,
                    caveat = excluded.caveat,
                    ret_1d = excluded.ret_1d,
                    ret_5d = excluded.ret_5d,
                    detail = excluded.detail,
                    mid_side = excluded.mid_side,
                    rarity_p = COALESCE(excluded.rarity_p, options_flow_events.rarity_p),
                    rarity_n = COALESCE(excluded.rarity_n, options_flow_events.rarity_n),
                    rarity_cell = COALESCE(excluded.rarity_cell, options_flow_events.rarity_cell),
                    rarity_asof = COALESCE(excluded.rarity_asof, options_flow_events.rarity_asof)",
                params![
                    ev.id,
                    ingest_seq,
                    ev.ts,
                    ev.underlying,
                    ev.occ_symbol,
                    ev.right,
                    ev.strike,
                    ev.expiry,
                    ev.dte,
                    ev.event_type,
                    ev.contracts,
                    ev.premium_usd,
                    ev.direction.as_str(),
                    ev.direction_basis,
                    ev.n_prints,
                    exchanges,
                    ev.reason,
                    ev.caveat,
                    created,
                    ev.detail,
                    minutes_into_session(&ev.ts),
                    ev.mid_side,
                ],
            )?;
        }
        tx.commit()?;
        Ok(())
    }

    /// `db/options_flow.flag_events_spread`: the late-enrichment repair —
    /// spread_flag=1 on already-written events. Idempotent; rows changed.
    pub fn flag_events_spread(&mut self, ids: &[String]) -> Result<usize> {
        let mut changed = 0;
        for chunk in ids.chunks(500) {
            let marks = vec!["?"; chunk.len()].join(",");
            let sql = format!(
                "UPDATE options_flow_events SET spread_flag = 1 WHERE id IN ({marks}) AND (spread_flag IS NULL OR spread_flag = 0)"
            );
            changed += self.conn.execute(&sql, rusqlite::params_from_iter(chunk.iter()))?;
        }
        Ok(changed)
    }

    pub fn upsert_minutes(
        &mut self,
        rows: &[(String, String, i64, f64, f64, f64, i64, Option<String>)],
        window: &str,
    ) -> Result<()> {
        if rows.is_empty() {
            return Ok(());
        }
        let now = now_iso();
        let tx = self.conn.transaction()?;
        {
            let mut stmt = tx.prepare_cached(
                "INSERT INTO index_pressure_minutes (
                    underlying, date, window, minute, bull_usd, bear_usd,
                    unclassified_usd, n_prints, coverage, updated_at
                ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
                ON CONFLICT(underlying, date, window, minute) DO UPDATE SET
                    bull_usd = excluded.bull_usd,
                    bear_usd = excluded.bear_usd,
                    unclassified_usd = excluded.unclassified_usd,
                    n_prints = excluded.n_prints,
                    coverage = CASE
                        WHEN index_pressure_minutes.coverage IS NULL
                            THEN excluded.coverage
                        WHEN excluded.coverage IS NULL
                            OR excluded.coverage = index_pressure_minutes.coverage
                            THEN index_pressure_minutes.coverage
                        ELSE 'mixed'
                    END,
                    updated_at = excluded.updated_at",
            )?;
            for r in rows {
                stmt.execute(params![r.0, r.1, window, r.2, r.3, r.4, r.5, r.6, r.7, now])?;
            }
        }
        tx.commit()?;
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    pub fn record_day(
        &mut self,
        date: &str,
        window: &str,
        session_type: &str,
        minutes_observed: i64,
        syms: i64,
        coverage_modes: Option<&str>,
        writer_pid: i64,
        finalized: bool,
    ) -> Result<()> {
        let finalized_at: Option<String> = if finalized { Some(now_iso()) } else { None };
        self.conn.execute(
            "INSERT INTO index_pressure_days (
                date, window, session_type, minutes_observed, syms,
                coverage_modes, writer_pid, writers, finalized_at
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 1, ?8)
            ON CONFLICT(date, window) DO UPDATE SET
                session_type = excluded.session_type,
                minutes_observed = MAX(
                    index_pressure_days.minutes_observed, excluded.minutes_observed
                ),
                syms = MAX(index_pressure_days.syms, excluded.syms),
                coverage_modes = COALESCE(
                    excluded.coverage_modes, index_pressure_days.coverage_modes
                ),
                writers = index_pressure_days.writers
                    + (COALESCE(index_pressure_days.writer_pid, -1)
                       != COALESCE(excluded.writer_pid, -1)),
                writer_pid = excluded.writer_pid,
                finalized_at = COALESCE(
                    index_pressure_days.finalized_at, excluded.finalized_at
                )",
            params![
                date,
                window,
                session_type,
                minutes_observed,
                syms,
                coverage_modes,
                writer_pid,
                finalized_at
            ],
        )?;
        Ok(())
    }
}
