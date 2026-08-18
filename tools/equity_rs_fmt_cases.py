#!/usr/bin/env python
"""Emit CPython formatting cases for collectors/equity_rs/examples/fmtcheck.rs.
Run: .venv/bin/python tools/equity_rs_fmt_cases.py | (cd collectors/equity_rs && cargo run -q --release --example fmtcheck)"""
import random
from datetime import datetime, timezone

random.seed(7)
out = []
def emit(kind, x, s):
    out.append(f"{kind}\t{x!r}\t{s}")
for _ in range(4000):
    p = random.choice([random.uniform(0.01, 5), random.uniform(5, 500), random.uniform(500, 5000), round(random.uniform(1, 800), 2), round(random.uniform(1, 800), 4)])
    n = p * random.choice([100, 1000, 12345, 200000, 1_000_000])
    emit("round4", p, repr(round(p, 4)))
    emit("round2", n, repr(round(n, 2)))
    emit("round1", n / 7, repr(round(n / 7, 1)))
    emit("fixed1c", n / 1e6, f"{n / 1e6:,.1f}")
    emit("fixed2c", p, f"{p:,.2f}")
    emit("fixed0", n / 3.3, f"{n / 3.3:.0f}")
    emit("repr", p, repr(p))
    t = 1_755_525_600 + random.random() * 86400 + random.choice([0, 0.5, 0.0000005, 0.9999995])
    emit("iso", t, datetime.fromtimestamp(t, timezone.utc).isoformat())
    ms = t * 1000.0
    emit("floordiv", ms, repr(ms // 10_000.0))
# ties for round-half-even sanity
for x in [0.125, 0.375, 2.5, 3.5, 1234.5, 0.00005, 12.345, 100.0, 1e-05, 0.5]:
    emit("round2", x, repr(round(x, 2)))
    emit("fixed0", x, f"{x:.0f}")
    emit("repr", x, repr(x))
    emit("fixed1c", x, f"{x:,.1f}")
print("\n".join(out))
