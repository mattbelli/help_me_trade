"""Strategy registry — config/data for independently toggleable trading strategies."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from config.settings import trading_config
from db.accounts import default_execution_account, list_accounts
from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

_DIP_BUY_DEFAULT_PARAMS: dict[str, Any] = {
    "oversold_3d_max": -5.0,
    "mkt_down_3d_max": -1.5,
    "healthy_required": True,
    "side": "long",
    "stop_pct": -3.0,
    "first_target_pct": 3.0,
    "trail_pct": 11.0,
    "max_holding_days": 20,
}

# Refined dip = the validated dip edge PLUS a "far-from-support" filter
# (far_support_min: stock must be >5% above its nearest swing-support level).
# Backtest (research/decision-engine-data-layer): the filter roughly doubled the
# RECENT excess over SPY (+1.6% -> +3%/trade). NOTE: this refinement is NOT
# block-bootstrap-validated to the same standard as the base dip — it is a live
# forward-test candidate, not a calibrated promise. Seeded DISABLED + UNBOUND.
_DIP_REFINED_DEFAULT_PARAMS: dict[str, Any] = {
    **_DIP_BUY_DEFAULT_PARAMS,
    "far_support_min": 5.0,
}

# Tactical sleeve: validated dip edge gated to steep yield-curve regimes (term_spread > 0.5),
# 10-day fixed hold (stop/target/trail disabled). Seeded DISABLED + UNBOUND.
_STEEPYC_DIP_DEFAULT_PARAMS: dict[str, Any] = {
    "side": "long",
    "healthy_required": True,
    "oversold_3d_max": -5.0,
    "mkt_down_3d_max": -1.5,
    "yc_min_spread": 0.5,
    "stop_pct": None,
    "first_target_pct": None,
    "trail_pct": None,
    "max_holding_days": 10,
}

_VIX_SPIKE_DEFAULT_PARAMS: dict[str, Any] = {
    "side": "long",
    "vix_spike_min": 28.0,
    "vix_calm_max": 22.0,
    "vix_calm_lookback": 12,
    "vix_calm_min_days": 8,
    "vix_exit_below": 20.0,
    "max_holding_days": 60,
}

# Regime-local (Tier-B) sleeves — References/REGIME_SLEEVES_SPEC.md. Paper-only,
# PROPOSE-only, seeded DISABLED + UNBOUND; the founder arms them manually. Scanned by
# signals.regime_sleeves (post-close loop), NOT the generic strategy scan. Kill lines
# are enforced mechanically (auto-disable through this table's enabled flag / params).
_GENERALS_DIP_DEFAULT_PARAMS: dict[str, Any] = {
    "side": "long",
    "sma_window": 50,
    "ret_3d_max": -3.5,
    # Exit = pure 10-trading-day fixed hold: trading/exit_engine maps this id to the
    # dip evaluator, and explicit None stop/target/trail legs disable every price leg
    # (the steepyc_dip pattern) — only the time cap remains.
    "stop_pct": None,
    "first_target_pct": None,
    "trail_pct": None,
    "max_holding_days": 10,
}

_VOL_EVENT_DEFAULT_PARAMS: dict[str, Any] = {
    "side": "long",
    "symbol": "SPY",
    "drawdown_pct": 5.0,
    "vix_min": 25.0,
    "dd_max_holding_days": 30,
    "tf_max_holding_days": 21,
    # Per-trigger enable flags — the sleeve's kill lines flip these (params_json IS
    # the existing strategy-state mechanism; no new state system).
    "triggers": {
        "drawdown_stab": {"enabled": True},
        "term_flip": {"enabled": True},
    },
}

_EARNINGS_DRIFT_DEFAULT_PARAMS: dict[str, Any] = {
    # entry (mirrors is_earnings_drift_entry defaults — the validated values)
    "blowout_pct": 5.0,
    "entry_lead_td": 10,
    "exit_lead_td": 1,
    "min_gap_td": 40,
    "max_gap_td": 90,
    # exit (evaluate_earnings_exit): sell ~exit_lead_td before next earnings; max_holding_days is a
    # stale-calendar SAFETY cap (holding is ~9 td: enter ~10 td before, exit ~1 td before).
    "max_holding_days": 15,
}

# LLM paper books (References/LLM_BOOKS_PROGRAM_SPEC_2026-07-23.md): free-rein
# nightly-driver books, NOT scan strategies — the generic scan and the exit
# engine both skip the "book" kind. DISABLED + propose; founder arms manually
# and sets notional caps at arming (spec §9). Account ids verified live 07-23.
_BOOK_STRATEGIES = (
    ("book_deepseek", "Deep Seek (LLM book)", "acct_alpaca_deepseek"),
    ("book_fable", "Fable (LLM book)", "acct_alpaca_fable"),
    ("book_panel", "Claude Panel (LLM book)", "acct_alpaca_open"),
)

_VALID_EXIT_MODES = frozenset({"auto", "propose"})
_VALID_ENTRY_MODES = frozenset({"auto", "propose"})


def init_strategy_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                account_id TEXT,
                notional_usd REAL NOT NULL DEFAULT 500,
                exit_mode TEXT NOT NULL DEFAULT 'propose',
                entry_mode TEXT NOT NULL DEFAULT 'propose',
                params_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        # Idempotent column adds (per-strategy risk caps; NULL = uncapped).
        existing = {row[1] for row in conn.execute("PRAGMA table_info(strategies)")}
        if "max_concurrent_positions" not in existing:
            conn.execute("ALTER TABLE strategies ADD COLUMN max_concurrent_positions INTEGER")
        if "max_deployed_usd" not in existing:
            conn.execute("ALTER TABLE strategies ADD COLUMN max_deployed_usd REAL")
        if "entry_mode" not in existing:
            conn.execute(
                "ALTER TABLE strategies ADD COLUMN entry_mode TEXT NOT NULL DEFAULT 'propose'"
            )
        if "universe" not in existing:
            conn.execute("ALTER TABLE strategies ADD COLUMN universe TEXT")
            # Backfill EXISTING rows to their current behavior (the active
            # watchlist) — never silently repoint a live strategy to a broad
            # universe. New strategies seed to 'sp500'; users opt in via the UI.
            conn.execute(
                "UPDATE strategies SET universe = 'active_watchlist' "
                "WHERE universe IS NULL"
            )
        if "last_rebalanced_month" not in existing:
            conn.execute(
                "ALTER TABLE strategies ADD COLUMN last_rebalanced_month TEXT"
            )
        # Percent-of-equity sizing. NULL = size off the static notional_usd (the
        # existing behaviour); set = notional is (pct/100 * account equity) at scan
        # time, so the sleeve compounds like the PC-validated config.
        if "notional_pct" not in existing:
            conn.execute("ALTER TABLE strategies ADD COLUMN notional_pct REAL")
        # Vol-targeted sizing opt-in, per strategy. Defaults 0 so every existing
        # strategy keeps its exact sizing; the global trading.volatility_enabled
        # master switch must ALSO be on before this does anything.
        if "vol_target" not in existing:
            conn.execute(
                "ALTER TABLE strategies ADD COLUMN vol_target INTEGER NOT NULL DEFAULT 0"
            )
        conn.commit()


def _row_to_strategy(row: sqlite3.Row) -> dict[str, Any]:
    try:
        params = json.loads(row["params_json"] or "{}")
    except json.JSONDecodeError:
        params = {}
    if not isinstance(params, dict):
        params = {}
    max_concurrent = row["max_concurrent_positions"]
    max_deployed = row["max_deployed_usd"]
    notional_pct = row["notional_pct"]
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "account_id": row["account_id"],
        "notional_usd": float(row["notional_usd"]),
        # None = size off notional_usd; set = percent of account equity at scan time.
        "notional_pct": float(notional_pct) if notional_pct is not None else None,
        "exit_mode": row["exit_mode"],
        "entry_mode": row["entry_mode"],
        # Opt this strategy into vol-targeted sizing (still gated by the global
        # trading.volatility_enabled master switch).
        "vol_target": bool(row["vol_target"]),
        "params": params,
        # NULL is only reachable via a half-applied migration or a hand-inserted
        # row; fall back to the NARROW active watchlist, never silently broaden.
        "universe": row["universe"] or "active_watchlist",
        "max_concurrent_positions": (
            int(max_concurrent) if max_concurrent is not None else None
        ),
        "max_deployed_usd": (
            float(max_deployed) if max_deployed is not None else None
        ),
        "last_rebalanced_month": row["last_rebalanced_month"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_strategies(
    *,
    enabled_only: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_strategy_tables(db_path)
    where = "WHERE enabled = 1" if enabled_only else ""
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                f"""
                SELECT id, name, enabled, account_id, notional_usd, notional_pct,
                       exit_mode, entry_mode, params_json, universe,
                       max_concurrent_positions, max_deployed_usd,
                       last_rebalanced_month, vol_target, created_at, updated_at
                FROM strategies
                {where}
                ORDER BY created_at
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [_row_to_strategy(row) for row in rows]


