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
from db.device_tokens import deactivate_device_token, list_active_device_tokens
from db.live_activities import (
    clear_for_proposal,
    has_other_pending_start,
    list_update_tokens,
    record_activity_start,
)
from db.paper_orders import get_proposal
from db.push_deliveries import record_delivery

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


def _safe_record_delivery(
    proposal_id: str,
    device_token: str,
    channel: str,
    status: str,
    *,
    detail: str | None = None,
    db_path: Path,
) -> None:
    try:
        record_delivery(
            proposal_id,
            device_token,
            channel,
            status,
            detail=detail,
            db_path=db_path,
        )
    except Exception:
        logger.exception(
            "push delivery ledger write failed for %s → %s…",
            proposal_id,
            device_token[:8],
        )


def _apns_failure_detail(resp: httpx.Response) -> tuple[str, bool]:
    """Return (detail, is_unregistered)."""
    reason = ""
    try:
        body = resp.json()
        reason = str(body.get("reason") or "")
    except Exception:
        reason = resp.text[:200]
    detail = f"{resp.status_code} {reason}".strip()
    unregistered = resp.status_code == 410 or reason == "Unregistered"
    return detail, unregistered


async def _send_expo(
    messages: list[dict[str, Any]],
    cfg: dict[str, Any],
    *,
    proposal_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    if not messages:
        return 0
    headers: dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}
    access_token = str(cfg.get("expo_access_token") or "").strip()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(_EXPO_PUSH_URL, json=messages, headers=headers)
        response.raise_for_status()
        payload = response.json()

    tickets = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(tickets, list):
        tickets = []

    sent = 0
    for idx, message in enumerate(messages):
        token = str(message.get("to") or "")
        ticket = tickets[idx] if idx < len(tickets) else {}
        if not isinstance(ticket, dict):
            ticket = {}

        if proposal_id and token:
            if ticket.get("status") == "ok":
                _safe_record_delivery(
                    proposal_id, token, "expo", "sent", db_path=db_path
                )
                sent += 1
            else:
                err_msg = str(ticket.get("message") or "expo error")
                details = ticket.get("details") if isinstance(ticket.get("details"), dict) else {}
                err_code = str(details.get("error") or "")
                if err_code == "DeviceNotRegistered":
                    deactivate_device_token(token, db_path=db_path)
                    _safe_record_delivery(
                        proposal_id,
                        token,
                        "expo",
                        "failed",
                        detail="unregistered — token deactivated",
                        db_path=db_path,
                    )
                else:
                    _safe_record_delivery(
                        proposal_id,
                        token,
                        "expo",
                        "failed",
                        detail=err_msg,
                        db_path=db_path,
                    )
        elif ticket.get("status") == "ok":
            sent += 1

    if not proposal_id:
        if tickets:
            return sum(
                1 for t in tickets if isinstance(t, dict) and t.get("status") == "ok"
            )
        return len(messages)
    return sent


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


async def _send_apns_to_token(
    proposal: dict[str, Any],
    token: str,
    *,
    cfg: dict[str, Any],
    client: httpx.AsyncClient,
    host: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    db_path: Path,
) -> bool:
    proposal_id = str(proposal.get("id") or "")
    try:
        resp = await client.post(
            f"https://{host}/3/device/{token}", headers=headers, json=payload
        )
    except httpx.HTTPError:
        logger.exception("apns request failed for %s…", token[:8])
        if proposal_id:
            _safe_record_delivery(
                proposal_id,
                token,
                "apns",
                "failed",
                detail="http error",
                db_path=db_path,
            )
        return False

    if resp.status_code == 200:
        if proposal_id:
            _safe_record_delivery(proposal_id, token, "apns", "sent", db_path=db_path)
        return True

    detail, unregistered = _apns_failure_detail(resp)
    logger.warning("apns %s for %s…: %s", resp.status_code, token[:8], detail)
    if proposal_id:
        if unregistered:
            deactivate_device_token(token, db_path=db_path)
            _safe_record_delivery(
                proposal_id,
                token,
                "apns",
                "failed",
                detail="unregistered — token deactivated",
                db_path=db_path,
            )
        else:
            _safe_record_delivery(
                proposal_id,
                token,
                "apns",
                "failed",
                detail=detail,
                db_path=db_path,
            )
    return False


async def _send_apns(
    proposal: dict[str, Any],
    cfg: dict[str, Any],
    db_path: Path,
    *,
    tokens: list[str] | None = None,
) -> int:
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

    if tokens is None:
        token_rows = list_active_device_tokens(token_type="apns", db_path=db_path)
        tokens = [str(row["token"]) for row in token_rows]
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
        for token in tokens:
            if await _send_apns_to_token(
                proposal,
                token,
                cfg=cfg,
                client=client,
                host=host,
                headers=headers,
                payload=payload,
                db_path=db_path,
            ):
                sent += 1
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


async def _fire_delivery_alarm(
    proposal: dict[str, Any],
    *,
    reason: str,
    db_path: Path,
) -> None:
    try:
        from signals.notify import notify

        proposal_id = str(proposal.get("id") or "")
        summary = _proposal_body(proposal)
        await notify(
            kind="delivery_alarm",
            title="Proposal push undelivered",
            body=f"{summary}: {reason}",
            severity="critical",
            ref_type="proposal",
            ref_id=proposal_id,
            data={
                "type": "delivery_alarm",
                "proposal_id": proposal_id,
                "reason": reason,
            },
            push=False,
            db_path=db_path,
        )
    except Exception:
        logger.exception("Failed to fire delivery_alarm for %s", proposal.get("id"))


