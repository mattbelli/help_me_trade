"""
Bullet 0: Verify Alpaca paper API connectivity.

Reads credentials from .env and calls GET /v2/account.
Run: python scripts/verify_alpaca.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Load .env from project root (parent of scripts/)
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

REQUIRED_VARS = ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "APCA_API_BASE_URL")
PLACEHOLDER_MARKERS = (
    "your_paper",
    "replace_with",
    "your_secret",
    "here",
    "changeme",
)


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def _looks_like_placeholder(value: str) -> bool:
    lower = value.strip().lower()
    return any(marker in lower for marker in PLACEHOLDER_MARKERS)


def main() -> int:
    missing = [name for name in REQUIRED_VARS if not os.getenv(name)]
    if missing:
        print("Missing environment variables:", ", ".join(missing))
        print()
        print("1. Copy .env.example to .env")
        print("2. Fill in your Alpaca paper API Key ID and Secret Key")
        print("     (Alpaca dashboard -> Paper Trading -> API Keys)")
        return 1

    key_id = os.environ["APCA_API_KEY_ID"].strip()
    secret = os.environ["APCA_API_SECRET_KEY"].strip()

    if _looks_like_placeholder(secret):
        print("Your .env still has a placeholder secret - not a real Alpaca key.")
        print()
        print("  APCA_API_KEY_ID looks like:     ", _mask(key_id))
        print("  APCA_API_SECRET_KEY looks like: ", _mask(secret), "(placeholder)")
        print()
        print("Fix:")
        print("  1. Open https://app.alpaca.markets/paper/dashboard/overview")
        print("  2. Go to API Keys -> Generate new key (or use saved secret)")
        print("  3. Edit .env and set APCA_API_SECRET_KEY to the secret")
        print("     (Alpaca only shows the secret ONCE when you create the key)")
        return 1

    base_url = os.environ["APCA_API_BASE_URL"].strip().rstrip("/")
    # Users sometimes paste the /v2 docs URL; normalize to host only.
    if base_url.endswith("/v2"):
        base_url = base_url[: -len("/v2")]
        print("Note: APCA_API_BASE_URL should not include /v2 — fixed automatically.")
        print(f"      Using: {base_url}")
        print()
    if "paper-api" not in base_url and "api.alpaca.markets" in base_url:
        print("WARNING: APCA_API_BASE_URL looks like LIVE trading, not paper.")
        print(f"         Current value: {base_url}")
        print("         Paper URL should be: https://paper-api.alpaca.markets")
        return 1

    headers = {
        "APCA-API-KEY-ID": key_id,
        "APCA-API-SECRET-KEY": secret,
    }

    url = f"{base_url}/v2/account"
    print("Connecting to Alpaca paper API...")
    print(f"  GET {url}")
    print()

    try:
        response = httpx.get(url, headers=headers, timeout=30.0)
    except httpx.RequestError as exc:
        print(f"Network error: {exc}")
        return 1

    if response.status_code == 401:
        print("Authentication failed (401).")
        print()
        print(f"  Key ID loaded:     {_mask(key_id)}")
        print(f"  Secret loaded:     {_mask(secret)} (length {len(secret)})")
        print(f"  Base URL:          {base_url}")
        print()
        print("Common fixes:")
        print("  - Secret must match the Key ID (same key pair from Alpaca)")
        print("  - Use PAPER keys with paper-api.alpaca.markets (not live keys)")
        print("  - No quotes around values in .env -- use: APCA_API_SECRET_KEY=abc123")
        print("  - If you lost the secret, generate a NEW key pair in Alpaca")
        return 1

    if response.status_code != 200:
        print(f"Unexpected status {response.status_code}: {response.text}")
        return 1

    account = response.json()
    print("Success! Paper account connected.")
    print()
    print(f"  Account ID:    {account.get('id')}")
    print(f"  Status:        {account.get('status')}")
    print(f"  Buying power:  ${float(account.get('buying_power', 0)):,.2f}")
    print(f"  Cash:          ${float(account.get('cash', 0)):,.2f}")
    print(f"  Portfolio val: ${float(account.get('portfolio_value', 0)):,.2f}")
    print()
    print("Bullet 0 complete - API keys work. Ready for Bullet 1 (fetch positions).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
