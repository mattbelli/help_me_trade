"""Named thresholds for the options-flow screener (design §5).

Every constant cites the design section and the public source that justified it.
Nothing inline, nothing magic — detection code imports from here only.
"""

from __future__ import annotations

# --- UV-1 vol/OI (design §5 / §2.3 — Barchart UOA screen) -------------------------
VOL_OI_RATIO_MIN = 1.25  # design §5 UV-1; Barchart UOA: vol/OI ≥ 1.25
MIN_CONTRACTS = 500  # design §5 UV-1; Barchart UOA: volume > 500
MIN_OI = 100  # design §5 UV-1; Barchart UOA: OI > 100
MIN_PRICE = 0.10  # design §5 UV-1; Barchart UOA: price > $0.10
MIN_PREMIUM_USD = 25_000  # design §5 UV-1 / §2.3; UW-inspired single-name floor
MIN_PREMIUM_INDEX_USD = 250_000  # design §5 UV-1 / §2.3; UW screener floor for index

# --- UV-2 volume z-score (design §5 / §2.3) ---------------------------------------
VOL_ZSCORE_MIN = 3.0  # design §5 UV-2; tunable; "2σ" circulates as CBOE folklore
ZSCORE_STD_FLOOR = 0.0  # set from backfill percentiles in 1b

# --- BLK-1 block (design §5) -----------------------------------------------------
BLOCK_MIN_CONTRACTS = 250  # design §5 BLK-1; single-name >99th-pct print size
BLOCK_MIN_PREMIUM_USD = 100_000  # design §5 BLK-1; block premium floor

# --- DTE bucketing + 0DTE exclusion (design §5 F-3 / §3.4) -----------------------
DTE_BUCKETS: tuple[tuple[int, int], ...] = (
    (2, 7),
    (8, 30),
    (31, 90),
    (91, 10_000),
)  # design §5 UV-2 buckets {2-7, 8-30, 31-90, 90+}
ZERO_DTE_EXCLUDE = True  # design §5 F-3; exclude DTE≤1 for index/mega-cap
INDEX_MEGACAP_SET = frozenset({"SPY", "QQQ", "IWM", "DIA"})  # design §5 F-3 / §2.3

# --- Retention + watchlist scope (design §4) -------------------------------------
EVENTS_PRUNE_DAYS = 180  # design §4; options_flow_events retention
DAILY_PRUNE_DAYS = 400  # design §4; options_flow_daily retention
OPTIONS_FLOW_MAX_UNDERLYINGS = 25  # design §4; watchlist cap


def dte_bucket(dte: int) -> str:
    """Return a stable DTE-bucket label (e.g. ``\"2-7\"``) for ``dte``.

    Falls through the ordered ``DTE_BUCKETS`` ranges; values outside all ranges
    (including DTE ≤ 1) return ``\"out\"``.
    """
    for lo, hi in DTE_BUCKETS:
        if lo <= dte <= hi:
            return f"{lo}-{hi}"
    return "out"
