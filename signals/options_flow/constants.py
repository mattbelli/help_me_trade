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
ZSCORE_STD_FLOOR = 1242.65  # research/options_flow/thresholds.md 2026-07-09 (0.1 x median bucket std, 23-name backfill)

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
# design §4; watchlist cap. Raised 25→30 (2026-07-14): the cap took the
# alphabetically-first N of a sorted watchlist, so SPY/TSLA/XLE/XLF/SLV were
# silently never watched. Over the cap is now a loud warning, not a quiet drop.
OPTIONS_FLOW_MAX_UNDERLYINGS = 30
# Cap contracts fetched per underlying for EOD bars (rank by OI desc). Keeps the
# nightly scan bounded: ~25 × 800 ≪ Alpaca's practical request budget; most
# liquid contracts win so UV-1 still sees the names that can clear MIN_OI.
OPTIONS_FLOW_MAX_CONTRACTS_PER_UNDERLYING = 800  # design §4; EOD scan contract cap

# --- Intraday poll (design §4) ---------------------------------------------------
ACTIVE_SET_PER_UNDERLYING = 60  # design §4; top contracts per underlying in active set

# Chain-snapshot fetch for the intraday active set. The vendor page default is 100
# contracts ALPHABETICAL — and an OCC sorts expiry, then C before P, then strike — so a
# bare call returns one expiry of CALLS and never reaches a put. Measured 2026-07-14:
# SPY has 13,730 live contracts across 35 expiries (6,865 puts), and the bare call the
# poll had always used returned 100 calls at one expiry = 0.7% of the chain, which made
# put_net structurally zero for SPY/QQQ/TSLA. Same trap the chain viewer hit and fixed
# on 2026-07-09 (BUILD.md); the poll was never carried across.
# Cost measured the same day: SPY's full chain = 14 pages in 1.16s; ~18s for the whole
# watchlist per 300s refresh, ~0.3% of the 10,000/min market-data budget.
CHAIN_SNAPSHOT_PAGE_LIMIT = 1000  # VENDOR: Alpaca documents limit max=1000 per page
CHAIN_SNAPSHOT_MAX_PAGES = 20  # SELF: SPY needed 14 on 2026-07-14; over this logs loudly
ACTIVE_SET_REFRESH_SECONDS = 300  # design §4; refresh active set every 5 min
POLL_QUOTE_CHUNK = 100  # design §4; quote/trades chunk size (Alpaca ≤100 symbols)
POLL_COLD_START_LOOKBACK_MINUTES = 30  # cursor floor for cursor-less contracts; bounds the
# restart refetch burst (2026-07-09: a mid-day cold start re-pulled ~440k SPY prints while the
# 20k day_prints cap kept only ~the last 30 min of them anyway)

# --- SWP-1 sweeps (design §5; Phase 3b — needs streaming timestamps) ------------
SWEEP_WINDOW_MS = 500  # design §5; same-contract prints within this of the first print
SWEEP_MIN_EXCHANGES = 3  # design §5; distinct exchange codes for an intermarket sweep
SPLIT_MIN_PRINTS = 5  # design §5; split variant — same window, one exchange
SWEEP_CLOSE_GRACE_MS = 1_000  # close a cluster only after late prints can no longer join

# --- OPRA websocket stream (design §5/§7; probe 2026-07-09) --------------------
STREAM_TRADES_MAX = 1000  # design §7; our trade-sub budget (vendor acked 1,500+)
# --- Quote budgets (re-derived 2026-07-15 from live measurement) ------------------
# Probed against the live OPRA ws: Alpaca ACKs exactly 1,000 option-quote subs and
# 405s ("symbol limit exceeded") at 2,000. Delivery at 1,000 subs measured 14.4
# quotes/s/contract ≈ 0.09 cores — the 07-09 soak's "150 subs ≈ 52% of a core" was
# ~39x high, and HOT_QUOTE_MAX=60 was sized off it. Budgets below fill the vendor
# cap with 50 slots of headroom for subscribe/unsubscribe churn.
OPRA_QUOTE_SUB_CAP = 1000  # vendor-enforced, measured 2026-07-15
# Standing near-the-money/high-$-volume contracts, pre-positioned so the FIRST print
# on a hot contract classifies against a live quote (reactive subs miss it).
QUOTE_ANCHOR_BUDGET = 600
HOT_QUOTE_MAX = 350  # reactive escalation pool (was 60 — sized off the wrong soak)

# Active-set selection quotas (measured, 07-14 market-wide tape: >=100k-premium
# prints put 65% of their premium in DTE>=90 and only 3.6% in DTE<=2; index/megacap
# 0-1DTE prints are DISCARDED by ZERO_DTE_EXCLUDE downstream, so subscribing them
# wastes the slot — 57-60% of SPY/QQQ's slots before this quota).
LONG_DTE_THRESHOLD_DAYS = 46
ACTIVE_SET_LONG_DTE_MIN = 15  # per underlying, when supply exists

# Pre-open warm window: the OI seed needs only prior-session data, so let the poll
# run shortly before the bell — subscribed through the open instead of ~50s late.
PREOPEN_WARM_MINUTES = 10
# msg/s ≈ 52% of a core on the event loop; hottest contracts dominate prints, so 60 keeps
# most DIR-1 coverage at a fraction of the flood
HOT_QUOTE_TTL_SECONDS = 300  # design §7; quote sub lifetime after last print
QUOTE_STALE_FOR_DIR1_SECONDS = 5  # design §5 DIR-1; stream quote freshness (round 3b)

# --- Convergence watch + push budget (design §6b / §5) -----------------------------
CONV_RVOL_MIN = 1.5  # design §6b; underlying RVOL component threshold
CONV_LEVEL_ATR = 0.5  # design §6b; |price − level| ≤ 0.5×ATR(14, daily)
SOLO_PUSH_PREMIUM_USD = 1_000_000  # design §6b; solo flow pushes only above this
# Phone-push daily ceiling lives in Settings → alerts.max_push_per_day
# (config/settings.py: alerts_config); design §5 dedup/budget.


def dte_bucket(dte: int) -> str:
    """Return a stable DTE-bucket label (e.g. ``\"2-7\"``) for ``dte``.

    Falls through the ordered ``DTE_BUCKETS`` ranges; values outside all ranges
    (including DTE ≤ 1) return ``\"out\"``.
    """
    for lo, hi in DTE_BUCKETS:
        if lo <= dte <= hi:
            return f"{lo}-{hi}"
    return "out"
