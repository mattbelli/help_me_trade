"""Phone push delivery for trade proposals — gated, fail-safe, transport-agnostic.

Supports two providers (PUSH_PROVIDER):
  - "expo": Expo Push Service (legacy/fallback, token_type 'expo').
  - "apns": direct token-based APNs over HTTP/2 (token_type 'apns') — the decided
    transport for the iOS app. Signs an ES256 JWT with the .p8 key (cryptography),
    no extra deps. Sandbox vs production is selectable (dev builds use sandbox).

NEVER raises — all failures are logged and swallowed; returns count sent.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from config.settings import push_config
from db.base import DEFAULT_DB_PATH
from db.device_tokens import list_active_device_tokens

logger = logging.getLogger(__name__)

_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_APNS_HOST_PROD = "api.push.apple.com"
_APNS_HOST_SANDBOX = "api.sandbox.push.apple.com"
_JWT_MAX_AGE_S = 50 * 60  # APNs caps provider-token age at 60m; refresh before then.

# Cached provider JWT: (key_id, team_id) -> (token, issued_at)
_jwt_cache: dict[tuple[str, str], tuple[str, int]] = {}


def _proposal_body(proposal: dict[str, Any]) -> str:
    summary = proposal.get("summary")
    if summary:
        return str(summary)
    inner = proposal.get("proposal") if isinstance(proposal.get("proposal"), dict) else {}
    side = str(inner.get("side") or proposal.get("side") or "buy").upper()
    qty = inner.get("qty") if inner.get("qty") is not None else proposal.get("qty")
    symbol = inner.get("symbol") or proposal.get("symbol") or "?"
    return f"{side} {qty} {symbol}"


# ---------------------------------------------------------------------------
# Expo (legacy / fallback)
# ---------------------------------------------------------------------------
def _build_expo_message(token: str, proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "to": token,
        "title": "Trade proposal",
        "body": _proposal_body(proposal),
        "data": {
            "type": "trade_proposal",
            "proposal_id": proposal["id"],
            "payload_hash": proposal.get("payload_hash"),
            "expires_at": proposal.get("expires_at"),
        },
        "categoryId": "TRADE_PROPOSAL",
        "sound": "default",
        "priority": "high",
    }


async def _send_expo(messages: list[dict[str, Any]], cfg: dict[str, Any]) -> int:
    if not messages:
        return 0
    headers: dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}
    access_token = str(cfg.get("expo_access_token") or "").strip()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(_EXPO_PUSH_URL, json=messages, headers=headers)
        response.raise_for_status()
    return len(messages)


# ---------------------------------------------------------------------------
# Direct APNs (token-based, .p8 / ES256 JWT, HTTP/2)
# ---------------------------------------------------------------------------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_apns_jwt(key_pem: bytes, key_id: str, team_id: str) -> str:
    """ES256-signed provider token: header.claims.signature (JOSE compact)."""
    header = _b64url(json.dumps({"alg": "ES256", "kid": key_id}, separators=(",", ":")).encode())
    claims = _b64url(json.dumps({"iss": team_id, "iat": int(time.time())}, separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode("ascii")
    key = load_pem_private_key(key_pem, password=None)
    der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")  # JOSE wants raw R||S, not DER
    return f"{header}.{claims}.{_b64url(raw)}"


def _apns_jwt(key_pem: bytes, key_id: str, team_id: str) -> str:
    cached = _jwt_cache.get((key_id, team_id))
    now = int(time.time())
    if cached and now - cached[1] < _JWT_MAX_AGE_S:
        return cached[0]
    token = _make_apns_jwt(key_pem, key_id, team_id)
    _jwt_cache[(key_id, team_id)] = (token, now)
    return token


def _build_apns_payload(proposal: dict[str, Any]) -> dict[str, Any]:
    # Custom keys sit alongside `aps`; expo-notifications surfaces them as data.
    return {
        "aps": {
            "alert": {"title": "Trade proposal", "body": _proposal_body(proposal)},
            "sound": "default",
            "category": "TRADE_PROPOSAL",
        },
        "type": "trade_proposal",
        "proposal_id": proposal["id"],
        "payload_hash": proposal.get("payload_hash"),
        "expires_at": proposal.get("expires_at"),
    }


async def _send_apns(proposal: dict[str, Any], cfg: dict[str, Any], db_path: Path) -> int:
    key_id = str(cfg.get("apns_key_id") or "").strip()
    team_id = str(cfg.get("apns_team_id") or "").strip()
    bundle = str(cfg.get("apns_bundle_id") or "").strip()
    key_path = str(cfg.get("apns_key_path") or "").strip()
    if not (key_id and team_id and bundle and key_path):
        logger.warning("apns config incomplete (need key_id/team_id/bundle_id/key_path) — skipping")
        return 0

    try:
        key_pem = Path(key_path).read_bytes()
    except OSError:
        logger.exception("apns key not readable at %s", key_path)
        return 0

    tokens = list_active_device_tokens(token_type="apns", db_path=db_path)
    if not tokens:
        return 0

    jwt = _apns_jwt(key_pem, key_id, team_id)
    host = _APNS_HOST_SANDBOX if cfg.get("apns_use_sandbox") else _APNS_HOST_PROD
    payload = _build_apns_payload(proposal)
    headers = {
        "authorization": f"bearer {jwt}",
        "apns-topic": bundle,
        "apns-push-type": "alert",
        "apns-priority": "10",
    }

    sent = 0
    async with httpx.AsyncClient(http2=True, timeout=15.0) as client:
        for row in tokens:
            token = str(row["token"])
            try:
                resp = await client.post(
                    f"https://{host}/3/device/{token}", headers=headers, json=payload
                )
            except httpx.HTTPError:
                logger.exception("apns request failed for %s…", token[:8])
                continue
            if resp.status_code == 200:
                sent += 1
            else:
                # e.g. BadDeviceToken / Unregistered — log, keep going.
                logger.warning("apns %s for %s…: %s", resp.status_code, token[:8], resp.text[:200])
    return sent


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def send_alert_push(
    title: str,
    body: str,
    *,
    data: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Send a simple alert (title + body) push to active devices.

    Used for background-monitor notifications (RVOL spikes, Schwab auth expiry)
    and chart price alerts. `data` (optional) adds flat custom keys to the push
    so the phone can deep-link on tap (e.g. {"type":"price_alert","symbol":"AAPL",
    "alert_id":42}). On APNs they sit alongside `aps`; on Expo they go in `data`.
    Returns count sent. NO-OP when push is disabled / no tokens / unconfigured.
    NEVER raises.
    """
    custom = dict(data or {})
    try:
        cfg = push_config()
        if not cfg["enabled"]:
            logger.debug("push disabled — skipping alert push")
            return 0

        provider = str(cfg.get("provider") or "expo").strip().lower()

        if provider == "apns":
            key_id = str(cfg.get("apns_key_id") or "").strip()
            team_id = str(cfg.get("apns_team_id") or "").strip()
            bundle = str(cfg.get("apns_bundle_id") or "").strip()
            key_path = str(cfg.get("apns_key_path") or "").strip()
            if not (key_id and team_id and bundle and key_path):
                logger.warning("apns config incomplete — skipping alert push")
                return 0
            try:
                key_pem = Path(key_path).read_bytes()
            except OSError:
                logger.exception("apns key not readable at %s", key_path)
                return 0
            tokens = list_active_device_tokens(token_type="apns", db_path=db_path)
            if not tokens:
                return 0
            jwt = _apns_jwt(key_pem, key_id, team_id)
            host = _APNS_HOST_SANDBOX if cfg.get("apns_use_sandbox") else _APNS_HOST_PROD
            payload = {
                "aps": {
                    "alert": {"title": title, "body": body},
                    "sound": "default",
                },
                "type": "alert",
                **custom,  # flat custom keys (may override "type")
            }
            headers = {
                "authorization": f"bearer {jwt}",
                "apns-topic": bundle,
                "apns-push-type": "alert",
                "apns-priority": "10",
            }
            sent = 0
            async with httpx.AsyncClient(http2=True, timeout=15.0) as client:
                for row in tokens:
                    token = str(row["token"])
                    try:
                        resp = await client.post(
                            f"https://{host}/3/device/{token}", headers=headers, json=payload
                        )
                    except httpx.HTTPError:
                        logger.exception("apns alert request failed for %s…", token[:8])
                        continue
                    if resp.status_code == 200:
                        sent += 1
                    else:
                        logger.warning(
                            "apns %s for %s…: %s", resp.status_code, token[:8], resp.text[:200]
                        )

        elif provider == "expo":
            tokens = list_active_device_tokens(token_type="expo", db_path=db_path)
            if not tokens:
                return 0
            messages = [
                {
                    "to": str(row["token"]),
                    "title": title,
                    "body": body,
                    "sound": "default",
                    "priority": "high",
                    **({"data": custom} if custom else {}),
                }
                for row in tokens
            ]
            sent = await _send_expo(messages, cfg)

        else:
            logger.warning("Unknown push provider %r — skipping alert push", provider)
            return 0

        if sent:
            logger.info("Sent alert push %r to %d device(s)", title, sent)
        return sent
    except Exception:
        logger.exception("Failed to send alert push %r", title)
        return 0


async def send_proposal_push(
    proposal: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Send an actionable push for a new proposal to active devices.

    Returns count sent. NO-OP (returns 0) when disabled / no tokens / unconfigured.
    NEVER raises — all failures are logged and swallowed.
    """
    try:
        cfg = push_config()
        if not cfg["enabled"]:
            logger.debug("push disabled")
            return 0

        provider = str(cfg.get("provider") or "expo").strip().lower()

        if provider == "apns":
            sent = await _send_apns(proposal, cfg, db_path)
        elif provider == "expo":
            tokens = list_active_device_tokens(token_type="expo", db_path=db_path)
            if not tokens:
                return 0
            messages = [_build_expo_message(str(row["token"]), proposal) for row in tokens]
            sent = await _send_expo(messages, cfg)
        else:
            logger.warning("Unknown push provider %r — skipping", provider)
            return 0

        if sent:
            logger.info("Sent proposal push for %s to %d device(s)", proposal.get("id"), sent)
        return sent
    except Exception:
        logger.exception("Failed to send proposal push for %s", proposal.get("id"))
        return 0
