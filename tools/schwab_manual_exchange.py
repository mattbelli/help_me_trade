"""Mint a Schwab token when serv's IP is Akamai-blocked from the login page.

Schwab's interactive login host (sws-gateway.schwab.com) 403s from serv when
Akamai flags the IP — scripts/schwab_reauth.py then can't even render a login
form. The token endpoint (api.schwabapi.com) stays reachable, so we split the
flow: do the browser half on a machine Schwab will talk to, paste the redirect
URL back here for the exchange.

    python -m tools.schwab_manual_exchange --start
    # open the printed URL in your browser, log in, approve; the tab will fail
    # to load a 127.0.0.1 page — that's expected. Copy its full address.
    python -m tools.schwab_manual_exchange --finish '<pasted url>' --restart-backend

The --start state is single-use and expires with the authorization code
(a few minutes), so run --finish promptly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.schwab_reauth import (  # noqa: E402
    _build_authorize_url,
    _exchange_code_for_token,
    _restart_backend,
    _save_token,
    _verify_client,
)

STATE_FILE = ROOT / "data" / ".schwab_manual_state.json"


def _start() -> int:
    api_key = os.environ["SCHWAB_API_KEY"]
    callback_url = os.environ["SCHWAB_CALLBACK_URL"]
    url, state = _build_authorize_url(api_key, callback_url)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"state": state}), encoding="utf-8")
    STATE_FILE.chmod(0o600)
    print("\nOpen this URL in a browser that can reach Schwab:\n")
    print(url)
    print(
        "\nLog in, approve the account link, and click Done. The browser will"
        "\nthen try to load https://127.0.0.1:8182/?code=... and fail to"
        "\nconnect — that is expected and correct. Copy that whole address."
        "\n\nThen run:"
        "\n  python -m tools.schwab_manual_exchange --finish '<paste>' --restart-backend\n"
    )
    return 0


def _finish(received_url: str, restart: bool) -> int:
    if "code=" not in received_url:
        print(
            "[manual] That URL has no code= parameter. Copy the full 127.0.0.1"
            " address from the failed tab, not the Schwab page.",
            file=sys.stderr,
        )
        return 1
    if not STATE_FILE.exists():
        print("[manual] No saved state — run --start first.", file=sys.stderr)
        return 1

    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))["state"]
    token = _exchange_code_for_token(
        os.environ["SCHWAB_API_KEY"],
        os.environ["SCHWAB_APP_SECRET"],
        os.environ["SCHWAB_CALLBACK_URL"],
        received_url,
        state,
    )

    from brokers.schwab_auth import token_path as get_token_path

    _save_token(get_token_path(), token)
    STATE_FILE.unlink()

    info = _verify_client()
    print(
        f"[manual] SUCCESS — new token minted, {info['accounts_found']} account(s)"
        " visible. Refresh-token clock reset; next reauth due in ~7 days."
    )
    if restart:
        _restart_backend()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="print the authorize URL")
    parser.add_argument("--finish", metavar="URL", help="the pasted 127.0.0.1 redirect URL")
    parser.add_argument("--restart-backend", action="store_true")
    args = parser.parse_args(argv)

    if args.start:
        return _start()
    if args.finish:
        return _finish(args.finish, args.restart_backend)
    parser.error("pass --start or --finish")


if __name__ == "__main__":
    raise SystemExit(main())
