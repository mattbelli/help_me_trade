"""Nightly cycle for the LOCAL-model paper book — the panel account (`acct_alpaca_open`,
strategy `book_panel`) run as a SOLO analyst, exactly like Deep Seek: the same in-process
driver (research/book_driver.run_book_cycle), the same charter + decision contract, the
same read-only analyst toolset (research/book_tools.py), the same registration + intake
path. The only difference is the LLM: LM Studio on the founder's PC (qwen3.8-27b) via the
`lmstudio` provider (config/ai_providers.py).

Scheduled by cron at 21:40 UTC Mon-Fri, right after Fable (tools/book_session.sh) — see
`crontab -l`. Preflights that the model is actually LOADED on the PC and fails loudly if
not, so the books dead-man (api/ops_jobs._book_deadman_once) flags the missing report
instead of the cycle burning its timeouts.

Usage:
    uv run python tools/book_local_run.py           # gate: weekday, post-17:05 ET, once/day
    uv run python tools/book_local_run.py --check   # preflight only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.llm.config import provider_credentials  # noqa: E402
from ai.llm.factory import get_llm_for_provider  # noqa: E402
from research.book_driver import run_book_cycle  # noqa: E402

BOOK_ID = "panel"
PROVIDER = "lmstudio"


def preflight() -> str:
    """Return the loaded model id, or exit nonzero with the reason."""
    creds = provider_credentials(PROVIDER)
    base, model = creds["base_url"].rstrip("/"), creds["model"]
    # LM Studio's native models endpoint carries load state; /v1/models does not.
    url = base[: -len("/v1")] + "/api/v0/models" if base.endswith("/v1") else base + "/api/v0/models"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            models = json.load(r)["data"]
    except Exception as exc:  # noqa: BLE001 — PC off / tailnet down / LM Studio not running
        sys.exit(f"preflight: LM Studio unreachable at {url}: {exc}")
    state = {m["id"]: m.get("state") for m in models}
    # LM Studio ids carry the quant as a suffix ("qwen3.8-27b@q4_k_xl") and the founder
    # swaps quants; the configured id is the family, the LOADED variant is what we call.
    loaded = [mid for mid, st in state.items() if st == "loaded" and mid.split("@")[0] == model]
    if not loaded:
        sys.exit(f"preflight: no loaded {model}* in LM Studio (states: {state})")
    return loaded[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="preflight only")
    args = parser.parse_args()
    model = preflight()
    print(f"preflight ok: {PROVIDER}/{model} loaded")
    if args.check:
        return 0
    llm = get_llm_for_provider(PROVIDER, model=model)
    result = asyncio.run(run_book_cycle(BOOK_ID, llm=llm))
    print(json.dumps(result, default=str))
    return 0 if result.get("status") in ("ok", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
