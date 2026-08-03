"""Intraday options-flow poll cycle (design §4) — REST transport, adapter-injected.

All mutable state lives in a module-level ``PollState``; ``api/main.py`` owns
nothing. Fail-soft per underlying/chunk — the cycle never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from db import options_flow as of_db
from imports.option_symbol import parse_occ_symbol
from signals.options_flow.constants import (
    ACTIVE_SET_LONG_DTE_MIN,
    ACTIVE_SET_PER_UNDERLYING,
    ACTIVE_SET_REFRESH_SECONDS,
    CANCEL_CONDITIONS,
    CHAIN_SNAPSHOT_MAX_PAGES,
    CHAIN_SNAPSHOT_PAGE_LIMIT,
    EXT_HOURS_CONDITIONS,
    GAUGE_FETCH_UNDERLYINGS,
    GAUGE_LANE_BUDGET,
    GAUGE_UNDERLYINGS,
    GAUGE_ZERO_DTE_PICKS,
    INDEX_MEGACAP_SET,
    LATE_OOS_CONDITIONS,
    LONG_DTE_THRESHOLD_DAYS,
    POLL_COLD_START_LOOKBACK_MINUTES,
    POLL_QUOTE_CHUNK,
    QUOTE_ANCHOR_BUDGET,
    STREAM_TRADES_MAX,
    ZERO_DTE_EXCLUDE,
)
from signals.options_flow.detect import detect_uv1
from signals.options_flow.intraday import (
    _ts_ms_to_iso,
    aggregate_directional_pressure,
    classify_direction,
    detect_block,
    is_hygiene_excluded,
    normalize_print,
    tag_likely_spreads,
)
from signals.options_flow.scan import (
    _bar_volume_close,
    _dte_days,
    _int_oi,
    _normalize_right,
    eligible_underlyings,
)

logger = logging.getLogger(__name__)

_TRADES_CHUNK = POLL_QUOTE_CHUNK
_DAY_PRINTS_CAP = 20_000
_MS_PER_SEC = 1000


def _session_start_iso(today_et: str) -> str:
    return f"{today_et}T13:30:00Z"


def _day_dollar_volume(snap: dict, trade: dict) -> float:
    """Rank contracts by money traded today, not recency.

    The prior key ``(ts_ms, size)`` made the active set "the 60 most recently
    traded contracts" — 0DTE churn wins that by construction, while a $2M block
    from three minutes ago loses to a 1-lot from two seconds ago. Day volume x
    last price approximates premium traded today; falls back to the last trade's
    notional at the bell, when dailyBar hasn't formed yet.
    """
    try:
        price = float(trade.get("p") or 0)
    except (TypeError, ValueError):
        price = 0.0
    day = snap.get("dailyBar") or snap.get("daily_bar") or {}
    try:
        vol = float(day.get("v") or 0) if isinstance(day, dict) else 0.0
    except (TypeError, ValueError):
        vol = 0.0
    if vol > 0 and price > 0:
        return vol * price
    try:
        return price * float(trade.get("s") or 0)
    except (TypeError, ValueError):
        return 0.0


def _skip_index_zero_dte(underlying: str, dte: int | None) -> bool:
    """0-1DTE prints on index/megacap names are DISCARDED downstream by
    ZERO_DTE_EXCLUDE — subscribing them burns the slot for prints we throw away
    (measured 2026-07-15: 57-60% of SPY/QQQ's active set)."""
    return (
        ZERO_DTE_EXCLUDE
        and underlying in INDEX_MEGACAP_SET
        and dte is not None
        and dte <= 1
    )


def _select_active_picks(
    candidates: list[tuple[float, int, str]], limit: int
) -> list[tuple[float, int, str]]:
    """Top ``limit`` candidates by score with a long-dated floor.

    ``candidates`` are ``(score, dte, occ)``. Measured (07-14 market-wide tape):
    65% of >=100k-premium flow lives at DTE>=90, but a pure dollar-volume rank
    still skews short-dated — so guarantee ACTIVE_SET_LONG_DTE_MIN picks from
    DTE>=LONG_DTE_THRESHOLD_DAYS when supply exists, swapping out the
    lowest-scored short-dated picks.
    """
    ranked = sorted(candidates, key=lambda c: (c[0], c[2]), reverse=True)
    picks = list(ranked[:limit])
    have_long = sum(1 for c in picks if c[1] >= LONG_DTE_THRESHOLD_DAYS)
    need = ACTIVE_SET_LONG_DTE_MIN - have_long
    if need > 0:
        pool = [c for c in ranked[limit:] if c[1] >= LONG_DTE_THRESHOLD_DAYS][:need]
        j = 0
        for i in range(len(picks) - 1, -1, -1):
            if j >= len(pool):
                break
            if picks[i][1] < LONG_DTE_THRESHOLD_DAYS:
                picks[i] = pool[j]
                j += 1
    return picks


def anchor_quote_symbols(state: PollState | None = None) -> list[str]:
    """Standing quote anchors: the active set's top contracts by rank score,
    globally, clipped to QUOTE_ANCHOR_BUDGET.

    Pre-positioned so the FIRST print on a hot contract classifies against a
    live quote — the reactive pool only subscribes after a print, so it always
    misses the print that made the contract hot. Pre-open the scores fall back
    to prior-session OI (the seed), so anchors exist before the bell.
    """
    st = state if state is not None else _default_state
    ranked = sorted(
        st.active.items(),
        key=lambda kv: (float(kv[1].get("rank_score") or 0.0), kv[0]),
        reverse=True,
    )
    return [occ for occ, _ in ranked[:QUOTE_ANCHOR_BUDGET]]


def _parse_ts_ms(ts: str) -> int | None:
    try:
        s = str(ts).strip()
        if not s:
            return None
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _meta_from_contract(c: dict, today_et: str) -> dict | None:
    occ = str(c.get("symbol") or "").strip().upper()
    if not occ:
        return None
    expiry = str(c.get("expiration_date") or "").strip()[:10]
    dte = _dte_days(expiry, today_et)
    if dte is None:
        return None
    try:
        strike = float(c.get("strike_price"))
    except (TypeError, ValueError):
        return None
    u = str(c.get("underlying_symbol") or "").strip().upper()
    if not u:
        parsed = parse_occ_symbol(occ)
        u = parsed.underlying if parsed else ""
    if not u:
        return None
    return {
        "occ_symbol": occ,
        "underlying": u,
        "right": _normalize_right(c.get("type")),
        "strike": strike,
        "expiry": expiry,
        "dte": dte,
    }


def active_subscription_symbols(state: PollState | None = None) -> list[str]:
    """OCC symbols in the intraday active set (for OPRA stream trade subscriptions)."""
    st = state if state is not None else _default_state
    return sorted(st.active.keys())


def _meta_from_occ(occ: str, underlying_hint: str, today_et: str) -> dict | None:
    sym = occ.strip().upper()
    parsed = parse_occ_symbol(sym)
    if parsed is None:
        return None
    expiry = parsed.expiration.isoformat()
    dte = _dte_days(expiry, today_et)
    if dte is None:
        return None
    return {
        "occ_symbol": sym,
        "underlying": parsed.underlying or underlying_hint.strip().upper(),
        "right": "call" if parsed.right == "C" else "put",
        "strike": float(parsed.strike),
        "expiry": expiry,
        "dte": dte,
    }


def _near_expiries(today_et: str, n: int = 2) -> list[str]:
    """The next `n` calendar dates from today — the 0-1 DTE expiry candidates.

    Calendar days, not trading days: an expiration_date query for a non-expiry
    date just returns empty, which is cheaper than being clever.
    """
    from datetime import date as _date
    from datetime import timedelta as _td

    d0 = _date.fromisoformat(today_et)
    return [(d0 + _td(days=i)).isoformat() for i in range(n)]


def _trade_is_today(trade: dict, today_et: str) -> bool:
    t = trade.get("t")
    if not t:
        return False
    return str(t).startswith(today_et)


def _append_day_prints(
    state: PollState, underlying: str, prints: list[dict]
) -> None:
    if not prints:
        return
    u = underlying.strip().upper()
    bucket = state.day_prints.setdefault(u, [])
    bucket.extend(prints)
    overflow = len(bucket) - _DAY_PRINTS_CAP
    if overflow > 0:
        del bucket[:overflow]
        logger.warning(
            "options_flow poll: capped day_prints for %s at %s (dropped %s oldest)",
            u,
            _DAY_PRINTS_CAP,
            overflow,
        )


@dataclass
class PollState:
    day: str = ""
    active: dict[str, dict] = field(default_factory=dict)
    cursors: dict[str, str] = field(default_factory=dict)
    oi: dict[str, int] = field(default_factory=dict)
    oi_seed: dict[str, list[str]] = field(default_factory=dict)
    oi_day: str | None = None
    last_active_refresh_ms: int = 0
    day_prints: dict[str, list[dict]] = field(default_factory=dict)
    condition_sets_loaded: bool = False
    cancel_conditions: frozenset[str] = CANCEL_CONDITIONS
    late_oos_conditions: frozenset[str] = LATE_OOS_CONDITIONS
    ext_hours_conditions: frozenset[str] = EXT_HOURS_CONDITIONS
    condition_descriptions: dict[str, str] = field(default_factory=dict)

    def reset(self) -> None:
        self.active.clear()
        self.cursors.clear()
        self.oi.clear()
        self.oi_seed.clear()
        self.oi_day = None
        self.last_active_refresh_ms = 0
        self.day_prints.clear()


_default_state = PollState()


def run_poll_once(
    adapter: Any,
    *,
    symbols: list[str],
    today_et: str,
    now_ms: int,
    state: PollState | None = None,
    db_path: Path | None = None,
    emit_blocks: bool = True,
) -> dict:
    """One intraday poll cycle. Returns counters; never raises.

    ``emit_blocks=False`` when the whole-market spine owns BLK-1 (provider=
    databento since 2026-08-03): both lanes see the same trade with different
    timestamps, so two detectors means two rows for one block.
    """
    st = state if state is not None else _default_state
    counters: dict[str, Any] = {
        "prints": 0,
        "blocks": 0,
        "uv1": 0,
        "pressure": 0,
        "events": [],
    }
    try:
        _run_poll_cycle(
            adapter,
            symbols=symbols,
            today_et=today_et,
            now_ms=now_ms,
            state=st,
            db_path=db_path,
            counters=counters,
            emit_blocks=emit_blocks,
        )
    except Exception:
        logger.exception("options_flow poll: unexpected cycle failure")
    return counters


def _run_poll_cycle(
    adapter: Any,
    *,
    symbols: list[str],
    today_et: str,
    now_ms: int,
    state: PollState,
    db_path: Path | None,
    counters: dict[str, Any],
    emit_blocks: bool = True,
) -> None:
    if state.day != today_et:
        state.reset()
        state.day = today_et

    if not state.condition_sets_loaded:
        try:
            mapping = adapter.get_option_trade_conditions()
            if isinstance(mapping, dict) and mapping:
                state.condition_descriptions = {
                    str(k): str(v) for k, v in mapping.items()
                }
        except Exception:
            logger.exception("options_flow poll: condition mapping fetch failed")
        state.condition_sets_loaded = True

    if state.oi_day != today_et:
        _refresh_oi(adapter, symbols, today_et, state)

    if (
        state.last_active_refresh_ms <= 0
        or now_ms - state.last_active_refresh_ms
        >= ACTIVE_SET_REFRESH_SECONDS * _MS_PER_SEC
    ):
        uv1 = _refresh_active_set(
            adapter, symbols, today_et, now_ms, state, db_path, counters
        )
        counters["uv1"] += uv1
        state.last_active_refresh_ms = now_ms

    if not state.active:
        return

    cycle_prints_by_u: dict[str, list[dict]] = {}
    new_prints_by_occ: dict[str, list[dict]] = {}

    active_occs = list(state.active.keys())
    # Cursor-less contracts (cold start after a restart, or newly active mid-day)
    # start at most POLL_COLD_START_LOOKBACK_MINUTES back, never session open —
    # min(starts) below drags the whole chunk to the oldest cursor. Pressure
    # accumulation restarts bounded-lossy; the day_prints cap already made a
    # full-day refetch mostly moot for the busiest names.
    default_start = _session_start_iso(today_et)
    lookback_ms = now_ms - POLL_COLD_START_LOOKBACK_MINUTES * 60 * _MS_PER_SEC
    session_start_ms = _parse_ts_ms(default_start)
    if session_start_ms is not None and lookback_ms > session_start_ms:
        default_start = _ts_ms_to_iso(lookback_ms)

    for i in range(0, len(active_occs), _TRADES_CHUNK):
        chunk = active_occs[i : i + _TRADES_CHUNK]
        try:
            starts = [
                state.cursors.get(occ) or default_start for occ in chunk
            ]
            chunk_start = min(starts)
            raw_by_occ = adapter.get_option_trades(chunk, start=chunk_start)
            if not isinstance(raw_by_occ, dict):
                continue
        except Exception:
            logger.exception(
                "options_flow poll: trades fetch failed for chunk starting %s",
                chunk[0] if chunk else "?",
            )
            continue

        for occ in chunk:
            meta = state.active.get(occ)
            if not meta:
                continue
            cursor = state.cursors.get(occ)
            cursor_ms = _parse_ts_ms(cursor) if cursor else None
            raw_trades = raw_by_occ.get(occ) or raw_by_occ.get(occ.upper()) or []
            if not isinstance(raw_trades, list):
                continue

            max_ts: str | None = cursor
            max_ms = cursor_ms if cursor_ms is not None else -1
            for raw in raw_trades:
                if not isinstance(raw, dict):
                    continue
                ts_raw = raw.get("t")
                if not ts_raw:
                    continue
                ts_str = str(ts_raw)
                ts_ms = _parse_ts_ms(ts_str)
                if ts_ms is None:
                    continue
                if cursor_ms is not None and ts_ms <= cursor_ms:
                    continue

                p = normalize_print(raw, meta=meta)
                if p is None:
                    continue

                counters["prints"] += 1
                _log_unknown_conditions(p.get("conditions") or [], state)

                u = str(meta.get("underlying") or "").strip().upper()
                cycle_prints_by_u.setdefault(u, []).append(p)
                new_prints_by_occ.setdefault(occ, []).append(p)

                if ts_ms > max_ms:
                    max_ms = ts_ms
                    max_ts = ts_str

            if max_ts and max_ts != cursor:
                state.cursors[occ] = max_ts

    for u, prints in cycle_prints_by_u.items():
        try:
            tag_likely_spreads(prints)
        except Exception:
            logger.exception("options_flow poll: spread tagging failed for %s", u)

    occs_with_new = list(new_prints_by_occ.keys())
    quotes_by_occ: dict[str, dict] = {}
    for i in range(0, len(occs_with_new), POLL_QUOTE_CHUNK):
        chunk = occs_with_new[i : i + POLL_QUOTE_CHUNK]
        try:
            got = adapter.get_option_latest_quotes(chunk)
            if isinstance(got, dict):
                quotes_by_occ.update(got)
        except Exception:
            logger.exception(
                "options_flow poll: quote fetch failed for chunk starting %s",
                chunk[0] if chunk else "?",
            )

    classified_by_u: dict[str, list[dict]] = {}
    for occ, prints in new_prints_by_occ.items():
        q = quotes_by_occ.get(occ) or {}
        bid = q.get("bid")
        ask = q.get("ask")
        for p in prints:
            direction = classify_direction(
                price=float(p["price"]),
                bid=bid if bid is not None else None,
                ask=ask if ask is not None else None,
            )
            p["direction"] = direction
            p["direction_basis"] = "polled_quote"
            u = str(p.get("underlying") or "").strip().upper()
            classified_by_u.setdefault(u, []).append(p)

            if p.get("spread_leg") or p.get("likely_spread"):
                continue
            if is_hygiene_excluded(
                p.get("conditions") or [],
                cancels=state.cancel_conditions,
                late=state.late_oos_conditions,
                ext=state.ext_hours_conditions,
            ):
                continue

            if not emit_blocks:
                continue
            try:
                ev = detect_block(
                    p,
                    event_date=today_et,
                    direction=direction,
                    direction_basis="polled_quote",
                )
                if ev is not None:
                    of_db.insert_event(ev, db_path=db_path)
                    counters["blocks"] += 1
                    counters["events"].append(ev)
            except Exception:
                logger.exception(
                    "options_flow poll: block detect/insert failed for %s", occ
                )

    for u, prints in classified_by_u.items():
        _append_day_prints(state, u, prints)

    for u in classified_by_u:
        try:
            ev = aggregate_directional_pressure(
                underlying=u,
                prints=state.day_prints.get(u, []),
                event_date=today_et,
                direction_basis="polled_quote",
            )
            if ev is not None:
                ev["ts"] = _ts_ms_to_iso(now_ms)
                of_db.insert_event(ev, db_path=db_path)
                counters["pressure"] += 1
                counters["events"].append(ev)
        except Exception:
            logger.exception(
                "options_flow poll: pressure detect/insert failed for %s", u
            )


def _log_unknown_conditions(codes: list[str], state: PollState) -> None:
    if not state.condition_descriptions:
        return
    known = set(state.condition_descriptions)
    for code in codes:
        c = str(code)
        if c and c not in known:
            logger.debug("options_flow poll: unknown trade condition code %r", c)


def _allocate_active(picks_by_u: dict[str, list[str]], budget: int) -> list[tuple[str, str]]:
    """Round-robin by rank across underlyings, up to the stream's sub budget.

    Taking every underlying's #1 before any underlying's #2 means a busy name can't
    crowd out a quiet one, and nothing is dropped for how its ticker is spelled —
    _desired_trades() clips sorted(state.active) at STREAM_TRADES_MAX, i.e. purely
    alphabetically, so anything we hand it beyond the budget dies by spelling.
    """
    out: list[tuple[str, str]] = []
    if budget <= 0:
        return out
    depth = max((len(occs) for occs in picks_by_u.values()), default=0)
    for i in range(depth):
        for u, occs in picks_by_u.items():
            if i < len(occs):
                out.append((u, occs[i]))
                if len(out) >= budget:
                    return out
    return out


def _refresh_oi(
    adapter: Any,
    symbols: list[str],
    today_et: str,
    state: PollState,
) -> None:
    capped = eligible_underlyings(symbols)
    # Gauge names get OI seeds too — the seed IS the pre-open subscription (the
    # bell's first minutes ride it until today's trades exist to rank), and the
    # open window's first minutes are exactly what the gauge exists to measure.
    # The seed path intentionally has no 0-1 DTE skip: big-OI same-day expiries
    # seed the gauge's coverage at the bell (audit 2026-07-16 F1).
    fetch_order = list(capped) + [u for u in GAUGE_FETCH_UNDERLYINGS if u not in capped]

    for u in fetch_order:
        try:
            contracts = adapter.get_option_contracts(
                u, expiration_date_gte=today_et, limit=1000
            )
            if not isinstance(contracts, list):
                continue
            seeds: list[tuple[int, str]] = []
            for c in contracts:
                if not isinstance(c, dict):
                    continue
                occ = str(c.get("symbol") or "").strip().upper()
                if not occ:
                    continue
                oi = _int_oi(c.get("open_interest"))
                if oi is not None:
                    state.oi[occ] = oi
                    seeds.append((oi, occ))
                meta = _meta_from_contract({**c, "underlying_symbol": u}, today_et)
                if meta is not None and occ in state.active:
                    state.active[occ] = meta
            seeds.sort(reverse=True)
            state.oi_seed[u] = [occ for _, occ in seeds[:ACTIVE_SET_PER_UNDERLYING]]
        except Exception:
            logger.exception("options_flow poll: OI refresh failed for %s", u)
            continue

    state.oi_day = today_et


def _refresh_active_set(
    adapter: Any,
    symbols: list[str],
    today_et: str,
    now_ms: int,
    state: PollState,
    db_path: Path | None,
    counters: dict[str, Any] | None = None,
) -> int:
    """Rebuild active set; run intraday UV-1 piggyback. Returns uv1 events inserted."""
    capped = eligible_underlyings(symbols)
    # Gauge lane (audit 2026-07-16 F1): the index-pressure universe is GUARANTEED
    # coverage — fetched regardless of watchlists, outside the watchlist cap (so a
    # full watchlist can never evict SPY), allocated BEFORE the regular budget (so
    # over-budget clipping can never drop it). SPX rides fail-soft: if the vendor
    # won't serve its chain, the per-underlying try/except below skips it and the
    # gauge simply accrues no SPX/SPXW rows.
    fetch_order = list(capped) + [u for u in GAUGE_FETCH_UNDERLYINGS if u not in capped]

    new_active: dict[str, dict] = {}
    picks_by_u: dict[str, list[str]] = {}
    gauge_picks_by_u: dict[str, list[str]] = {}
    uv1_count = 0

    score_by_occ: dict[str, float] = {}
    for u in fetch_order:
        try:
            # Must be paginated+limited: the vendor's bare default is 100 contracts in
            # OCC-alphabetical order = one expiry of calls, so the ranking below never
            # saw a put for the liquid names. See CHAIN_SNAPSHOT_* in constants.
            snaps = adapter.get_option_chain_snapshot(
                u,
                limit=CHAIN_SNAPSHOT_PAGE_LIMIT,
                max_pages=CHAIN_SNAPSHOT_MAX_PAGES,
            )
            if not isinstance(snaps, dict):
                continue
            # SPXW pagination trap (measured live 2026-07-16): OCC symbols sort
            # SPX-root (monthlies) strictly before SPXW-root (dailies), so the paged
            # whole-chain fetch exhausts its budget on monthlies and the gauge's
            # 0-1 DTE dailies never appear. For gauge index names, ALSO fetch the
            # next two expiries explicitly — small, complete by construction.
            if u in GAUGE_UNDERLYINGS and u in INDEX_MEGACAP_SET:
                for exp_date in _near_expiries(today_et):
                    try:
                        exp_snaps = adapter.get_option_chain_snapshot(
                            u,
                            expiration_date=exp_date,
                            limit=CHAIN_SNAPSHOT_PAGE_LIMIT,
                            max_pages=CHAIN_SNAPSHOT_MAX_PAGES,
                        )
                        if isinstance(exp_snaps, dict):
                            for occ, s in exp_snaps.items():
                                snaps.setdefault(occ, s)
                    except Exception:
                        logger.debug(
                            "options_flow poll: per-expiry fetch failed for %s %s",
                            u,
                            exp_date,
                            exc_info=True,
                        )

            candidates: list[tuple[float, int, str]] = []
            gauge_zero_dte: list[tuple[float, int, str]] = []
            for occ, snap in snaps.items():
                if not isinstance(snap, dict):
                    continue
                trade = snap.get("latestTrade") or snap.get("latest_trade") or {}
                if not isinstance(trade, dict) or not _trade_is_today(trade, today_et):
                    continue
                sym = str(occ).strip().upper()
                parsed = parse_occ_symbol(sym)
                if parsed is None:
                    continue
                dte = _dte_days(parsed.expiration.isoformat(), today_et)
                if _skip_index_zero_dte(u, dte):
                    # Gauge lane: the skip stays for the REGULAR set (the detectors
                    # discard these prints), but the index-pressure gauge WANTS the
                    # 0-1 DTE tape — collect the skipped contracts for its own picks.
                    if u in GAUGE_UNDERLYINGS:
                        gauge_zero_dte.append(
                            (
                                _day_dollar_volume(snap, trade),
                                dte if dte is not None else 0,
                                sym,
                            )
                        )
                    continue
                candidates.append(
                    (_day_dollar_volume(snap, trade), dte if dte is not None else 0, sym)
                )

            if gauge_zero_dte:
                gauge_zero_dte.sort(key=lambda c: (c[0], c[2]), reverse=True)
                gauge_top = gauge_zero_dte[:GAUGE_ZERO_DTE_PICKS]
                for score, _dte, sym in gauge_top:
                    score_by_occ[sym] = score
                gauge_picks_by_u[u] = [sym for _, _, sym in gauge_top]

            picked = _select_active_picks(candidates, ACTIVE_SET_PER_UNDERLYING)
            for score, _dte, sym in picked:
                score_by_occ[sym] = score
            picks = [sym for _, _, sym in picked]
            if not picks:
                # Nothing has traded today yet — at the bell this ranking is empty by
                # construction, which used to leave the stream unsubscribed through the
                # open. Seed from prior-session OI so the burst is covered.
                picks = state.oi_seed.get(u, [])
            picks_by_u[u] = picks
        except Exception:
            logger.exception("options_flow poll: active-set refresh failed for %s", u)
            continue

    # Gauge lane allocates FIRST from its own budget — the regular set's clipping
    # can never evict it, and it can never eat more than GAUGE_LANE_BUDGET slots.
    for u, occ in _allocate_active(
        gauge_picks_by_u, min(GAUGE_LANE_BUDGET, STREAM_TRADES_MAX)
    ):
        meta = _meta_from_occ(occ, u, today_et)
        if meta is not None:
            meta["rank_score"] = score_by_occ.get(occ) or float(state.oi.get(occ) or 0)
            meta["gauge_lane"] = True
            new_active[occ] = meta

    wanted = sum(len(occs) for occs in picks_by_u.values())
    regular_budget = STREAM_TRADES_MAX - len(new_active)
    if wanted > regular_budget:
        logger.info(
            "options_flow poll: %d ranked contracts over the %d sub budget — "
            "allocating round-robin across %d underlyings (~%d each)",
            wanted,
            regular_budget,
            len(picks_by_u),
            regular_budget // max(len(picks_by_u), 1),
        )
    for u, occ in _allocate_active(picks_by_u, regular_budget):
        if occ in new_active:
            continue
        meta = _meta_from_occ(occ, u, today_et)
        if meta is not None:
            # Anchor ranking: traded score, else prior-session OI (the seed path —
            # pre-open nothing has traded, but OI still orders the anchors).
            meta["rank_score"] = score_by_occ.get(occ) or float(state.oi.get(occ) or 0)
            new_active[occ] = meta

    state.active = new_active

    if not new_active:
        return 0

    bars_by_occ: dict[str, list[dict]] = {}
    occ_list = list(new_active.keys())
    for i in range(0, len(occ_list), _TRADES_CHUNK):
        chunk = occ_list[i : i + _TRADES_CHUNK]
        try:
            got = adapter.get_option_bars(
                chunk, start=today_et, timeframe="1Day"
            )
            if isinstance(got, dict):
                for occ, items in got.items():
                    bars_by_occ[str(occ).strip().upper()] = items
        except Exception:
            logger.exception(
                "options_flow poll: UV-1 bars fetch failed for chunk starting %s",
                chunk[0] if chunk else "?",
            )

    for occ, meta in new_active.items():
        try:
            vc = _bar_volume_close(bars_by_occ.get(occ))
            if vc is None:
                continue
            volume, close = vc
            u = str(meta.get("underlying") or "").strip().upper()
            ev = detect_uv1(
                underlying=u,
                occ_symbol=occ,
                right=meta["right"],
                strike=meta["strike"],
                expiry=meta["expiry"],
                dte=meta["dte"],
                otm_pct=None,
                volume=volume,
                oi=state.oi.get(occ),
                close=close,
                event_date=today_et,
            )
            if ev is not None:
                ev["ts"] = _ts_ms_to_iso(now_ms)
                of_db.insert_event(ev, db_path=db_path)
                uv1_count += 1
                if counters is not None:
                    counters["events"].append(ev)
        except Exception:
            logger.exception("options_flow poll: UV-1 detect/insert failed for %s", occ)

    return uv1_count
