//! Python-compatible number/time formatting — the A/B compares `reason` strings,
//! spot prices (`str(float)`), and RFC3339 timestamps verbatim, so every helper
//! here reproduces the exact CPython output for the same double.

/// Python `round(x, nd)` (correctly rounded, half-even) → f64.
pub fn round_nd(x: f64, nd: usize) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{:.*}", nd, x).parse().unwrap_or(x)
}

/// Python `f"{x:.nf}"`.
pub fn fixed(x: f64, nd: usize) -> String {
    format!("{:.*}", nd, x)
}

/// Python `f"{n:,}"` for a non-negative integer.
pub fn int_commas(n: i64) -> String {
    let s = n.abs().to_string();
    let g = group_thousands(&s);
    if n < 0 {
        format!("-{g}")
    } else {
        g
    }
}

fn group_thousands(digits: &str) -> String {
    let b = digits.as_bytes();
    let mut out = String::with_capacity(digits.len() + digits.len() / 3);
    for (i, c) in b.iter().enumerate() {
        if i > 0 && (b.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(*c as char);
    }
    out
}

/// Python `str(float)` / `repr(float)`: shortest round-trip, always with a
/// fractional part or exponent. Prices never hit the exponent regime
/// (< 1e-4 or >= 1e16), so plain shortest formatting + ".0" is exact.
pub fn py_repr(x: f64) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let a = x.abs();
    if a != 0.0 && (a < 1e-4 || a >= 1e16) {
        return py_repr_exp(x);
    }
    let s = format!("{x}");
    if s.contains('.') {
        s
    } else {
        format!("{s}.0")
    }
}

fn py_repr_exp(x: f64) -> String {
    // Python: mantissa shortest, exponent at least 2 digits, e.g. 1e-05, 1.5e+16
    let s = format!("{x:e}"); // e.g. "1e-5", "1.5e16"
    let (m, e) = s.split_once('e').unwrap_or((&s, "0"));
    let ev: i32 = e.parse().unwrap_or(0);
    let sign = if ev < 0 { '-' } else { '+' };
    format!("{m}e{sign}{:02}", ev.abs())
}

/// `datetime.fromtimestamp(t, timezone.utc).isoformat()` for a float epoch-seconds t.
pub fn iso_from_unix(t: f64) -> String {
    // CPython _PyTime_DoubleToDenominator with ROUND_HALF_EVEN, denominator 1e6.
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
    let secs = intpart as i64;
    let us = frac as i64;
    let (y, mo, d, h, mi, s) = civil(secs);
    if us == 0 {
        format!("{y:04}-{mo:02}-{d:02}T{h:02}:{mi:02}:{s:02}+00:00")
    } else {
        format!("{y:04}-{mo:02}-{d:02}T{h:02}:{mi:02}:{s:02}.{us:06}+00:00")
    }
}

/// `datetime.now(UTC).isoformat()` — write-time stamps (never compared).
pub fn now_iso() -> String {
    let d = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    iso_from_unix(d.as_secs_f64())
}

fn civil(secs: i64) -> (i64, u32, u32, u32, u32, u32) {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (y, m, d) = civil_from_days(days);
    (
        y,
        m,
        d,
        (rem / 3600) as u32,
        ((rem % 3600) / 60) as u32,
        (rem % 60) as u32,
    )
}

// Howard Hinnant's days→civil.
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn commas_and_repr() {
        assert_eq!(int_commas(200000), "200,000");
        assert_eq!(py_repr(100.0), "100.0");
        assert_eq!(py_repr(100.1), "100.1");
        assert_eq!(py_repr(0.1 + 0.2), "0.30000000000000004");
        assert_eq!(py_repr(1e-05), "1e-05");
    }

    #[test]
    fn iso() {
        assert_eq!(iso_from_unix(1_755_525_600.0), "2025-08-18T14:00:00+00:00");
        assert_eq!(
            iso_from_unix(1_755_525_600.5),
            "2025-08-18T14:00:00.500000+00:00"
        );
    }
}

/// Python `f"{x:g}"` (precision 6): shortest of fixed/exponent with trailing
/// zeros stripped. Used by `_fmt_strike` for non-integer strikes.
pub fn py_g6(x: f64) -> String {
    if x == 0.0 {
        return "0".into();
    }
    if !x.is_finite() {
        return py_repr(x);
    }
    // 6 significant digits, correctly rounded.
    let sci = format!("{:.5e}", x); // e.g. "5.25000e1"
    let (mant, exp) = sci.split_once('e').unwrap();
    let exp: i32 = exp.parse().unwrap();
    if (-4..6).contains(&exp) {
        let decimals = (5 - exp).max(0) as usize;
        let s = format!("{:.*}", decimals, x);
        strip_trailing_zeros(&s)
    } else {
        let m = strip_trailing_zeros(mant);
        let sign = if exp < 0 { '-' } else { '+' };
        format!("{m}e{sign}{:02}", exp.abs())
    }
}

fn strip_trailing_zeros(s: &str) -> String {
    if s.contains('.') {
        let t = s.trim_end_matches('0');
        t.trim_end_matches('.').to_string()
    } else {
        s.to_string()
    }
}

/// `int(x)` on a finite float: truncation toward zero.
pub fn py_int(x: f64) -> i64 {
    x.trunc() as i64
}

/// `round(x)` (no ndigits): half-even to an integer.
pub fn py_round0(x: f64) -> i64 {
    x.round_ties_even() as i64
}

#[cfg(test)]
mod g_tests {
    use super::*;
    #[test]
    fn g6() {
        assert_eq!(py_g6(52.5), "52.5");
        assert_eq!(py_g6(152.75), "152.75");
        assert_eq!(py_g6(0.5), "0.5");
        assert_eq!(py_g6(1234.5), "1234.5");
        assert_eq!(py_g6(12345.5), "12345.5");
        assert_eq!(py_g6(2.125), "2.125");
        assert_eq!(py_g6(0.001), "0.001");
        assert_eq!(py_g6(0.00001), "1e-05");
    }
}

/// CPython ≥ 3.12 `sum()` over floats: Neumaier-compensated summation from 0.
/// Plain sequential `+=` loops in the Python are NOT this — only `sum(...)` calls.
pub fn py_sum(xs: impl IntoIterator<Item = f64>) -> f64 {
    let mut f = 0.0f64;
    let mut c = 0.0f64;
    for x in xs {
        let t = f + x;
        if f.abs() >= x.abs() {
            c += (f - t) + x;
        } else {
            c += (x - t) + f;
        }
        f = t;
    }
    if c != 0.0 && c.is_finite() {
        f += c;
    }
    f
}

#[cfg(test)]
mod sum_tests {
    use super::*;
    #[test]
    fn neumaier() {
        // Python 3.12: sum([0.1]*10) == 1.0 exactly; naive gives 0.9999999999999999
        assert_eq!(py_sum(std::iter::repeat(0.1).take(10)), 1.0);
    }
}
