//! Live condition-code pairing — the port of `signals/options_flow/live_pairing.py`
//! (`StreamingCondMatcher`, the frozen Addendum E join, streaming) and the
//! non-IO half of `shadow_pairing.py` (`ShadowPairing`: resolutions → codes
//! stamped onto the spine's prints, late MULTILEG repairs, counters, stats).
//!
//! Databento tcbbo prints carry direction but no condition code; Alpaca's
//! wildcard trades carry the code but no quote. Per (contract, price_cents,
//! size) key the k-th print on one tape pairs with the k-th on the other,
//! accepted when |Δt| ≤ TOL_NS on the shared OPRA participant timestamp. Two
//! bounded deviations from the batch join, both counted: a print whose twin has
//! not arrived within HORIZON of wall-clock time resolves '' and its k-slot is
//! tombstoned (never freed, so a late twin cannot shift the alignment behind
//! it); an idle key past the horizon is GC'd (k-count restarts on both sides).
//!
//! Where the Rust spine differs from the Python one in SHAPE (not semantics):
//! Python stamped codes onto shared print dicts in place; here the runner PULLS
//! `take_resolved()` at the top of every 1 s cycle and stamps the pending
//! batch + the sweep buffer's copies, and events register `attach_events`
//! backrefs (print_id → event id, 30 s TTL) so a late MULTILEG code queues the
//! same `spread_flag=1` repair. Prints the meta gate dropped never get stamped
//! (their resolutions age out — Python's early-cond TTL).
//!
//! Every DBN print fed in is resolved exactly once; Alpaca prints are fuel.

use crate::quotebook::{Shadow, ShadowStats};
use std::collections::{HashMap, VecDeque};

pub const TOL_NS: i64 = 100_000_000; // ±100 ms, Addendum E (tools/condition_labels.TOL_NS)
pub const HORIZON_NS: i64 = 600 * 1_000_000_000; // 10 min, above the open-burst queue lag
pub const MAX_PENDING: usize = 2_000_000; // measured ~484 B/print in Python; ~100 B here
const ATTACH_TTL_NS: i64 = 30_000_000_000; // event backrefs live long enough for the late repair
const RESOLVED_TTL_NS: i64 = 10_000_000_000; // unclaimed codes (meta-gated prints) age out
const REPAIR_QUEUE_MAX: usize = 10_000;
const LAG_SAMPLE_MAX: usize = 8192;
pub const MULTILEG_CODES: &str = "fghijklmnopqrs"; // constants.MULTILEG_CONDITIONS
const ENRICH_EDGES_NS: [i64; 4] = [10_000_000, 100_000_000, 1_000_000_000, 10_000_000_000];
const ENRICH_LABELS: [&str; 5] = ["≤10ms", "≤100ms", "≤1s", "≤10s", ">10s"];
const INDEX_ROOTS: [&str; 7] = ["SPX", "SPXW", "XSP", "VIX", "VIXW", "NDX", "NDXP"];

pub type Key = (String, i64, i64); // (occ, price_cents, size)

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Why {
    Matched,
    Tolerance,
    Expired,
    Evicted,
}

/// (print_id, cond, why, the Alpaca twin's shadow direction) — cond is "" and
/// the shadow default for everything but Matched.
pub type Resolution = (u64, String, Why, Shadow);

#[derive(Default)]
struct KeyState {
    d_next: u64,
    a_next: u64,
    pair_next: u64,
    /// k → Some((print_id, ts)) pending, None = tombstone (slot consumed).
    d_pend: HashMap<u64, Option<(u64, i64)>>,
    a_pend: HashMap<u64, (String, i64, Shadow)>,
    last_ns: i64,
}

pub struct Matcher {
    horizon_ns: i64,
    max_pending: usize,
    keys: HashMap<Key, KeyState>,
    expiry: VecDeque<(i64, Key, u64)>,
    last_gc_ns: i64,
    pub matched: u64,
    pub tolerance_rejects: u64,
    pub expired: u64,
    pub evicted: u64,
    pub cond_after_expiry: u64,
    pub alpaca_orphans: u64,
    pub pending_dbn: usize,
    pub pending_alpaca: usize,
    pub pending_dbn_peak: usize,
    pub pending_alpaca_peak: usize,
    pub keys_peak: usize,
}

