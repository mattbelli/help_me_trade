"""Schwab OAuth token management via schwab-py."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from authlib.integrations.httpx_client import OAuth2Client
from dotenv import load_dotenv
from schwab import auth
from schwab.auth import TOKEN_ENDPOINT
from schwab.client import Client

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
    print("Schwab manual login (headless-friendly — no local browser needed).")
    # Manual flow always runs the full paste-based login (it never short-circuits
    # on an existing token, unlike easy_client). Remove any prior token first so a
    # weekly re-auth starts clean rather than refreshing a possibly-stale token.
    if path.exists():
        print(f"Removing existing token to force re-auth: {path}")
        path.unlink()
    print(f"Callback URL must match your Schwab app: {callback_url}")
    print(f"Token will be saved to: {path}")
    print(
        "\nA URL will be printed below. Open it on any device, log in, and when "
        "the browser is redirected to your callback URL (it will fail to load — "
        "that's expected), copy the FULL address-bar URL and paste it back here.\n"
    )
    # NB: client_from_manual_flow has no `interactive` kwarg (unlike easy_client);
    # the manual paste flow is inherently interactive. `interactive` stays on the
    # signature for caller compatibility (scripts/schwab_login.py --no-prompt).
    auth.client_from_manual_flow(
        api_key,
        app_secret,
        callback_url,
        str(path),
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

    return _client_from_token_basic(api_key, app_secret, path)


def _client_from_token_basic(api_key: str, app_secret: str, path: Path):
    """Build a Schwab client whose token refresh uses HTTP Basic auth.

    schwab-py builds its OAuth2 session without setting
    ``token_endpoint_auth_method``, so authlib defaults to
    ``client_secret_post`` (credentials in the body) — which Schwab's token
    endpoint rejects with ``invalid_client``. authlib binds the auth method at
    construction, so we build the session ourselves with
    ``client_secret_basic`` and persist refreshed tokens in schwab-py's
    ``{creation_timestamp, token}`` file format.
    """
    raw = json.loads(path.read_text())
    token = raw.get("token", raw) if isinstance(raw, dict) else raw
    creation_timestamp = (
        int(raw.get("creation_timestamp", time.time()))
        if isinstance(raw, dict)
        else int(time.time())
    )

    def _write(new_token, *args, **kwargs):
        # Preserve the refresh-token mint clock. Prefer the CURRENT on-disk value:
        # if a cron re-mint replaced the file while this client was alive, keeping
        # the fresh clock matters more than this instance's stale snapshot.
        ts = creation_timestamp
        try:
            on_disk = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(on_disk, dict) and on_disk.get("creation_timestamp") is not None:
                ts = int(on_disk["creation_timestamp"])
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        payload = json.dumps({"creation_timestamp": ts, "token": new_token})
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, path)

    session = OAuth2Client(
        api_key,
        client_secret=app_secret,
        token=token,
        token_endpoint=TOKEN_ENDPOINT,
        token_endpoint_auth_method="client_secret_basic",
        update_token=_write,
        leeway=300,
    )
    # Populate token_metadata (schwab-py leaves it None when we build the session
    # ourselves) so StreamClient.login() can read token_metadata.token['access_token'].
    token_metadata = auth.TokenMetadata(token, creation_timestamp, _write)
    return Client(api_key, session, token_metadata=token_metadata)


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
