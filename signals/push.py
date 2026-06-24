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
from db.live_activities import clear_for_proposal, list_update_tokens
from db.paper_orders import get_proposal

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


# ---------------------------------------------------------------------------
# Live Activity (ActivityKit → APNs)
# ---------------------------------------------------------------------------
_LA_END_DISMISSAL_GRACE_S = 600


def _parse_expires_unix(expires_at: str | None) -> int | None:
    if not expires_at:
        return None
    try:
        from datetime import UTC, datetime

        dt = datetime.fromisoformat(str(expires_at))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return None


def _intent_from_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    inner = proposal.get("proposal") if isinstance(proposal.get("proposal"), dict) else {}
    return {
        "symbol": inner.get("symbol") or proposal.get("symbol") or "?",
        "side": str(inner.get("side") or proposal.get("side") or "buy").lower(),
        "qty": inner.get("qty") if inner.get("qty") is not None else proposal.get("qty"),
        "order_type": str(
            inner.get("order_type") or proposal.get("order_type") or "market"
        ).lower(),
    }


def _la_content_state(
    proposal: dict[str, Any],
    *,
    status: str,
    status_reason: str | None = None,
) -> dict[str, Any]:
    intent = _intent_from_proposal(proposal)
    out: dict[str, Any] = {
        "proposalId": proposal["id"],
        "symbol": intent["symbol"],
        "side": intent["side"],
        "qty": intent["qty"],
        "orderType": intent["order_type"],
        "status": status,
    }
    score = proposal.get("score")
    if isinstance(score, dict):
        win_prob = score.get("win_prob")
        if win_prob is not None:
            out["winProb"] = win_prob
    if status == "failed" and status_reason:
        out["statusReason"] = status_reason
    return out


def _la_apns_config(cfg: dict[str, Any]) -> tuple[str, str, str, bytes] | None:
    key_id = str(cfg.get("apns_key_id") or "").strip()
    team_id = str(cfg.get("apns_team_id") or "").strip()
    bundle = str(cfg.get("apns_bundle_id") or "").strip()
    key_path = str(cfg.get("apns_key_path") or "").strip()
    if not (key_id and team_id and bundle and key_path):
        return None
    try:
        key_pem = Path(key_path).read_bytes()
    except OSError:
        logger.exception("apns key not readable at %s", key_path)
        return None
    return key_id, team_id, bundle, key_pem


async def _send_live_activity(
    token: str,
    payload_aps: dict[str, Any],
    *,
    db_path: Path,
) -> bool:
    try:
        cfg = push_config()
        apns = _la_apns_config(cfg)
        if apns is None:
            logger.warning("apns config incomplete — skipping live activity push")
            return False
        key_id, team_id, bundle, key_pem = apns
        jwt = _apns_jwt(key_pem, key_id, team_id)
        host = _APNS_HOST_SANDBOX if cfg.get("apns_use_sandbox") else _APNS_HOST_PROD
        headers = {
            "authorization": f"bearer {jwt}",
            "apns-topic": f"{bundle}.push-type.liveactivity",
            "apns-push-type": "liveactivity",
            "apns-priority": "10",
        }
        async with httpx.AsyncClient(http2=True, timeout=15.0) as client:
            resp = await client.post(
                f"https://{host}/3/device/{token}",
                headers=headers,
                json={"aps": payload_aps},
            )
        if resp.status_code == 200:
            return True
        logger.warning(
            "live activity apns %s for %s…: %s",
            resp.status_code,
            token[:8],
            resp.text[:200],
        )
        return False
    except Exception:
        logger.exception("live activity apns request failed for %s…", token[:8])
        return False


async def start_proposal_live_activity(
    proposal: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Send START Live Activity push to all la_pushtostart tokens. NEVER raises."""
    try:
        cfg = push_config()
        if not cfg["enabled"]:
            logger.debug("push disabled — skipping live activity start")
            return 0

        tokens = list_active_device_tokens(token_type="la_pushtostart", db_path=db_path)
        if not tokens:
            return 0

        if _la_apns_config(cfg) is None:
            return 0

        intent = _intent_from_proposal(proposal)
        side = str(intent["side"]).upper()
        qty = intent["qty"]
        symbol = intent["symbol"]
        ttl_unix = _parse_expires_unix(proposal.get("expires_at"))
        now_unix = int(time.time())
        stale = ttl_unix if ttl_unix is not None else now_unix + 3600
        dismissal = stale

        payload_aps: dict[str, Any] = {
            "timestamp": now_unix,
            "event": "start",
            "attributes-type": "LiveActivityAttributes",
            "attributes": {},
            "content-state": _la_content_state(proposal, status="pending"),
            "stale-date": stale,
            "dismissal-date": dismissal,
            "alert": {
                "title": "Trade proposal",
                "body": f"{side} {qty} {symbol} — approve?",
            },
        }

        sent = 0
        for row in tokens:
            token = str(row["token"])
            if await _send_live_activity(token, payload_aps, db_path=db_path):
                sent += 1
        if sent:
            logger.info(
                "Started live activity for proposal %s on %d device(s)",
                proposal.get("id"),
                sent,
            )
        return sent
    except Exception:
        logger.exception("Failed to start live activity for %s", proposal.get("id"))
        return 0


async def end_proposal_live_activity(
    proposal_id: str,
    *,
    status: str,
    status_reason: str | None = None,
    proposal: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Send UPDATE/END Live Activity push to per-activity tokens. NEVER raises."""
    try:
        cfg = push_config()
        if not cfg["enabled"]:
            logger.debug("push disabled — skipping live activity end")
            return 0

        update_tokens = list_update_tokens(proposal_id, db_path=db_path)
        if not update_tokens:
            return 0

        if _la_apns_config(cfg) is None:
            return 0

        row = proposal if proposal is not None else get_proposal(proposal_id, db_path=db_path)
        if row is None:
            logger.warning("live activity end: proposal %s not found", proposal_id)
            return 0

        content_state = _la_content_state(row, status=status, status_reason=status_reason)
        now_unix = int(time.time())
        is_terminal = status != "approved"

        if is_terminal:
            payload_aps: dict[str, Any] = {
                "timestamp": now_unix,
                "event": "end",
                "content-state": content_state,
                "dismissal-date": now_unix + _LA_END_DISMISSAL_GRACE_S,
            }
        else:
            payload_aps = {
                "timestamp": now_unix,
                "event": "update",
                "content-state": content_state,
            }

        sent = 0
        for entry in update_tokens:
            token = str(entry["update_token"])
            if await _send_live_activity(token, payload_aps, db_path=db_path):
                sent += 1

        if is_terminal:
            clear_for_proposal(proposal_id, db_path=db_path)

        if sent:
            logger.info(
                "Ended live activity for proposal %s (%s) on %d device(s)",
                proposal_id,
                status,
                sent,
            )
        return sent
    except Exception:
        logger.exception("Failed to end live activity for %s", proposal_id)
        return 0
