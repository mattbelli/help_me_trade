"""The app's in-process options-flow runner instance + its universe callback.
Extracted verbatim from ``api/main`` (strangler round 3). The pipeline itself
lives in ``signals/options_flow/runner.py`` so the standalone collector
(``tools/flow_collector.py``) can host it; ownership flips via
OPTIONS_FLOW_EXTERNAL_COLLECTOR. ``api/main``'s lifespan starts/stops
``_flow_runner``; ``api/background_jobs`` registers ``intraday_once``.
"""

from __future__ import annotations

import logging
import os

from signals.options_flow.runner import OptionsFlowRunner
from signals.universe import flow_watch_symbols as _flow_watch_symbols

logger = logging.getLogger(__name__)


_OPTIONS_FLOW_STREAM_ENABLED = os.environ.get("OPTIONS_FLOW_STREAM_ENABLED", "0").strip() not in (
    "0",
    "false",
    "no",
)
# "1" = the standalone collector process (tools/flow_collector.py) owns the
# intraday flow pipeline and the API must NOT run it: Alpaca allows one OPRA
# websocket per account, so two hosts 406-fight each other into backoff. Both
# processes read the same .env, so this one switch flips ownership atomically.
_OPTIONS_FLOW_EXTERNAL_COLLECTOR = os.environ.get(
    "OPTIONS_FLOW_EXTERNAL_COLLECTOR", "0"
).strip() not in ("0", "false", "no")


OPTIONS_FLOW_POLL_SECONDS = max(10, int(os.environ.get("OPTIONS_FLOW_POLL_SECONDS", "30")))


# ---------------------------------------------------------------------------
# The nightly EOD options-flow scan lives in api/research_jobs.py (R1a) so the
# research worker can own it after the R1b split.
# The intraday flow pipeline (poll + OPRA stream + sweep consumer) lives in
# signals/options_flow/runner.py so the standalone collector process
# (tools/flow_collector.py) can host it instead of this event loop --
# ownership flips via OPTIONS_FLOW_EXTERNAL_COLLECTOR in the shared .env.
def _flow_universe() -> list[str]:
    """Flow watchlist (dedicated "flow" list, pinned-first; union fallback)
    + Round-4 hot-lane promotions (lane names ride last so the underlying cap
    clips them first — the founder's list is never evicted).
    Scoped to the FLOW runner only; other _all_watchlist_symbols consumers
    (earnings warm, ext quotes) stay watchlist-pure. Import inside the guard:
    even a broken attention module must never cost the flow lane its
    watchlist."""
    base = _flow_watch_symbols()
    try:
        from signals.fusion.attention import merge_hot_lane

        return merge_hot_lane(base)
    except Exception:
        logger.warning("flow universe: hot-lane merge unavailable", exc_info=True)
        return base


_flow_runner = OptionsFlowRunner(watchlist_symbols=_flow_universe)