impl Default for Matcher {
    fn default() -> Self {
        Self::new(HORIZON_NS, MAX_PENDING)
    }
}

impl Matcher {
    pub fn new(horizon_ns: i64, max_pending: usize) -> Self {
        Self {
            horizon_ns,
            max_pending,
            keys: HashMap::new(),
            expiry: VecDeque::new(),
            last_gc_ns: 0,
            matched: 0,
            tolerance_rejects: 0,
            expired: 0,
            evicted: 0,
            cond_after_expiry: 0,
            alpaca_orphans: 0,
            pending_dbn: 0,
            pending_alpaca: 0,
            pending_dbn_peak: 0,
            pending_alpaca_peak: 0,
            keys_peak: 0,
        }
    }

    fn key_state(&mut self, key: &Key, now_ns: i64) -> &mut KeyState {
        if !self.keys.contains_key(key) {
            self.keys.insert(key.clone(), KeyState { last_ns: now_ns, ..Default::default() });
            self.keys_peak = self.keys_peak.max(self.keys.len());
        }
        let ks = self.keys.get_mut(key).unwrap();
        ks.last_ns = now_ns;
        ks
    }

    pub fn on_dbn_print(&mut self, key: Key, ts_ns: i64, print_id: u64, now_ns: i64, out: &mut Vec<Resolution>) {
        let ks = self.key_state(&key, now_ns);
        let k = ks.d_next;
        ks.d_next += 1;
        ks.d_pend.insert(k, Some((print_id, ts_ns)));
        self.pending_dbn += 1;
        self.pending_dbn_peak = self.pending_dbn_peak.max(self.pending_dbn);
        self.expiry.push_back((now_ns, key.clone(), k));
        self.pair(&key, out);
        if self.pending_dbn > self.max_pending {
            self.evict_over_cap(out);
        }
    }

    pub fn on_alpaca_trade(&mut self, key: Key, ts_ns: i64, cond: &str, shadow: Shadow, now_ns: i64, out: &mut Vec<Resolution>) {
        let ks = self.key_state(&key, now_ns);
        let k = ks.a_next;
        ks.a_next += 1;
        ks.a_pend.insert(k, (cond.to_string(), ts_ns, shadow));
        self.pending_alpaca += 1;
        self.pending_alpaca_peak = self.pending_alpaca_peak.max(self.pending_alpaca);
        self.pair(&key, out);
    }

    fn pair(&mut self, key: &Key, out: &mut Vec<Resolution>) {
        let Some(ks) = self.keys.get_mut(key) else { return };
        while ks.pair_next < ks.d_next && ks.pair_next < ks.a_next {
            let k = ks.pair_next;
            ks.pair_next += 1;
            let entry = ks.d_pend.remove(&k).flatten();
            let (cond, a_ts, shadow) = ks.a_pend.remove(&k).expect("a_pend has k < a_next");
            self.pending_alpaca -= 1;
            let Some((print_id, d_ts)) = entry else {
                self.cond_after_expiry += 1; // slot expired before the twin arrived
                continue;
            };
            self.pending_dbn -= 1;
            if (a_ts - d_ts).abs() <= TOL_NS {
                self.matched += 1;
                out.push((print_id, cond, Why::Matched, shadow));
            } else {
                self.tolerance_rejects += 1;
                out.push((print_id, String::new(), Why::Tolerance, Shadow::default()));
            }
        }
    }

