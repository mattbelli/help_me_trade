"""Who may approve a trade proposal — the authority check in front of the chokepoint.

Approval moves money, so it must not ride the shared API_TOKEN (that token is
compiled into the web bundle and sits on every client). Three authorities, in
order:

1. **Enrolled device signature** — the phone signs
   ``hmt-approve\\n{proposal_id}\\n{payload_hash}\\n{ts}`` with an Ed25519 key
   that lives only in its keychain; we verify against the enrolled public key.
   The signature binds to ONE proposal at ONE set of terms, and the proposal's
   atomic claim makes it single-use, so a captured signature buys nothing.
2. **Admin token** — the desk path (web cockpit). ADMIN_TOKEN is already the
   risk-control scope and is never baked into a client.
3. **Legacy shared token** — only while NO device has ever been enrolled, so a
   freshly deployed server doesn't lock the founder out of his own phone before
   it can enroll. The first enrollment closes this path permanently (ratchet).
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException, Request, status

import db.base as db_base
from api import auth as _auth
from api.auth import _token_matches, extract_token_from_request
from db.approval_devices import any_device_enrolled, get_device, touch_device

logger = logging.getLogger(__name__)

DEVICE_HEADER = "x-approval-device"
SIGNATURE_HEADER = "x-approval-signature"
TIMESTAMP_HEADER = "x-approval-timestamp"

# Phone clocks are NTP-disciplined; 5 minutes tolerates a wedged background
# action while keeping an old capture useless long before its proposal expires.
MAX_CLOCK_SKEW_SEC = 300

# Failed-signature limiter: a wrong key is a bug or an attacker, and either one
# gets a handful of tries per minute, not a brute-force lane. Keyed by CLIENT
# ADDRESS, not device id — device ids are readable with the shared token, and a
# limiter keyed on them would let a token-holder lock the real phone out.
_FAIL_LIMIT = 10
_FAIL_WINDOW_SEC = 60.0
_FAIL_MAX_KEYS = 1000
_failures: dict[str, deque[float]] = defaultdict(deque)


def _require_device() -> bool:
    """APPROVAL_REQUIRE_DEVICE=1 pins the legacy path shut independent of the DB —
    set it once the phone is enrolled, so a fresh/restored trading.db (no
    approval_devices rows) can't silently reopen shared-token approvals."""
    return os.environ.get("APPROVAL_REQUIRE_DEVICE", "").strip().lower() in ("1", "true", "yes")


def approval_message(proposal_id: str, payload_hash: str, ts: str) -> bytes:
    """The exact bytes the phone signs — keep in lockstep with mobile/src/auth/approval-key.ts."""
    return f"hmt-approve\n{proposal_id}\n{payload_hash}\n{ts}".encode()


def _forbid(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _note_failure(key: str) -> None:
    now = time.monotonic()
    if key not in _failures and len(_failures) >= _FAIL_MAX_KEYS:
        _failures.clear()  # bounded memory; a flood resets everyone's window
    q = _failures[key]
    q.append(now)
    while q and now - q[0] > _FAIL_WINDOW_SEC:
        q.popleft()


def _throttled(key: str) -> bool:
    now = time.monotonic()
    q = _failures.get(key)
    if not q:
        return False
    while q and now - q[0] > _FAIL_WINDOW_SEC:
        q.popleft()
    if not q:
        del _failures[key]
        return False
    return len(q) >= _FAIL_LIMIT


def _reset_failures() -> None:
    """Test seam."""
    _failures.clear()


def _verify_device_signature(
    request: Request,
    proposal_id: str,
    payload_hash: str,
    *,
    db_path: Path,
) -> str:
    device_id = request.headers.get(DEVICE_HEADER, "").strip()
    signature_b64 = request.headers.get(SIGNATURE_HEADER, "").strip()
    ts = request.headers.get(TIMESTAMP_HEADER, "").strip()
    if not device_id or not signature_b64 or not ts:
        raise _forbid("approval signature headers incomplete")

    client = _client_key(request)
    if _throttled(client):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed approval signatures — wait a minute",
        )

    device = get_device(device_id, db_path=db_path)
    if device is None or device.get("revoked_at"):
        _note_failure(client)
        raise _forbid("approval device not enrolled")

    try:
        ts_int = int(ts)
    except ValueError:
        _note_failure(client)
        raise _forbid("approval timestamp malformed") from None
    if abs(time.time() - ts_int) > MAX_CLOCK_SKEW_SEC:
        _note_failure(client)
        raise _forbid("approval timestamp outside the allowed window")

    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(device["public_key"], validate=True)
        )
        signature = base64.b64decode(signature_b64, validate=True)
        public_key.verify(signature, approval_message(proposal_id, payload_hash, ts))
    except (InvalidSignature, ValueError, binascii.Error):
        _note_failure(client)
        logger.warning(
            "approval signature REJECTED device=%s proposal=%s", device_id, proposal_id
        )
        raise _forbid("approval signature invalid") from None

    touch_device(device_id, db_path=db_path)
    label = str(device.get("label") or "").strip() or device_id[:8]
    return f"device:{label}"


def authorize_approval(
    request: Request,
    proposal_id: str,
    payload_hash: str,
    *,
    db_path: Path | None = None,
) -> str:
    """Return the approver identity for the decision journal, or raise 403/429.

    Device headers present → the signature MUST verify (a bad signature never
    falls through to a token). Otherwise admin token, then the legacy shared
    token only until the first device is enrolled.
    """
    # Resolved at call time (not a def-time default) so test isolation of
    # db.base.DEFAULT_DB_PATH reaches this path too.
    db_path = db_path or db_base.DEFAULT_DB_PATH
    if request.headers.get(DEVICE_HEADER):
        return _verify_device_signature(request, proposal_id, payload_hash, db_path=db_path)

    token = extract_token_from_request(request)
    if _token_matches(token, _auth.ADMIN_TOKEN):
        return "admin"

    if not _require_device() and not any_device_enrolled(db_path=db_path):
        logger.warning(
            "legacy approval via shared API token for %s — enroll a phone "
            "(Settings → Approval device) to close this path",
            proposal_id,
        )
        return "shared-token"

    raise _forbid(
        "approval requires an enrolled device signature or the admin token"
    )
