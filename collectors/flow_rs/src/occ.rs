//! OCC symbol handling — `dbn_stream.occ_from_osi`, `imports.option_symbol.parse_occ_symbol`,
//! `poll._meta_from_occ` (`_dte_days`), and the `ts_recv → ts_ms` round trip
//! `dbn_stream._on_record` + `intraday.normalize_print` perform (parity trap #1).

use crate::pyfmt::py_int;
use chrono::NaiveDate;

/// Contract metadata as `_meta_from_occ` returns it (the poll lane's active set
/// is OFF in replay, so this is the only meta source).
#[derive(Clone, Debug)]
pub struct Meta {
    pub occ: String,
    pub underlying: String,
    pub right: &'static str, // "call" | "put"
    pub strike: f64,
    pub expiry: String, // YYYY-MM-DD
    pub dte: i64,
}

/// `raw.replace(" ", "").upper()` — OSI roots never contain a space.
pub fn occ_from_osi(raw: &str) -> String {
    raw.chars().filter(|c| *c != ' ').flat_map(|c| c.to_uppercase()).collect()
}

/// `^([A-Z]{1,6})(\d{6})([CP])(\d{8})$` + date validity + strike > 0, then
/// `_dte_days(expiry, today)`; None ⇒ the print is `skipped_no_meta`.
pub fn meta_from_occ(occ: &str, today: NaiveDate) -> Option<Meta> {
    let b = occ.as_bytes();
    let n = b.len();
    if !(16..=21).contains(&n) {
        return None;
    }
    let root_len = n - 15;
    if !(1..=6).contains(&root_len) {
        return None;
    }
    let root = &b[..root_len];
    if !root.iter().all(|c| c.is_ascii_uppercase()) {
        return None;
    }
    let ymd = &b[root_len..root_len + 6];
    if !ymd.iter().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let right = b[root_len + 6];
    if right != b'C' && right != b'P' {
        return None;
    }
    let strike_raw = &b[root_len + 7..];
    if strike_raw.len() != 8 || !strike_raw.iter().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let num = |s: &[u8]| -> i64 { s.iter().fold(0i64, |a, c| a * 10 + (c - b'0') as i64) };
    let yy = num(&ymd[0..2]);
    let mm = num(&ymd[2..4]);
    let dd = num(&ymd[4..6]);
    let expiry = NaiveDate::from_ymd_opt((2000 + yy) as i32, mm as u32, dd as u32)?;
    let strike_int = num(strike_raw); // thousandths of a dollar
    if strike_int <= 0 {
        return None;
    }
    // Decimal(strike_raw) / 1000 → float(): the correctly rounded double of the
    // exact decimal, which is what parsing "int.frac" yields.
    let strike: f64 = format!("{}.{:03}", strike_int / 1000, strike_int % 1000)
        .parse()
        .ok()?;
    let dte = (expiry - today).num_days();
    Some(Meta {
        occ: occ.to_string(),
        underlying: String::from_utf8_lossy(root).into_owned(),
        right: if right == b'C' { "call" } else { "put" },
        strike,
        expiry: expiry.format("%Y-%m-%d").to_string(),
        dte,
    })
}

/// `ts_ms` exactly as the Python spine derives it:
///   `t = datetime.fromtimestamp(ts_recv / 1e9, UTC).isoformat()` (µs, half-even),
///   `normalize_print` → `datetime.fromisoformat(t).timestamp()` (= total_µs / 1e6),
///   `int(ts * 1000)`.
pub fn ts_ms_from_ts_recv(ts_recv: u64) -> i64 {
    let t = ts_recv as f64 / 1e9;
    let (secs, us) = split_us_half_even(t);
    let total_us = secs * 1_000_000 + us;
    let ts = total_us as f64 / 1e6;
    py_int(ts * 1000.0)
}

/// CPython `_PyTime_DoubleToDenominator(t, 1e6, ROUND_HALF_EVEN)`.
pub fn split_us_half_even(t: f64) -> (i64, i64) {
    let mut intpart = t.trunc();
    let mut frac = t - intpart;
    frac *= 1e6;
    frac = frac.round_ties_even();
    if frac >= 1e6 {
        frac -= 1e6;
        intpart += 1.0;
    } else if frac < 0.0 {
        frac += 1e6;
        intpart -= 1.0;
    }
    (intpart as i64, frac as i64)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn osi() {
        assert_eq!(occ_from_osi("IPI   261218P00050000"), "IPI261218P00050000");
        let m = meta_from_occ("IPI261218P00050000", NaiveDate::from_ymd_opt(2026, 8, 17).unwrap()).unwrap();
        assert_eq!((m.underlying.as_str(), m.right, m.strike, m.expiry.as_str(), m.dte), ("IPI", "put", 50.0, "2026-12-18", 123));
        assert!(meta_from_occ("WOLF1261218P00050000", NaiveDate::from_ymd_opt(2026, 8, 17).unwrap()).is_none());
        assert!(meta_from_occ("SPY260231C00500000", NaiveDate::from_ymd_opt(2026, 8, 17).unwrap()).is_none());
        let m = meta_from_occ("SPXW260818C06450500", NaiveDate::from_ymd_opt(2026, 8, 17).unwrap()).unwrap();
        assert_eq!(m.strike, 6450.5);
    }

    #[test]
    fn ts_round_trip() {
        // 1786973400404975539 → 1786973400.4049756 → µs 404976 → ms 404
        assert_eq!(ts_ms_from_ts_recv(1786973400404975539), 1786973400404);
    }
}