    /// Expire overdue slots and GC idle keys. Call every second.
    pub fn sweep(&mut self, now_ns: i64, out: &mut Vec<Resolution>) {
        let cutoff = now_ns - self.horizon_ns;
        while let Some((t, _, _)) = self.expiry.front() {
            if *t > cutoff {
                break;
            }
            let (_, key, k) = self.expiry.pop_front().unwrap();
            let Some(ks) = self.keys.get_mut(&key) else { continue };
            let Some(slot) = ks.d_pend.get_mut(&k) else { continue }; // already paired
            let Some((print_id, _)) = slot.take() else { continue }; // already tombstoned
            self.pending_dbn -= 1;
            self.expired += 1;
            out.push((print_id, String::new(), Why::Expired, Shadow::default()));
        }
        if now_ns - self.last_gc_ns >= self.horizon_ns {
            self.last_gc_ns = now_ns;
            let dead: Vec<Key> = self.keys.iter().filter(|(_, ks)| ks.last_ns <= cutoff).map(|(k, _)| k.clone()).collect();
            for key in dead {
                let ks = self.keys.remove(&key).unwrap();
                for entry in ks.d_pend.into_values().flatten() {
                    self.pending_dbn -= 1;
                    self.expired += 1;
                    out.push((entry.0, String::new(), Why::Expired, Shadow::default()));
                }
                let n_a = ks.a_pend.len();
                self.alpaca_orphans += n_a as u64;
                self.pending_alpaca -= n_a;
            }
        }
    }

    fn evict_over_cap(&mut self, out: &mut Vec<Resolution>) {
        while self.pending_dbn > self.max_pending {
            let Some((_, key, k)) = self.expiry.pop_front() else { break };
            let Some(ks) = self.keys.get_mut(&key) else { continue };
            let Some(slot) = ks.d_pend.get_mut(&k) else { continue };
            let Some((print_id, _)) = slot.take() else { continue };
            self.pending_dbn -= 1;
            self.evicted += 1;
            out.push((print_id, String::new(), Why::Evicted, Shadow::default()));
        }
    }
}

// --- the pairing state the spine + sidecar share -------------------------------------------

pub fn is_multileg(cond: &str) -> bool {
    // Python: `cond in frozenset("fghijklmnopqrs")` — one whole code, exact.
    cond.len() == 1 && MULTILEG_CODES.contains(cond)
}

fn root(sym: &str) -> String {
    sym.chars().take_while(|c| c.is_ascii_alphabetic()).collect()
}

fn pctl(sorted: &[f64], q: f64) -> f64 {
    if sorted.is_empty() {
        return f64::NAN;
    }
    let i = ((sorted.len() - 1) as f64 * q).round() as usize;
    sorted[i.min(sorted.len() - 1)]
}

pub struct Pairing {
    pub m: Matcher,
    /// Matched codes awaiting the runner (print_id, cond, resolved_ns).
    resolved: Vec<(u64, String, i64)>,
    /// print_id → (event id, attached_ns) for the late-repair path.
    attached: HashMap<u64, (String, i64)>,
    repairs: VecDeque<String>,
    last_print_id: u64,
    pub stopping: bool,
    // counters (Python's names)
    pub dbn_seen: u64,
    pub alp_seen: u64,
    pub enriched: u64,
    pub repairs_queued: u64,
    pub repairs_dropped: u64,
    pub matcher_resets: u64,
    pub reconnects: u64,
    pub acked_wide: bool,
    pub enrich_buckets: [u64; 5],
    d_roots: HashMap<String, u64>,
    a_roots: HashMap<String, u64>,
    d_lags_ms: Vec<f64>,
    a_lags_ms: Vec<f64>,
    /// (written, dropped_non_session) — mirrored in by the sidecar's housekeeping for the snapshot.
    pub gth_stats: (u64, u64),
    /// Stage-A shadow: matched twins' Alpaca directions awaiting the runner,
    /// the runner's scores, and the quote book's counters (mirrored in by housekeeping).
    shadows: HashMap<u64, Shadow>,
    pub shadow: ShadowStats,
    pub book_json: serde_json::Value,
}

impl Default for Pairing {
    fn default() -> Self {
        Self::new()
    }
}

impl Pairing {
    pub fn new() -> Self {
        Self {
            m: Matcher::default(),
            resolved: Vec::new(),
            attached: HashMap::new(),
            repairs: VecDeque::new(),
            last_print_id: 0,
            stopping: false,
            dbn_seen: 0,
            alp_seen: 0,
            enriched: 0,
            repairs_queued: 0,
            repairs_dropped: 0,
            matcher_resets: 0,
            reconnects: 0,
            acked_wide: false,
            enrich_buckets: [0; 5],
            d_roots: HashMap::new(),
            a_roots: HashMap::new(),
            d_lags_ms: Vec::new(),
            a_lags_ms: Vec::new(),
            gth_stats: (0, 0),
            shadows: HashMap::new(),
            shadow: ShadowStats::default(),
            book_json: serde_json::Value::Null,
        }
    }

