//! Formatting parity check against CPython. Feed it lines produced by
//! tools/equity_rs_fmt_cases.py: `<kind>\t<input>\t<expected>`; prints mismatches.
#[path = "../src/pyfmt.rs"]
mod pyfmt;
use std::io::BufRead;
fn main() {
    let (mut n, mut bad) = (0u64, 0u64);
    for line in std::io::stdin().lock().lines() {
        let line = line.unwrap();
        let mut it = line.split('\t');
        let (kind, input, expected) = (it.next().unwrap(), it.next().unwrap(), it.next().unwrap());
        let x: f64 = input.parse().unwrap();
        let got = match kind {
            "round4" => pyfmt::py_repr(pyfmt::round_nd(x, 4)),
            "round2" => pyfmt::py_repr(pyfmt::round_nd(x, 2)),
            "round1" => pyfmt::py_repr(pyfmt::round_nd(x, 1)),
            "fixed1c" => pyfmt::fixed_commas(x, 1),
            "fixed2c" => pyfmt::fixed_commas(x, 2),
            "fixed0" => pyfmt::fixed(x, 0),
            "repr" => pyfmt::py_repr(x),
            "iso" => pyfmt::iso_from_unix(x),
            "floordiv" => pyfmt::py_repr(pyfmt::py_floordiv(x, 10_000.0)),
            _ => panic!("kind {kind}"),
        };
        n += 1;
        if got != expected {
            bad += 1;
            if bad <= 20 { eprintln!("MISMATCH {kind} {input}: rust={got} py={expected}"); }
        }
    }
    println!("{n} cases, {bad} mismatches");
    if bad > 0 { std::process::exit(1); }
}
