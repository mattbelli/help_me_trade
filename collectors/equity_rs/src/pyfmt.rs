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

/// Python `f"{x:,.nf}"` (thousands separators on the integer part).
pub fn fixed_commas(x: f64, nd: usize) -> String {
    let s = fixed(x, nd);
    let (neg, s) = match s.strip_prefix('-') {
        Some(r) => (true, r.to_string()),
        None => (false, s),
    };
    let (int_part, frac) = match s.find('.') {
        Some(i) => (&s[..i], &s[i..]),
        None => (&s[..], ""),
    };
    let mut out = String::with_capacity(s.len() + 8);
    if neg {
        out.push('-');
    }
    out.push_str(&group_thousands(int_part));
    out.push_str(frac);
    out
}

/// Python `f"{n:,}"` for a non-negative integer.
pub fn int_commas(n: i64) -> String {
    let s = n.abs().to_string();
    let g = group_thousands(&s);
    if n < 0 { format!("-{g}") } else { g }
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
    if s.contains('.') { s } else { format!("{s}.0") }
}

fn py_repr_exp(x: f64) -> String {
    // Python: mantissa shortest, exponent at least 2 digits, e.g. 1e-05, 1.5e+16
    let s = format!("{x:e}"); // e.g. "1e-5", "1.5e16"
    let (m, e) = s.split_once('e').unwrap_or((&s, "0"));
    let ev: i32 = e.parse().unwrap_or(0);
    let sign = if ev < 0 { '-' } else { '+' };
    format!("{m}e{sign}{:02}", ev.abs())
}

/// Python float floor-division `a // b` (b > 0), reproducing CPython's algorithm.
pub fn py_floordiv(a: f64, b: f64) -> f64 {
    let m = a % b; // fmod
    let mut div = (a - m) / b;
    if m != 0.0 && ((b < 0.0) != (m < 0.0)) {
        div -= 1.0;
    }
    if div != 0.0 {
        let fl = div.floor();
        if div - fl > 0.5 { fl + 1.0 } else { fl }
    } else {
        (a / b).signum() * 0.0
    }
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
    (y, m, d, (rem / 3600) as u32, ((rem % 3600) / 60) as u32, (rem % 60) as u32)
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
        assert_eq!(fixed_commas(1234567.891, 1), "1,234,567.9");
        assert_eq!(fixed_commas(0.5, 1), "0.5");
        assert_eq!(fixed_commas(999.999, 2), "1,000.00");
        assert_eq!(int_commas(200000), "200,000");
        assert_eq!(py_repr(100.0), "100.0");
        assert_eq!(py_repr(100.1), "100.1");
        assert_eq!(py_repr(0.1 + 0.2), "0.30000000000000004");
        assert_eq!(py_repr(1e-05), "1e-05");
    }

    #[test]
    fn iso() {
        assert_eq!(iso_from_unix(1_755_525_600.0), "2025-08-18T14:00:00+00:00");
        assert_eq!(iso_from_unix(1_755_525_600.5), "2025-08-18T14:00:00.500000+00:00");
    }

    #[test]
    fn floordiv() {
        assert_eq!(py_floordiv(25.0, 10.0), 2.0);
        assert_eq!(py_floordiv(1_755_525_600_123.0, 10_000.0), 175_552_560.0);
    }
}
