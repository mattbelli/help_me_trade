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

# --- F-1 hygiene condition codes (design §5 F-1 / §2.1) ---------------------------
# Fallback sets only — the runtime fetches /v1beta1/options/meta/conditions/trade and
# may override; detection functions take the sets as parameters with these defaults.
CANCEL_CONDITIONS = frozenset({"A", "C", "E", "G"})  # design §5 F-1; OPRA cancels
LATE_OOS_CONDITIONS = frozenset({"B", "D", "F", "H"})  # design §5 F-1; late/out-of-sequence
EXT_HOURS_CONDITIONS = frozenset({"v"})  # design §5 F-1; extended-hours

# --- F-2 spread suppression (design §5 F-2 / §2.1) --------------------------------
MULTILEG_CONDITIONS = frozenset("fghijklmnopqrs")  # design §2.1; MLET + combo codes
SPREAD_CLUSTER_WINDOW_MS = 1000  # design §5 F-2
SPREAD_CLUSTER_MIN_STRIKES = 2  # design §5 F-2
MULTILEG_RATIO_MAX = 0.4  # design §5 F-2; UW-inspired

# --- BLK-1 block (design §5) -----------------------------------------------------
BLOCK_MIN_CONTRACTS = 250  # design §5 BLK-1; single-name >99th-pct print size
BLOCK_MIN_PREMIUM_USD = 100_000  # design §5 BLK-1; block premium floor

# --- DIR-1 aggressor tagging (design §5 DIR-1 / §2.2) -----------------------------
QUOTE_EPS = 0.01  # design §5 DIR-1; quote-rule tolerance
PRESSURE_MIN_USD = 250_000  # design §5 DIR-1
PRESSURE_MIN_CLASSIFIED = 0.5  # design §5 DIR-1

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
# Cap contracts fetched per underlying for EOD bars (rank by OI desc). Keeps the
# nightly scan bounded: ~25 × 800 ≪ Alpaca's practical request budget; most
# liquid contracts win so UV-1 still sees the names that can clear MIN_OI.
OPTIONS_FLOW_MAX_CONTRACTS_PER_UNDERLYING = 800  # design §4; EOD scan contract cap

# --- Intraday poll (design §4) ---------------------------------------------------
ACTIVE_SET_PER_UNDERLYING = 60  # design §4; top contracts per underlying in active set
ACTIVE_SET_REFRESH_SECONDS = 300  # design §4; refresh active set every 5 min
POLL_QUOTE_CHUNK = 100  # design §4; quote/trades chunk size (Alpaca ≤100 symbols)

# --- Convergence watch + push budget (design §6b / §5) -----------------------------
CONV_RVOL_MIN = 1.5  # design §6b; underlying RVOL component threshold
CONV_LEVEL_ATR = 0.5  # design §6b; |price − level| ≤ 0.5×ATR(14, daily)
SOLO_PUSH_PREMIUM_USD = 1_000_000  # design §6b; solo flow pushes only above this
OPTIONS_FLOW_MAX_PUSH_PER_DAY = 5  # design §5 dedup/budget; phone push ceiling


def dte_bucket(dte: int) -> str:
    """Return a stable DTE-bucket label (e.g. ``\"2-7\"``) for ``dte``.

    Falls through the ordered ``DTE_BUCKETS`` ranges; values outside all ranges
    (including DTE ≤ 1) return ``\"out\"``.
    """
    for lo, hi in DTE_BUCKETS:
        if lo <= dte <= hi:
            return f"{lo}-{hi}"
    return "out"