def get_strategy(
    strategy_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    init_strategy_tables(db_path)
    with _connect(db_path) as conn:
        try:
            row = conn.execute(
                """
                SELECT id, name, enabled, account_id, notional_usd, notional_pct,
                       exit_mode, entry_mode, params_json, universe,
                       max_concurrent_positions, max_deployed_usd,
                       last_rebalanced_month, vol_target, created_at, updated_at
                FROM strategies
                WHERE id = ?
                """,
                (strategy_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return _row_to_strategy(row) if row else None


def update_strategy(
    strategy_id: str,
    *,
    enabled: bool | None = None,
    account_id: str | None = ...,  # type: ignore[assignment]
    notional_usd: float | None = None,
    # Sentinel default (not None) so an explicit None CLEARS it — the user must be
    # able to switch a strategy back from percent-of-equity to static-dollar sizing.
    notional_pct: float | None = ...,  # type: ignore[assignment]
    exit_mode: str | None = None,
    entry_mode: str | None = None,
    params: dict[str, Any] | None = None,
    universe: str | None = None,
    max_concurrent_positions: int | None = ...,  # type: ignore[assignment]
    max_deployed_usd: float | None = ...,  # type: ignore[assignment]
    last_rebalanced_month: str | None = ...,  # type: ignore[assignment]
    vol_target: bool | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_strategy_tables(db_path)
    existing = get_strategy(strategy_id, db_path=db_path)
    if existing is None:
        raise ValueError(f"unknown strategy {strategy_id!r}")

    if exit_mode is not None and exit_mode not in _VALID_EXIT_MODES:
        raise ValueError(f"exit_mode must be one of {sorted(_VALID_EXIT_MODES)}")

    if entry_mode is not None and entry_mode not in _VALID_ENTRY_MODES:
        raise ValueError(f"entry_mode must be one of {sorted(_VALID_ENTRY_MODES)}")

    if notional_usd is not None and notional_usd < 0:
        raise ValueError("notional_usd must be >= 0")

    if (
        notional_pct is not ...
        and notional_pct is not None
        and not 0 < notional_pct <= 100
    ):
        raise ValueError("notional_pct must be > 0 and <= 100")

    if (
        max_concurrent_positions is not ...
        and max_concurrent_positions is not None
        and max_concurrent_positions < 0
    ):
        raise ValueError("max_concurrent_positions must be >= 0")

    if (
        max_deployed_usd is not ...
        and max_deployed_usd is not None
        and max_deployed_usd < 0
    ):
        raise ValueError("max_deployed_usd must be >= 0")

    fields: list[str] = []
    values: list[Any] = []

    if enabled is not None:
        fields.append("enabled = ?")
        values.append(1 if enabled else 0)
    if account_id is not ...:
        fields.append("account_id = ?")
        values.append(account_id)
    if notional_usd is not None:
        fields.append("notional_usd = ?")
        values.append(float(notional_usd))
    if notional_pct is not ...:
        fields.append("notional_pct = ?")
        values.append(float(notional_pct) if notional_pct is not None else None)
    if exit_mode is not None:
        fields.append("exit_mode = ?")
        values.append(exit_mode)
    if entry_mode is not None:
        fields.append("entry_mode = ?")
        values.append(entry_mode)
    if vol_target is not None:
        fields.append("vol_target = ?")
        values.append(1 if vol_target else 0)
    if params is not None:
        fields.append("params_json = ?")
        values.append(json.dumps(params))
    if universe is not None:
        cleaned_universe = universe.strip()
        if not cleaned_universe:
            raise ValueError("universe must be a non-empty string")
        fields.append("universe = ?")
        values.append(cleaned_universe)
    if max_concurrent_positions is not ...:
        fields.append("max_concurrent_positions = ?")
        values.append(
            int(max_concurrent_positions)
            if max_concurrent_positions is not None
            else None
        )
    if max_deployed_usd is not ...:
        fields.append("max_deployed_usd = ?")
        values.append(
            float(max_deployed_usd) if max_deployed_usd is not None else None
        )
    if last_rebalanced_month is not ...:
        fields.append("last_rebalanced_month = ?")
        values.append(last_rebalanced_month)

    if not fields:
        return existing

    fields.append("updated_at = ?")
    values.append(_now())
    values.append(strategy_id)

    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE strategies SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        conn.commit()

    updated = get_strategy(strategy_id, db_path=db_path)
    assert updated is not None
    return updated


def _resolve_seed_account_id(*, db_path: Path) -> str | None:
    """Non-default execution-capable account if exactly one; else default; else NULL."""
    exec_accounts = [
        a
        for a in list_accounts(db_path=db_path)
        if a.get("enabled")
        and a.get("execution_capable")
        and not a.get("is_default")
    ]
    if len(exec_accounts) == 1:
        return str(exec_accounts[0]["id"])
    default = default_execution_account(db_path=db_path)
    return str(default["id"]) if default else None


def _insert_strategy(
    conn: sqlite3.Connection,
    *,
    strategy_id: str,
    name: str,
    enabled: bool,
    account_id: str | None,
    notional: float,
    params: dict[str, Any],
    now: str,
    universe: str = "sp500",
    exit_mode: str = "auto",
) -> None:
    conn.execute(
        """
        INSERT INTO strategies (
            id, name, enabled, account_id, notional_usd, exit_mode,
            params_json, universe, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            strategy_id,
            name,
            1 if enabled else 0,
            account_id,
            notional,
            exit_mode,
            json.dumps(params),
            universe,
            now,
            now,
        ),
    )


def seed_default_strategies(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Idempotent seed — registers each built-in strategy when missing.

    Each strategy is seeded independently (checked by id), so adding a new
    built-in here back-fills it on the next boot without disturbing existing
    rows or their user-edited enable/account/notional settings.
    """
    init_strategy_tables(db_path)
    cfg = trading_config()
    now = _now()

    need_dip_buy = get_strategy("dip_buy", db_path=db_path) is None
    need_dip_refined = get_strategy("dip_refined", db_path=db_path) is None
    need_steepyc = get_strategy("steepyc_dip", db_path=db_path) is None
    need_vix = get_strategy("vix_spike", db_path=db_path) is None
    need_earnings = get_strategy("earnings_drift", db_path=db_path) is None
    need_session_node = get_strategy("session_node_spy", db_path=db_path) is None
    need_momentum = get_strategy("momentum_sp500", db_path=db_path) is None
    need_generals = get_strategy("generals_dip", db_path=db_path) is None
    need_vol_event = get_strategy("vol_event", db_path=db_path) is None
    need_books = [
        (sid, name, account_id)
        for sid, name, account_id in _BOOK_STRATEGIES
        if get_strategy(sid, db_path=db_path) is None
    ]
    if (
        not need_dip_buy
        and not need_dip_refined
        and not need_steepyc
        and not need_vix
        and not need_earnings
        and not need_session_node
        and not need_momentum
        and not need_generals
        and not need_vol_event
        and not need_books
    ):
        return

    seed_account_id = _resolve_seed_account_id(db_path=db_path) if need_dip_buy else None

    with _connect(db_path) as conn:
        if need_dip_buy:
            _insert_strategy(
                conn,
                strategy_id="dip_buy",
                name="Dip-Buy",
                enabled=bool(cfg.get("dip_strategy_enabled", False)),
                account_id=seed_account_id,
                notional=float(cfg.get("dip_strategy_notional_usd", 500) or 500),
                params=_DIP_BUY_DEFAULT_PARAMS,
                now=now,
            )

        if need_dip_refined:
            # Disabled + UNBOUND by default — the user binds it to a dedicated
            # forward-test account in the UI (ideally separate from dip_buy's so
            # the two edges don't commingle). Never auto-arms.
            _insert_strategy(
                conn,
                strategy_id="dip_refined",
                name="Dip-Buy + S/R filter",
                enabled=False,
                account_id=None,
                notional=500.0,
                params=_DIP_REFINED_DEFAULT_PARAMS,
                now=now,
            )

        if need_steepyc:
            # Tactical 20% sleeve — steep yield-curve dip with 10d fixed hold.
            # Seeded DISABLED + UNBOUND; paper-fenced until founder arms it.
            _insert_strategy(
                conn,
                strategy_id="steepyc_dip",
                name="Steep-Curve Dip (tactical)",
                enabled=False,
                account_id=None,
                notional=15000.0,
                params=_STEEPYC_DIP_DEFAULT_PARAMS,
                now=now,
                exit_mode="auto",
                universe="sp500",
            )
            conn.execute(
                "UPDATE strategies SET entry_mode = 'auto' WHERE id = ?",
                ("steepyc_dip",),
            )

        if need_vix:
            # Tactical 10% sleeve — VIX-spike reversion (buy-fear SPY long). DISABLED + UNBOUND;
            # paper-fenced until the founder arms it. ~$7.5k = 10% of the ~$75k book (founder tunes).
            _insert_strategy(
                conn,
                strategy_id="vix_spike",
                name="VIX-Spike Reversion (tactical)",
                enabled=False,
                account_id=None,
                notional=7500.0,
                params=_VIX_SPIKE_DEFAULT_PARAMS,
                now=now,
                exit_mode="auto",
                universe="spy",
            )
            conn.execute(
                "UPDATE strategies SET entry_mode = 'auto' WHERE id = ?",
                ("vix_spike",),
            )

        if need_earnings:
            # Disabled + UNBOUND by default — the user binds a dedicated STRAT2 forward-test account in the
            # UI and enables it. exit_mode 'propose' so the first exits are phone-approved (safe default).
            _insert_strategy(
                conn,
                strategy_id="earnings_drift",
                name="Earnings Drift",
                enabled=False,
                account_id=None,
                notional=500.0,
                params=_EARNINGS_DRIFT_DEFAULT_PARAMS,
                now=now,
                exit_mode="propose",
            )

        if need_session_node:
            # SPY-only propose-only session-node reversion — seeded DISABLED; user arms via
            # Settings → Strategies. Falls back to default execution account when unbound.
            _insert_strategy(
                conn,
                strategy_id="session_node_spy",
                name="Session-Node Reversion (SPY)",
                enabled=False,
                account_id=None,
                notional=500.0,
                params={"symbol": "SPY"},
                now=now,
                exit_mode="propose",
                universe=None,
            )

        if need_generals:
            # Regime-local Tier-B sleeve (paper pilot) — top-30 3d-flush, 10td fixed
            # hold. DISABLED + UNBOUND + propose-only (session-node pattern); founder
            # arms manually. Universe is the sleeve's own monthly point-in-time top-30
            # (universe column unused → None). Max 5 concurrent per the spec.
            _insert_strategy(
                conn,
                strategy_id="generals_dip",
                name="Generals Dip (regime-local)",
                enabled=False,
                account_id=None,
                notional=1000.0,
                params=_GENERALS_DIP_DEFAULT_PARAMS,
                now=now,
                exit_mode="propose",
                universe=None,
            )
            conn.execute(
                "UPDATE strategies SET max_concurrent_positions = 5 WHERE id = ?",
                ("generals_dip",),
            )

        if need_vol_event:
            # Regime-local Tier-B sleeve (paper pilot) — SPY-only vol-shock termination
            # (drawdown_stab + term_flip triggers; existing vix_spike stays a separate
            # strategy but counts toward the shared cluster cap). DISABLED + UNBOUND +
            # propose-only; founder arms manually.
            _insert_strategy(
                conn,
                strategy_id="vol_event",
                name="Vol-Event Reversion (regime-local)",
                enabled=False,
                account_id=None,
                notional=1000.0,
                params=_VOL_EVENT_DEFAULT_PARAMS,
                now=now,
                exit_mode="propose",
                universe=None,
            )
            conn.execute(
                "UPDATE strategies SET max_concurrent_positions = 1 WHERE id = ?",
                ("vol_event",),
            )

        if need_momentum:
            # Residual-momentum portfolio — seeded DISABLED + UNBOUND. Ships full-auto +
            # summary alerts (paper-fenced); founder binds a dedicated paper account and
            # enables when ready. Rebalance execution is Inc-3.
            _insert_strategy(
                conn,
                strategy_id="momentum_sp500",
                name="Residual-Momentum Portfolio (S&P 500)",
                enabled=False,
                account_id=None,
                notional=50000.0,
                params={},
                now=now,
                exit_mode="propose",
                universe="sp500",
            )
            conn.execute(
                "UPDATE strategies SET entry_mode = 'auto' WHERE id = ?",
                ("momentum_sp500",),
            )

        for sid, name, account_id in need_books:
            _insert_strategy(
                conn,
                strategy_id=sid,
                name=name,
                enabled=False,
                account_id=account_id,
                notional=500.0,
                params={},
                now=now,
                exit_mode="propose",
                universe=None,
            )

        conn.commit()