    /// The spine's raw-print tee (after symbology, BEFORE the meta gate — the
    /// same whole-market population the batch join labels). Returns the
    /// print_id (arrival ns, made strictly increasing) the runner stores on
    /// the print; None once stopping.
    pub fn on_dbn(&mut self, occ: &str, price_raw: i64, size: u32, ts_event: i64, now_ns: i64) -> Option<u64> {
        if self.stopping {
            return None;
        }
        let arrival = (now_ns as u64).max(self.last_print_id + 1);
        self.last_print_id = arrival;
        let cents = ((price_raw as f64 / 1e9) * 100.0).round() as i64;
        self.dbn_seen += 1;
        if self.d_lags_ms.len() < LAG_SAMPLE_MAX {
            self.d_lags_ms.push((now_ns - ts_event) as f64 / 1e6);
        }
        *self.d_roots.entry(root(occ)).or_default() += 1;
        let mut out = Vec::new();
        self.m.on_dbn_print((occ.to_string(), cents, size as i64), ts_event, arrival, now_ns, &mut out);
        self.absorb(out, now_ns);
        Some(arrival)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn on_alpaca(&mut self, sym: &str, t_ns: i64, cents: i64, size: i64, cond: &str, shadow: Shadow, now_ns: i64) {
        self.alp_seen += 1;
        if self.a_lags_ms.len() < LAG_SAMPLE_MAX {
            self.a_lags_ms.push((now_ns - t_ns) as f64 / 1e6);
        }
        *self.a_roots.entry(root(sym)).or_default() += 1;
        let mut out = Vec::new();
        self.m.on_alpaca_trade((sym.to_string(), cents, size), t_ns, cond, shadow, now_ns, &mut out);
        self.absorb(out, now_ns);
    }

    /// 1 s housekeeping: matcher expiry/GC + TTLs on the two side tables.
    pub fn sweep(&mut self, now_ns: i64) {
        let mut out = Vec::new();
        self.m.sweep(now_ns, &mut out);
        self.absorb(out, now_ns);
        if !self.resolved.is_empty() {
            let cutoff = now_ns - RESOLVED_TTL_NS;
            self.resolved.retain(|(_, _, t)| *t > cutoff);
        }
        if !self.attached.is_empty() {
            let cutoff = now_ns - ATTACH_TTL_NS;
            self.attached.retain(|_, (_, t)| *t > cutoff);
        }
    }

    fn absorb(&mut self, resolutions: Vec<Resolution>, now_ns: i64) {
        for (print_id, cond, why, shadow) in resolutions {
            let attached = self.attached.remove(&print_id);
            if why != Why::Matched {
                continue;
            }
            // every matched twin's shadow; the runner claims what it still holds
            self.shadows.insert(print_id, shadow);
            // Only matches carry a wait; print_id IS the print's arrival ns.
            let wait = now_ns - print_id as i64;
            let mut b = ENRICH_EDGES_NS.len();
            for (i, edge) in ENRICH_EDGES_NS.iter().enumerate() {
                if wait <= *edge {
                    b = i;
                    break;
                }
            }
            self.enrich_buckets[b] += 1;
            if let Some((event_id, _)) = attached {
                // The print's event already exists and this code says package
                // leg → the stored row is wrong; queue the id-exact repair.
                self.enriched += 1;
                if is_multileg(&cond) {
                    if self.repairs.len() >= REPAIR_QUEUE_MAX {
                        self.repairs.pop_front();
                        self.repairs_dropped += 1;
                    }
                    self.repairs.push_back(event_id);
                    self.repairs_queued += 1;
                }
            } else {
                self.resolved.push((print_id, cond, now_ns));
            }
        }
    }

    /// Runner, top of each cycle: every matched code not yet claimed. The
    /// caller stamps what it still holds (pending batch, sweep buffer).
    pub fn take_resolved(&mut self) -> HashMap<u64, String> {
        let mut map = HashMap::with_capacity(self.resolved.len());
        for (pid, cond, _) in self.resolved.drain(..) {
            map.insert(pid, cond);
        }
        self.enriched += map.len() as u64;
        map
    }

    /// Runner, top of each cycle (with `take_resolved`): matched twins' shadows.
    pub fn take_shadows(&mut self) -> HashMap<u64, Shadow> {
        std::mem::take(&mut self.shadows)
    }

    /// Runner, after emitting events: print_id → event id backrefs.
    pub fn attach_events(&mut self, refs: Vec<(u64, String)>, now_ns: i64) {
        for (pid, ev) in refs {
            self.attached.insert(pid, (ev, now_ns));
        }
    }

    pub fn drain_repairs(&mut self) -> Vec<String> {
        self.repairs.drain(..).collect()
    }

    /// New measurement epoch after an Alpaca gap (see shadow_pairing.py:
    /// one reconnect mis-ranks every hot key for the rest of the session).
    pub fn reset_matcher(&mut self) -> (usize, usize) {
        let dropped = (self.m.pending_dbn, self.m.pending_alpaca);
        self.m = Matcher::default();
        self.matcher_resets += 1;
        self.attached.clear();
        self.resolved.clear();
        self.shadows.clear();
        dropped
    }

    /// Counters for the UI snapshot (no side effects; the lag windows belong to the 60 s line).
    pub fn snapshot_json(&self, gth: (u64, u64)) -> serde_json::Value {
        let m = &self.m;
        let resolved = m.matched + m.tolerance_rejects + m.expired + m.evicted;
        serde_json::json!({
            "dbn_seen": self.dbn_seen, "alp_seen": self.alp_seen,
            "matched": m.matched, "resolved": resolved,
            "matched_pct": if resolved > 0 { (10000.0 * m.matched as f64 / resolved as f64).round() / 100.0 } else { 0.0 },
            "enriched": self.enriched, "repairs": self.repairs_queued,
            "tolerance": m.tolerance_rejects, "expired": m.expired, "evicted": m.evicted,
            "late_twin": m.cond_after_expiry, "orphans": m.alpaca_orphans,
            "enrich_buckets": { "le10ms": self.enrich_buckets[0], "le100ms": self.enrich_buckets[1], "le1s": self.enrich_buckets[2], "le10s": self.enrich_buckets[3], "gt10s": self.enrich_buckets[4] },
            "pending_dbn": m.pending_dbn, "pending_alpaca": m.pending_alpaca, "keys_peak": m.keys_peak,
            "acked_wide": self.acked_wide, "resets": self.matcher_resets, "reconnects": self.reconnects,
            "gth_written": gth.0, "gth_dropped": gth.1,
            "shadow": self.shadow.json(), "quote_book": self.book_json,
        })
    }

    pub fn stats_line(&mut self, gth: (u64, u64), rss_mb: f64) -> String {
        let m = &self.m;
        let resolved = m.matched + m.tolerance_rejects + m.expired + m.evicted;
        let n_match = m.matched.max(1) as f64;
        let buckets: Vec<String> = ENRICH_LABELS
            .iter()
            .zip(self.enrich_buckets.iter())
            .map(|(l, c)| format!("{l}={c}({:.1}%)", 100.0 * *c as f64 / n_match))
            .collect();
        let idx: Vec<String> = INDEX_ROOTS
            .iter()
            .map(|r| format!("{r}={}/{}", self.d_roots.get(*r).copied().unwrap_or(0), self.a_roots.get(*r).copied().unwrap_or(0)))
            .collect();
        let mut top_a: Vec<(&String, &u64)> = self.a_roots.iter().collect();
        top_a.sort_by(|a, b| b.1.cmp(a.1).then(a.0.cmp(b.0)));
        let top_a: Vec<String> = top_a.iter().take(8).map(|(r, n)| format!("{r}={n}")).collect();
        let mut d = std::mem::take(&mut self.d_lags_ms);
        let mut a = std::mem::take(&mut self.a_lags_ms);
        d.sort_by(|x, y| x.partial_cmp(y).unwrap());
        a.sort_by(|x, y| x.partial_cmp(y).unwrap());
        format!(
            "pairing: dbn={} alp={} matched={} ({:.1}% of {} resolved) enriched={} att_pend={} repairs={} \
             tol={} expired={} evicted={} late_twin={} orphans={} | enrich {} | \
             wire_lag_ms d p50={:.0} p95={:.0} a p50={:.0} p95={:.0} | pending d={} a={} keys_peak={} | \
             roots d={} a={} idx_d/a: {} | top_a={} | gth_w={} gth_drop={} | \
             acked_wide={} resets={} reconnects={} rss={rss_mb:.0}MB",
            self.dbn_seen, self.alp_seen, m.matched, 100.0 * m.matched as f64 / resolved.max(1) as f64, resolved,
            self.enriched, self.attached.len(), self.repairs_queued,
            m.tolerance_rejects, m.expired, m.evicted, m.cond_after_expiry, m.alpaca_orphans,
            buckets.join(" "),
            pctl(&d, 0.5), pctl(&d, 0.95), pctl(&a, 0.5), pctl(&a, 0.95),
            m.pending_dbn, m.pending_alpaca, m.keys_peak,
            self.d_roots.len(), self.a_roots.len(), idx.join(" "),
            top_a.join(" "), gth.0, gth.1,
            self.acked_wide, self.matcher_resets, self.reconnects,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const MS: i64 = 1_000_000;
    fn key() -> Key {
        ("SPXW260818C06400000".to_string(), 250, 10)
    }

    /// Feed both tapes merged by participant ts (arrival == ts), then flush.
    fn stream(d: &[(Key, i64)], a: &[(Key, i64, &str)]) -> (HashMap<u64, (String, Why)>, Matcher) {
        let mut ev: Vec<(i64, u8, usize)> = d.iter().enumerate().map(|(i, (_, t))| (*t, 0, i)).collect();
        ev.extend(a.iter().enumerate().map(|(j, (_, t, _))| (*t, 1, j)));
        ev.sort_by_key(|e| e.0); // stable
        let mut m = Matcher::default();
        let mut res = HashMap::new();
        let mut out = Vec::new();
        for (ts, side, idx) in &ev {
            if *side == 0 {
                m.on_dbn_print(d[*idx].0.clone(), *ts, *idx as u64, *ts, &mut out);
            } else {
                m.on_alpaca_trade(a[*idx].0.clone(), *ts, a[*idx].2, Shadow::default(), *ts, &mut out);
            }
        }
        let last = ev.last().map(|e| e.0).unwrap_or(0);
        m.sweep(last + 10 * HORIZON_NS, &mut out);
        for (pid, c, w, _) in out {
            assert!(res.insert(pid, (c, w)).is_none(), "a print resolved twice");
        }
        (res, m)
    }

    #[test]
    fn simple_match_and_symmetry() {
        let (res, m) = stream(&[(key(), 1000 * MS)], &[(key(), 1050 * MS, "a")]);
        assert_eq!(res[&0], ("a".to_string(), Why::Matched));
        assert_eq!((m.matched, m.pending_dbn, m.pending_alpaca), (1, 0, 0));
        let (res, _) = stream(&[(key(), 1050 * MS)], &[(key(), 1000 * MS, "f")]);
        assert_eq!(res[&0], ("f".to_string(), Why::Matched));
    }

    #[test]
    fn kth_pair_beyond_tolerance_resolves_empty_and_k_rank_is_tape_order() {
        let (res, m) = stream(&[(key(), 0)], &[(key(), TOL_NS + 1, "a")]);
        assert_eq!(res[&0], (String::new(), Why::Tolerance));
        assert_eq!(m.tolerance_rejects, 1);
        let d = [(key(), 0), (key(), 10 * MS), (key(), 20 * MS)];
        let a = [(key(), MS, "a"), (key(), 11 * MS, "b"), (key(), 21 * MS, "c")];
        let (res, _) = stream(&d, &a);
        assert_eq!(res[&0].0, "a");
        assert_eq!(res[&1].0, "b");
        assert_eq!(res[&2].0, "c");
    }

    #[test]
    fn unpaired_copies_expire_unlabeled() {
        let (res, m) = stream(&[(key(), 0), (key(), 10 * MS)], &[(key(), MS, "a")]);
        assert_eq!(res[&0], ("a".to_string(), Why::Matched));
        assert_eq!(res[&1], (String::new(), Why::Expired));
        assert_eq!(m.expired, 1);
    }

    #[test]
    fn expired_slot_stays_consumed_for_the_late_twin() {
        let mut m = Matcher::new(100 * MS, MAX_PENDING);
        let mut out = Vec::new();
        m.on_dbn_print(key(), 0, 0, 0, &mut out);
        m.on_dbn_print(key(), 300 * MS, 1, 95 * MS, &mut out);
        assert!(out.is_empty());
        m.sweep(150 * MS, &mut out);
        assert_eq!(out, vec![(0, String::new(), Why::Expired, Shadow::default())]);
        out.clear();
        m.on_alpaca_trade(key(), 90 * MS, "a", Shadow::default(), 160 * MS, &mut out);
        assert!(out.is_empty());
        assert_eq!(m.cond_after_expiry, 1);
        m.on_alpaca_trade(key(), 305 * MS, "b", Shadow::default(), 170 * MS, &mut out);
        assert_eq!(out, vec![(1, "b".to_string(), Why::Matched, Shadow::default())]);
    }

    #[test]
    fn pending_cap_evicts_oldest_and_idle_key_gc_counts_orphans() {
        let mut m = Matcher::new(HORIZON_NS, 2);
        let mut out = Vec::new();
        for i in 0..3i64 {
            m.on_dbn_print((format!("K{i}"), 100, 1), i * MS, i as u64, i * MS, &mut out);
        }
        assert_eq!(out, vec![(0, String::new(), Why::Evicted, Shadow::default())]);
        assert_eq!((m.evicted, m.pending_dbn), (1, 2));

        let mut m = Matcher::new(100 * MS, MAX_PENDING);
        let mut out = Vec::new();
        m.on_alpaca_trade(key(), 0, "a", Shadow::default(), 0, &mut out);
        assert_eq!(m.pending_alpaca, 1);
        m.sweep(150 * MS, &mut out);
        m.sweep(300 * MS, &mut out);
        assert_eq!((m.pending_alpaca, m.alpaca_orphans), (0, 1));
    }

    #[test]
    fn pairing_stamps_then_repairs() {
        let mut p = Pairing::new();
        let now = 1_000_000_000_000i64;
        // inline match: alpaca twin first, then the dbn print → resolved immediately
        p.on_alpaca("SPXW260818C06400000", now, 250, 10, "f", Shadow::default(), now);
        let pid = p.on_dbn("SPXW260818C06400000", 2_500_000_000, 10, now, now + 5 * MS).unwrap();
        let codes = p.take_resolved();
        assert_eq!(codes.get(&pid).map(String::as_str), Some("f"));
        assert_eq!(p.enrich_buckets[0], 1);
        // late match after the event exists → repair queued
        let pid2 = p.on_dbn("SPXW260818C06400000", 2_500_000_000, 10, now + 1_000 * MS, now + 1_000 * MS).unwrap();
        p.attach_events(vec![(pid2, "of_x_swp_1".into())], now + 1_500 * MS);
        p.on_alpaca("SPXW260818C06400000", now + 1_000 * MS, 250, 10, "g", Shadow::default(), now + 2_000 * MS);
        assert_eq!(p.drain_repairs(), vec!["of_x_swp_1".to_string()]);
        assert!(p.take_resolved().is_empty());
        // non-multileg late code → no repair
        let pid3 = p.on_dbn("SPXW260818C06400000", 2_500_000_000, 10, now + 3_000 * MS, now + 3_000 * MS).unwrap();
        p.attach_events(vec![(pid3, "of_x_swp_2".into())], now + 3_100 * MS);
        p.on_alpaca("SPXW260818C06400000", now + 3_000 * MS, 250, 10, "a", Shadow::default(), now + 3_200 * MS);
        assert!(p.drain_repairs().is_empty());
        assert!(is_multileg("f") && is_multileg("s") && !is_multileg("a") && !is_multileg(""));
    }
}