async def send_proposal_push_to_token(
    proposal: dict[str, Any],
    token: str,
    *,
    channel: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """Re-send a proposal push to a single device token. NEVER raises."""
    try:
        cfg = push_config()
        if not cfg["enabled"]:
            return False

        provider = (channel or str(cfg.get("provider") or "expo")).strip().lower()
        if provider == "apns":
            sent = await _send_apns(proposal, cfg, db_path, tokens=[token])
            return sent == 1
        if provider == "expo":
            message = _build_expo_message(token, proposal)
            sent = await _send_expo(
                [message],
                cfg,
                proposal_id=str(proposal.get("id") or ""),
                db_path=db_path,
            )
            return sent == 1
        logger.warning("Unknown push provider %r — skipping single-token send", provider)
        return False
    except Exception:
        logger.exception(
            "Failed to send proposal push for %s to %s…",
            proposal.get("id"),
            token[:8],
        )
        return False


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
        proposal_id = str(proposal.get("id") or "")

        if provider == "apns":
            token_rows = list_active_device_tokens(token_type="apns", db_path=db_path)
            had_tokens = bool(token_rows)
            sent = await _send_apns(proposal, cfg, db_path)
        elif provider == "expo":
            token_rows = list_active_device_tokens(token_type="expo", db_path=db_path)
            had_tokens = bool(token_rows)
            if not token_rows:
                sent = 0
            else:
                messages = [
                    _build_expo_message(str(row["token"]), proposal) for row in token_rows
                ]
                sent = await _send_expo(
                    messages,
                    cfg,
                    proposal_id=proposal_id,
                    db_path=db_path,
                )
        else:
            logger.warning("Unknown push provider %r — skipping", provider)
            return 0

        if sent:
            logger.info("Sent proposal push for %s to %d device(s)", proposal.get("id"), sent)
        elif had_tokens:
            await _fire_delivery_alarm(
                proposal, reason="all push attempts failed", db_path=db_path
            )
        else:
            await _fire_delivery_alarm(
                proposal, reason="no registered device tokens", db_path=db_path
            )
        return sent
    except Exception:
        logger.exception("Failed to send proposal push for %s", proposal.get("id"))
        return 0


# ---------------------------------------------------------------------------
# Live Activity (ActivityKit → APNs)
# ---------------------------------------------------------------------------
_LA_END_DISMISSAL_GRACE_S = 600
# expo-widgets routes a generic LiveActivityAttributes by this name to the widget
# layout; the trade fields live in `props` as a JSON-ENCODED STRING (double-encoded),
# not a nested object. See References/SERV_REPLY_live_activity.md.
_LA_ACTIVITY_NAME = "TradeProposalActivity"


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
        # Forward the calibration verdict, never a bare number: when the sample is
        # insufficient or no win_prob exists, ship ONLY the "unproven" flag and NO
        # winProb/evPct keys (the 07-08 leak was stripping exactly this qualifier).
        # When the number is trustworthy, its qualifier (ci_low + n_samples) travels
        # inseparably with it.
        win_prob = score.get("win_prob")
        if not score.get("sufficient") or win_prob is None:
            out["calibration"] = "unproven"
        else:
            out["winProb"] = win_prob
            out["ciLow"] = score.get("ci_low")
            out["nSamples"] = score.get("n_samples")
            ev_pct = score.get("ev_pct")
            if ev_pct is not None:
                out["evPct"] = ev_pct
        validated = score.get("validated")
        if validated is not None:
            out["validated"] = validated
        strategy_label = score.get("label")
        if strategy_label is not None:
            out["strategyLabel"] = strategy_label
    if status == "failed" and status_reason:
        out["statusReason"] = status_reason
    return out


def _la_wire_content_state(
    proposal: dict[str, Any],
    *,
    status: str,
    status_reason: str | None = None,
) -> dict[str, str]:
    """Wrap the trade fields in the expo-widgets envelope: {name, props} where
    props is the JSON-ENCODED STRING of the content. iOS Codable expects this exact
    shape — top-level fields or a nested-object props silently fail to decode."""
    fields = _la_content_state(proposal, status=status, status_reason=status_reason)
    return {
        "name": _LA_ACTIVITY_NAME,
        "props": json.dumps(fields, separators=(",", ":")),
    }


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
            "content-state": _la_wire_content_state(proposal, status="pending"),
            "stale-date": stale,
            "dismissal-date": dismissal,
            "alert": {
                "title": "Trade proposal",
                "body": f"{side} {qty} {symbol} — approve?",
            },
        }

        proposal_id = str(proposal.get("id") or "")
        sent = 0
        for row in tokens:
            token = str(row["token"])
            if has_other_pending_start(token, proposal_id, db_path=db_path):
                logger.warning(
                    "concurrent LA starts for device %s…— older activity will not receive UPDATE/END",
                    token[:8],
                )
            record_activity_start(proposal_id, token, db_path=db_path)
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

        content_state = _la_wire_content_state(
            row, status=status, status_reason=status_reason
        )
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
