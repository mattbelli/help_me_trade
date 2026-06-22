"""Schwab OAuth token management via schwab-py."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from schwab import auth

from brokers.errors import BrokerError

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OPEN_ORDER_STATUSES = {
    "ACCEPTED",
    "AWAITING_CONDITION",
    "AWAITING_MANUAL_REVIEW",
    "AWAITING_PARENT_CONDITION",
    "AWAITING_UR_OUT",
    "PENDING_ACTIVATION",
    "PENDING_CANCEL",
    "PENDING_REPLACE",
    "QUEUED",
    "WORKING",
}


def token_path() -> Path:
    raw = os.environ.get("SCHWAB_TOKEN_PATH", "data/schwab_token.json").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path


def _credentials(overrides: dict[str, str] | None = None) -> tuple[str, str, str]:
    from config.settings import schwab_config

    cfg = {**schwab_config(), **(overrides or {})}
    api_key = cfg.get("api_key", "").strip()
    app_secret = cfg.get("app_secret", "").strip()
    callback_url = cfg.get("callback_url", "https://127.0.0.1:8182").strip()
    missing = [
        name
        for name, val in [
            ("SCHWAB_API_KEY", api_key),
            ("SCHWAB_APP_SECRET", app_secret),
            ("SCHWAB_CALLBACK_URL", callback_url),
        ]
        if not val
    ]
    if missing:
        raise BrokerError(
            f"Missing Schwab env vars: {', '.join(missing)}. See .env.example."
        )
    return api_key, app_secret, callback_url


def token_exists() -> bool:
    return token_path().exists()


def auth_status() -> dict:
    path = token_path()
    if not path.exists():
        return {
            "authenticated": False,
            "token_path": str(path),
            "message": "No token file. Run: python -m scripts.schwab_login",
        }

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "authenticated": False,
            "token_path": str(path),
            "message": f"Token file unreadable: {exc}",
        }

    created_at = payload.get("creation_timestamp")
    expires_hint = None
    if created_at is not None:
        # Schwab refresh tokens expire after 7 days; schwab-py stores creation time.
        created = datetime.fromtimestamp(float(created_at), tz=UTC)
        age_days = (datetime.now(UTC) - created).total_seconds() / 86400
        expires_hint = max(0.0, 7.0 - age_days)

    return {
        "authenticated": True,
        "token_path": str(path),
        "refresh_token_age_days": round(
            (datetime.now(UTC).timestamp() - float(created_at)) / 86400, 2
        )
        if created_at is not None
        else None,
        "estimated_days_until_reauth": round(expires_hint, 2)
        if expires_hint is not None
        else None,
        "message": "Token on disk. Re-auth weekly via scripts.schwab_login.",
    }


def run_interactive_login(interactive: bool = True) -> None:
    api_key, app_secret, callback_url = _credentials()
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # easy_client() short-circuits and returns the EXISTING token whenever the
    # file is present and younger than its max_token_age — so a weekly re-auth
    # would silently no-op (no browser, ignoring `interactive`). Force a fresh
    # login flow by removing any prior token first.
    if path.exists():
        print(f"Removing existing token to force re-auth: {path}")
        path.unlink()
    print("Opening browser for Schwab login...")
    print(f"Callback URL must match your Schwab app: {callback_url}")
    print(f"Token will be saved to: {path}")
    auth.easy_client(
        api_key,
        app_secret,
        callback_url,
        str(path),
        interactive=interactive,
    )
    print("Login complete. Set BROKER=schwab in .env and restart the API.")


def create_client(*, interactive: bool = False):
    api_key, app_secret, callback_url = _credentials()
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if interactive:
        return auth.easy_client(
            api_key,
            app_secret,
            callback_url,
            str(path),
            interactive=True,
        )

    if not path.exists():
        raise BrokerError(
            "Schwab token missing. Run: python -m scripts.schwab_login"
        )

    return auth.client_from_token_file(str(path), api_key, app_secret)


def resolve_account_hash(client, *, account_hash: str | None = None) -> str:
    explicit = (account_hash or "").strip()
    if not explicit:
        from config.settings import schwab_config

        explicit = schwab_config().get("account_hash", "").strip()
    if explicit:
        return explicit

    response = client.get_account_numbers()
    response.raise_for_status()
    accounts = response.json()
    if not accounts:
        raise BrokerError("No Schwab accounts linked to this token.")

    if len(accounts) > 1:
        hashes = [a.get("hashValue") for a in accounts]
        raise BrokerError(
            "Multiple Schwab accounts found. Set SCHWAB_ACCOUNT_HASH in .env "
            f"to one of: {', '.join(h for h in hashes if h)}"
        )

    return str(accounts[0]["hashValue"])
