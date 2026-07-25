#!/usr/bin/env python3
"""Register the sampler's WIDE breadth tier — one-time (re-runnable) universe resolver.

Study B (`References/IV_LEVEL_PREREG_2026-07-25.md`) measured the cost of not having
breadth: the IV-level effect is real and robust across 2,112 names, and absent on our
own 37. The blocker was never the phenomenon, it was the size of the cross-section. This
registers a wide tier so the sampler starts banking IV readings on hundreds of names.

Source is the DoltHub corpus's symbol list, chosen deliberately: those names already
carry up to 7 years of history in `data/options_iv_history.db`, so a level/rank baseline
exists on day one instead of in 2027.

A symbol is registered only if Alpaca actually returns option contracts for it — the
universe table is APPEND-ONLY, so a delisted or non-optionable ticker admitted here would
sit in the sweep forever returning nothing.

    python tools/resolve_wide_universe.py --dry-run     # probe + report, register nothing
    python tools/resolve_wide_universe.py --limit 400   # probe the 400 deepest-history names
    python tools/resolve_wide_universe.py               # the full corpus list
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brokers.alpaca import AlpacaAdapter  # noqa: E402
from db.options_chain_history import (  # noqa: E402
    init_chain_tables,
    register_universe,
    universe_tiers,
)
from signals.options_chain_snapshot import is_optionable_symbol  # noqa: E402

logger = logging.getLogger("resolve_wide_universe")
ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "options_iv_history.db"
PROBE_PAUSE = 0.02  # gentle on the vendor across a few thousand probes


def corpus_symbols(limit: int | None) -> list[str]:
    """Corpus symbols, deepest history first — history depth is the whole point."""
    conn = sqlite3.connect(f"file:{CORPUS}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT act_symbol, COUNT(*) n FROM volatility_history"
        " GROUP BY act_symbol ORDER BY n DESC"
    ).fetchall()
    conn.close()
    syms = [s for s, _ in rows if is_optionable_symbol(s)]
    return syms[:limit] if limit else syms


def has_options(adapter: AlpacaAdapter, sym: str, window: tuple[str, str]) -> bool:
    """Cheapest possible optionability probe: the wide tier's own fetch, one page.

    Reuses the real fetch rather than a separate assets lookup, so 'registerable'
    means exactly 'the sweep will get something back'.
    """
    try:
        snaps = adapter.get_option_chain_snapshot(
            sym, limit=100, max_pages=1,
            expiration_date_gte=window[0], expiration_date_lte=window[1],
        )
    except Exception:
        return False
    return bool(snaps)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, help="probe only the N deepest-history symbols")
    ap.add_argument("--dry-run", action="store_true", help="probe and report, register nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not CORPUS.exists():
        logger.error("Corpus not found at %s", CORPUS)
        return 2

    today = date.today()
    window = ((today + timedelta(days=7)).isoformat(), (today + timedelta(days=75)).isoformat())
    candidates = corpus_symbols(args.limit)

    if not args.dry_run:
        init_chain_tables()
    existing = {} if args.dry_run else universe_tiers()
    already_deep = [s for s in candidates if existing.get(s) == "deep"]
    todo = [s for s in candidates if s not in existing]
    logger.info(
        "%d corpus symbols; %d already in the universe (%d of them deep, left alone), %d to probe",
        len(candidates), len(candidates) - len(todo), len(already_deep), len(todo),
    )

    adapter = AlpacaAdapter.from_settings()
    # The probe deliberately reads ONE page and only asks "did anything come back",
    # so the adapter's page-cap warning is expected here and would otherwise fire
    # thousands of times. Silenced for the probe loop only — the sweep itself keeps it.
    logging.getLogger("brokers.alpaca").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    optionable: list[str] = []
    t0 = time.time()
    for i, sym in enumerate(todo, 1):
        if has_options(adapter, sym, window):
            optionable.append(sym)
        if i % 200 == 0:
            logger.info("  probed %d/%d — %d optionable (%.0fs)", i, len(todo), len(optionable), time.time() - t0)
        time.sleep(PROBE_PAUSE)

    elapsed = time.time() - t0
    logger.info(
        "Probed %d in %.0fs: %d optionable, %d without a chain (dropped, never registered)",
        len(todo), elapsed, len(optionable), len(todo) - len(optionable),
    )
    if args.dry_run:
        logger.info("DRY RUN — registering nothing. Sample: %s", ", ".join(optionable[:25]))
        return 0

    added = register_universe(optionable, source="corpus-breadth", tier="wide")
    tiers = universe_tiers()
    deep = sum(1 for t in tiers.values() if t == "deep")
    wide = sum(1 for t in tiers.values() if t == "wide")
    logger.info("Registered %d new wide symbols. Universe now %d deep + %d wide = %d",
                added, deep, wide, deep + wide)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
