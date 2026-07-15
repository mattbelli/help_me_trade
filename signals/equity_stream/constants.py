"""Equity tape collector constants — every threshold carries its justification."""

# Local proxy the API's market hub connects to instead of upstream (the stocks
# stream allows ONE connection per account — measured 2026-07-15, second
# connection: {'code': 406, 'msg': 'connection limit exceeded'}).
EQUITY_TAPE_PROXY_HOST = "127.0.0.1"
EQUITY_TAPE_PROXY_PORT = 8767
# Per-proxy-client send queue; a slow/stuck hub client must never backpressure
# the wildcard hot path — overflow drops newest and counts it.
PROXY_CLIENT_QUEUE_MAX = 2000

# NBBO freshness gate for direction classification. Quote staleness measured
# 2026-07-15 (quote_decay): 2s-old quotes agree with fresh ones 78.2% of the
# time and failures are mostly SIGN FLIPS — so past this age we say
# "undetermined" rather than guess. Stream delivery lag is ~14ms, so a stale
# entry means the symbol's book went quiet, not that our copy is behind.
NBBO_FRESH_MS = 2_000

# --- ISO sweeps (SIP condition "F") -------------------------------------------------
# An intermarket sweep executes one parent order as simultaneous child prints
# across venues; we aggregate per symbol inside a short window and report the
# cluster, not each child print.
SWEEP_WINDOW_MS = 1_000
SWEEP_MIN_NOTIONAL_USD = 500_000.0
SWEEP_MIN_VENUES = 3
SWEEP_COOLDOWN_SECONDS = 30.0

# --- print-velocity spikes (micro-RVOL) ---------------------------------------------
# Spike = a completed bucket vs the name's OWN trailing per-bucket EMA — never a
# cross-sectional threshold, so a quiet name waking up scores like a megacap
# accelerating. Evaluated on bucket close (≤10s latency; perception, not HFT).
VELOCITY_BUCKET_SECONDS = 10
VELOCITY_WARMUP_BUCKETS = 15  # ~2.5 min of its own baseline before eligible
VELOCITY_SPIKE_RATIO = 10.0
VELOCITY_MIN_PRINTS = 30  # absolute floor: 3 prints vs 0.2 baseline is noise
VELOCITY_EMA_ALPHA = 0.05
VELOCITY_COOLDOWN_SECONDS = 300.0
# A spike bucket feeds the EMA capped at 3x baseline so one burst doesn't
# inflate the baseline and mask the next real one.
VELOCITY_EMA_SPIKE_CAP_RATIO = 3.0
# |buy-sell| / gross below this → direction "mixed" (two-sided battle).
VELOCITY_MIXED_NET_FRACTION = 0.2

# --- spread stats (calibration only — NO events in v1) ------------------------------
# Liquidity-vacuum detection needs a measured baseline of normal spread
# behavior first; until then we collect rolling per-symbol spread samples and
# log the top wideners each stats interval. No fabricated thresholds.
SPREAD_SAMPLE_EVERY = 16
SPREAD_BASELINE_SAMPLES = 64

STATS_INTERVAL_SECONDS = 60
FLUSH_INTERVAL_SECONDS = 2.0
