"""
scripts/schwab_reauth.py — headless Schwab OAuth re-authentication.

PURPOSE
-------
Re-mint a fresh Schwab OAuth token without any human interaction, to stay
ahead of Schwab's 7-day refresh-token expiry cliff. Designed to be run daily
via cron (skips when the on-disk token is still fresh).

USAGE
-----
    python -m scripts.schwab_reauth          # recommended (--skip-if-fresh on)
    python -m scripts.schwab_reauth --no-skip-if-fresh
    python scripts/schwab_reauth.py          # also works

CRON EXAMPLE (daily at 06:00 — apply on serv; do not rely on day-of-month stride)
-----------------------------------------------------------------------------------
    0 6 * * * cd /home/mbelli/dev/help_me_trade && /home/mbelli/dev/help_me_trade/.venv/bin/python -m scripts.schwab_reauth >> /home/mbelli/dev/help_me_trade/data/reauth.log 2>&1

ENVIRONMENT (from .env)
-----------------------
    SCHWAB_API_KEY          Schwab developer app key
    SCHWAB_APP_SECRET       Schwab app secret
    SCHWAB_CALLBACK_URL     Must match Schwab portal (https://127.0.0.1:8182)
    SCHWAB_LOGIN_USERNAME   Schwab login e-mail / username
    SCHWAB_LOGIN_PASSWORD   Schwab login password
    SCHWAB_VIP_SECRET       Base32 TOTP secret for Schwab VIP Access OTP

SELECTOR TUNING
---------------
All page selectors are defined as constants near the top of this file.
On the first live run you will likely need to adjust them to match the
actual Schwab login DOM. The constants are clearly labelled — just edit
the strings and re-run.

SAFETY
------
- Imports nothing from trading/; never places orders.
- Never prints/logs passwords, secrets, or token contents.
- On failure: saves a screenshot to $CLAUDE_JOB_DIR/tmp/ (or /tmp/) and
  exits with a non-zero status code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Path bootstrap (works both as `python -m scripts.schwab_reauth`
# and as `python scripts/schwab_reauth.py`)
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

# ---------------------------------------------------------------------------
# PAGE SELECTOR CONSTANTS — edit these if the Schwab login DOM changes
# ---------------------------------------------------------------------------

# Step 1: Username/login-ID field on the initial login page
LOGIN_ID_SELECTOR = "#loginIdInput"

# Step 2: Password field (may appear on same page or after username submit)
PASSWORD_SELECTOR = "#passwordInput"

# Step 3: "Log In" / submit button on the credentials page
LOGIN_BTN_SELECTOR = "#btnLogin"

# Step 4: Security / OTP code input (VIP Access code page)
# Schwab may label this field differently; inspect if it fails.
OTP_INPUT_SELECTOR = "#otpCode"

# Step 5: Submit button on the OTP/security-code page
OTP_SUBMIT_SELECTOR = "#btnContinue"

# Step 6 (optional): "Trust this device" checkbox — checked before submit
# Set to None to skip the trust-device step entirely.
TRUST_DEVICE_CHECKBOX_SELECTOR = "#trustDevice"

# Step 7 (optional): "Don't ask again" / "Remember device" confirm button
TRUST_DEVICE_BTN_SELECTOR = "#btnTrust"

# Step 8: "Allow" / "Accept" / "Authorize" button on the OAuth consent page
AUTHORIZE_BTN_SELECTOR = "#acceptTerms"

# Fallback text-based locator for the authorize button (tried if the above fails)
AUTHORIZE_BTN_TEXT = "Allow"

# ---------------------------------------------------------------------------
# TIMEOUTS (milliseconds) — generous to handle slow Schwab pages
# ---------------------------------------------------------------------------
NAV_TIMEOUT_MS = 60_000       # page navigation / load timeout
ELEMENT_TIMEOUT_MS = 30_000   # wait_for_selector / wait_for_url timeout
REDIRECT_TIMEOUT_MS = 30_000  # wait for the final callback redirect

# ---------------------------------------------------------------------------
# CHROMIUM LAUNCH — system chromium required (bundled playwright chromium
# does NOT work on this Ubuntu 26.04 box with snap confinement)
# ---------------------------------------------------------------------------
CHROMIUM_EXECUTABLE = "/snap/bin/chromium"
CHROMIUM_ARGS = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]

# ---------------------------------------------------------------------------
# Schwab OAuth endpoints
# ---------------------------------------------------------------------------
SCHWAB_AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
SCHWAB_TOKEN_ENDPOINT = "https://api.schwabapi.com/v1/oauth/token"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _token_age_days(path: Path) -> float | None:
    """Return refresh-token age in days, or None if unreadable."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        created_at = raw.get("creation_timestamp")
        if created_at is None:
            return None
        return (time.time() - float(created_at)) / 86400
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _notify_reauth_failure(reason: str) -> None:
    """Best-effort critical push; must not prevent a non-zero exit."""
    try:
        from signals.notify import notify

        asyncio.run(
            notify(
                kind="schwab_health",
                title="Schwab re-auth failed",
                body=reason,
                severity="critical",
                group_key="schwab_reauth_failed",
                data={"auth_status": "reauth_failed"},
            )
        )
    except Exception as exc:
        print(f"[reauth] Could not send failure alert: {exc}", file=sys.stderr)


def _screenshot_path() -> Path:
    """Return a path for a failure screenshot (no secrets in filename)."""
    base = os.environ.get("CLAUDE_JOB_DIR")
    if base:
        d = Path(base) / "tmp"
    else:
        d = Path("/tmp")
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return d / f"schwab_reauth_failure_{ts}.png"


def _build_authorize_url(api_key: str, callback_url: str) -> tuple[str, str]:
    """Return (authorization_url, state) using authlib's OAuth2Client.

    Delegates to schwab-py's ``get_auth_context`` so the URL and state are
    constructed identically to the manual flow.
    """
    from schwab.auth import get_auth_context

    ctx = get_auth_context(api_key, callback_url)
    return ctx.authorization_url, ctx.state


def _save_token(path: Path, token: dict) -> None:
    """Write token to disk in schwab-py's ``{creation_timestamp, token}`` format."""
    payload = {
        "creation_timestamp": int(time.time()),
        "token": token,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    print(f"[reauth] Token saved to {path}")


def _exchange_code_for_token(
    api_key: str,
    app_secret: str,
    callback_url: str,
    received_url: str,
    state: str,
) -> dict:
    """Exchange the received redirect URL for an OAuth token.

    Uses HTTP Basic auth (``auth=(api_key, app_secret)``) because Schwab's
    token endpoint requires ``client_secret_basic``; authlib's default of
    ``client_secret_post`` results in an ``invalid_client`` rejection.

    This replicates exactly what ``schwab.auth.client_from_received_url`` does.
    """
    from authlib.integrations.httpx_client import OAuth2Client

    oauth = OAuth2Client(api_key, redirect_uri=callback_url)
    token = oauth.fetch_token(
        SCHWAB_TOKEN_ENDPOINT,
        authorization_response=received_url,
        client_id=api_key,
        auth=(api_key, app_secret),
        state=state,
    )
    return dict(token)


def _verify_client() -> dict:
    """Instantiate the app's production client (non-interactive) and make one
    read-only call.  Returns info dict.  Raises on failure."""
    from brokers.schwab_auth import create_client

    client = create_client(interactive=False)

    # One cheap, read-only call: fetch the list of account numbers
    response = client.get_account_numbers()
    response.raise_for_status()

    accounts = response.json()
    return {
        "accounts_found": len(accounts),
    }


# ---------------------------------------------------------------------------
# Core headless flow
# ---------------------------------------------------------------------------

def run_headless_reauth() -> None:  # noqa: C901 (complexity acceptable for a flow script)
    """Perform the full headless OAuth re-authentication loop."""

    # -- Read credentials from environment -----------------------------------
    api_key = os.environ.get("SCHWAB_API_KEY", "").strip()
    app_secret = os.environ.get("SCHWAB_APP_SECRET", "").strip()
    callback_url = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182").strip()
    username = os.environ.get("SCHWAB_LOGIN_USERNAME", "").strip()
    password = os.environ.get("SCHWAB_LOGIN_PASSWORD", "").strip()
    vip_secret = os.environ.get("SCHWAB_VIP_SECRET", "").strip()

    missing = [
        name
        for name, val in [
            ("SCHWAB_API_KEY", api_key),
            ("SCHWAB_APP_SECRET", app_secret),
            ("SCHWAB_CALLBACK_URL", callback_url),
            ("SCHWAB_LOGIN_USERNAME", username),
            ("SCHWAB_LOGIN_PASSWORD", password),
            ("SCHWAB_VIP_SECRET", vip_secret),
        ]
        if not val
    ]
    if missing:
        sys.exit(f"[reauth] FATAL: Missing environment variables: {', '.join(missing)}")

    # -- TOTP generator. The actual code is generated JUST before entry (below),
    #    not here — each code is valid only ~30s and login takes ~30s, so a code
    #    minted now would be stale (and a wrong code risks a 2FA lockout).
    import pyotp

    _totp = pyotp.TOTP(vip_secret)

    # -- Build OAuth authorize URL -------------------------------------------
    authorization_url, state = _build_authorize_url(api_key, callback_url)
    print(f"[reauth] Authorization URL built (state={state[:8]}...)")

    # -- Resolve token path ---------------------------------------------------
    from brokers.schwab_auth import token_path as get_token_path

    tok_path = get_token_path()

    # -- Playwright headless flow --------------------------------------------
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    received_url: str | None = None

    # Headed (under xvfb) evades Akamai's headless-browser block better than headless.
    # Toggle with SCHWAB_REAUTH_HEADED=1 (run via `xvfb-run -a` since serv has no display).
    headed = os.environ.get("SCHWAB_REAUTH_HEADED", "").strip().lower() in ("1", "true", "yes")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROMIUM_EXECUTABLE,
            headless=not headed,
            args=CHROMIUM_ARGS,
        )
        context = browser.new_context(
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        page = context.new_page()
        page.set_default_timeout(ELEMENT_TIMEOUT_MS)
        page.set_default_navigation_timeout(NAV_TIMEOUT_MS)

        # Capture the redirect URL (Schwab redirects to 127.0.0.1:8182 which
        # has no server; Playwright raises a net error but the URL is captured
        # before the error fires via the request event.)
        captured_urls: list[str] = []

        def _on_request(request):
            url = request.url
            parsed = urlparse(url)
            if parsed.hostname == "127.0.0.1" and "code=" in url:
                captured_urls.append(url)

        page.on("request", _on_request)

        try:
            # ----------------------------------------------------------------
            # 1. Navigate to Schwab authorize URL
            # ----------------------------------------------------------------
            print("[reauth] Navigating to authorization URL …")
            try:
                page.goto(authorization_url, wait_until="domcontentloaded")
            except Exception:
                # Initial goto can raise due to redirects to Schwab login;
                # that's fine — we just wait for the login page to settle.
                pass

            # ----------------------------------------------------------------
            # 2. Enter username
            # ----------------------------------------------------------------
            print("[reauth] Entering username …")
            page.wait_for_selector(LOGIN_ID_SELECTOR, timeout=ELEMENT_TIMEOUT_MS)
            page.fill(LOGIN_ID_SELECTOR, username)

            # ----------------------------------------------------------------
            # 3. Enter password
            # ----------------------------------------------------------------
            print("[reauth] Entering password …")
            page.wait_for_selector(PASSWORD_SELECTOR, timeout=ELEMENT_TIMEOUT_MS)
            page.fill(PASSWORD_SELECTOR, password)

            # ----------------------------------------------------------------
            # 4. Click login button
            # ----------------------------------------------------------------
            print("[reauth] Clicking login button …")
            page.wait_for_selector(LOGIN_BTN_SELECTOR, timeout=ELEMENT_TIMEOUT_MS)
            page.click(LOGIN_BTN_SELECTOR)

            # ----------------------------------------------------------------
            # 5. OTP / Security code
            # ----------------------------------------------------------------
            print("[reauth] Waiting for OTP input …")
            # Page: "Security Code" label + a single text field + green "Continue".
            otp_input = page.get_by_label("Security Code")
            try:
                otp_input.wait_for(state="visible", timeout=ELEMENT_TIMEOUT_MS)
            except Exception:
                otp_input = page.locator("input:visible").first
                otp_input.wait_for(state="visible", timeout=ELEMENT_TIMEOUT_MS)
            # Generate a FRESH code now; if <8s left in the current 30s window, wait
            # for the next so it stays valid through submission.
            seconds_left = _totp.interval - (int(time.time()) % _totp.interval)
            if seconds_left < 8:
                print(f"[reauth] OTP window {seconds_left}s left — waiting for a fresh code …")
                page.wait_for_timeout((seconds_left + 1) * 1000)
            otp_code = _totp.now()
            print(f"[reauth] OTP code generated (length={len(otp_code)})")
            otp_input.fill(otp_code)

            # ----------------------------------------------------------------
            # 6. Submit OTP
            # ----------------------------------------------------------------
            print("[reauth] Submitting OTP …")
            page.get_by_role("button", name="Continue").click(timeout=ELEMENT_TIMEOUT_MS)

            # ----------------------------------------------------------------
            # 7. Optional: trust-device checkbox + confirm
            # ----------------------------------------------------------------
            if TRUST_DEVICE_CHECKBOX_SELECTOR:
                try:
                    page.wait_for_selector(
                        TRUST_DEVICE_CHECKBOX_SELECTOR, timeout=8_000
                    )
                    page.check(TRUST_DEVICE_CHECKBOX_SELECTOR)
                    print("[reauth] Checked 'trust device'")
                    if TRUST_DEVICE_BTN_SELECTOR:
                        try:
                            page.wait_for_selector(
                                TRUST_DEVICE_BTN_SELECTOR, timeout=5_000
                            )
                            page.click(TRUST_DEVICE_BTN_SELECTOR)
                            print("[reauth] Clicked trust-device confirm button")
                        except PlaywrightTimeout:
                            print("[reauth] Trust-device confirm button absent — skipping")
                except PlaywrightTimeout:
                    print("[reauth] Trust-device step absent — skipping")

            # ----------------------------------------------------------------
            # 8. Consent / Authorize page
            # ----------------------------------------------------------------
            # 8. Consent / Terms page (selectors confirmed from the live page):
            #    required checkbox #acceptTerms → a T&C modal with #agree-modal-btn-
            #    ("Accept") → #submit-btn ("Continue"). Order varies, so handle robustly.
            print("[reauth] Handling consent / terms page …")

            def _accept_modal_if_present() -> None:
                try:
                    btn = page.locator("#agree-modal-btn-")
                    if btn.count() and btn.first.is_visible():
                        btn.first.click(timeout=5_000)
                        print("[reauth] Accepted T&C modal")
                        page.wait_for_timeout(800)
                except Exception:
                    pass

            try:
                cb = page.locator("#acceptTerms")
                cb.wait_for(state="visible", timeout=ELEMENT_TIMEOUT_MS)
                _accept_modal_if_present()  # clicking T&C link may auto-open a modal
                if not cb.is_checked():
                    cb.check(timeout=8_000)
                    print("[reauth] Checked acceptTerms")
                _accept_modal_if_present()
            except Exception as exc:
                print(f"[reauth] acceptTerms handling: {exc}")

            try:
                page.locator("#submit-btn").click(timeout=ELEMENT_TIMEOUT_MS)
                print("[reauth] Clicked consent Continue (#submit-btn)")
            except Exception as exc:
                print(f"[reauth] #submit-btn click: {exc}")

            # The "Instruction and Informed Consent" modal opens AFTER Continue — wait
            # for its Accept button to render (don't check too early), then click it.
            try:
                accept_btn = page.locator("#agree-modal-btn-")
                accept_btn.wait_for(state="visible", timeout=ELEMENT_TIMEOUT_MS)
                accept_btn.click()
                print("[reauth] Accepted Informed-Consent modal")
            except Exception as exc:
                print(f"[reauth] consent modal Accept: {exc}")
            page.wait_for_timeout(2000)

            # 8b. Account-selection page ("Select your Schwab accounts to link") —
            #     accounts default to checked; click its Continue (#submit-btn) → redirect.
            try:
                cont = page.locator("#submit-btn")
                cont.wait_for(state="visible", timeout=ELEMENT_TIMEOUT_MS)
                cont.click()
                print("[reauth] Clicked account-link Continue (#submit-btn)")
                page.wait_for_timeout(2000)
            except Exception as exc:
                print(f"[reauth] account-link Continue: {exc}")

            # 8c. "Review your selected accounts" → final "Done" button. Schwab
            #     reuses id=cancel-btn for it (sic), so match by accessible name.
            try:
                done_btn = page.get_by_role("button", name="Done")
                done_btn.wait_for(state="visible", timeout=ELEMENT_TIMEOUT_MS)
                done_btn.click()
                print("[reauth] Clicked Done on review page")
                page.wait_for_timeout(2000)
            except Exception as exc:
                print(f"[reauth] review-page Done: {exc}")

            # ----------------------------------------------------------------
            # 9. Wait for the redirect to the callback URL
            # ----------------------------------------------------------------
            print("[reauth] Waiting for callback redirect …")
            # The page will throw a net error (no server at 127.0.0.1:8182)
            # but the request event fires first, capturing the URL.
            try:
                page.wait_for_url(
                    "**//*127.0.0.1*/**",
                    timeout=REDIRECT_TIMEOUT_MS,
                )
            except Exception:
                # Expected: net::ERR_CONNECTION_REFUSED / cert error
                pass

            # Fallback: check for URL via current URL
            if not captured_urls:
                current = page.url
                if "127.0.0.1" in current and "code=" in current:
                    captured_urls.append(current)

            # Another fallback: poll page.url briefly
            if not captured_urls:
                deadline = time.time() + 10
                while time.time() < deadline:
                    cur = page.url
                    if "127.0.0.1" in cur and "code=" in cur:
                        captured_urls.append(cur)
                        break
                    time.sleep(0.3)

            if not captured_urls:
                raise RuntimeError(
                    "Never received the OAuth callback redirect to 127.0.0.1:8182. "
                    "The authorize step may have failed or the browser was blocked."
                )

            received_url = captured_urls[0]
            print("[reauth] Captured callback redirect URL (contains code=…)")

        except Exception as exc:
            # Save a screenshot + page HTML for debugging (no secrets in the DOM here).
            shot = _screenshot_path()
            try:
                page.screenshot(path=str(shot))
                print(f"[reauth] Failure screenshot saved: {shot}", file=sys.stderr)
            except Exception as ss_err:
                print(f"[reauth] Could not save screenshot: {ss_err}", file=sys.stderr)
            try:
                html_path = str(shot).replace(".png", ".html")
                with open(html_path, "w", encoding="utf-8") as fh:
                    fh.write(page.content())
                print(f"[reauth] Failure page HTML saved: {html_path}", file=sys.stderr)
            except Exception as html_err:
                print(f"[reauth] Could not save page HTML: {html_err}", file=sys.stderr)
            browser.close()
            sys.exit(f"[reauth] FATAL during Playwright flow: {exc}")

        browser.close()

    # -- Exchange code for token (outside browser context) -------------------
    print("[reauth] Exchanging authorization code for token …")
    try:
        token = _exchange_code_for_token(
            api_key=api_key,
            app_secret=app_secret,
            callback_url=callback_url,
            received_url=received_url,
            state=state,
        )
    except Exception as exc:
        sys.exit(f"[reauth] FATAL: Token exchange failed: {exc}")

    # -- Save token ----------------------------------------------------------
    _save_token(tok_path, token)

    # -- Verify round-trip ---------------------------------------------------
    print("[reauth] Verifying new token with read-only API call …")
    try:
        info = _verify_client()
    except Exception as exc:
        sys.exit(
            f"[reauth] FATAL: Token saved but verification call failed: {exc}. "
            f"Token is at {tok_path}; you may need to re-run or re-authenticate manually."
        )

    # -- Report success ------------------------------------------------------
    # Compute refresh-token age from the token we just saved
    try:
        raw = json.loads(tok_path.read_text(encoding="utf-8"))
        created_at = float(raw.get("creation_timestamp", time.time()))
        age_seconds = time.time() - created_at
        age_minutes = age_seconds / 60
    except Exception:
        age_minutes = 0.0

    print(
        f"[reauth] SUCCESS — new token minted, {info['accounts_found']} account(s) visible, "
        f"token age {age_minutes:.1f}m. "
        f"Refresh-token clock reset; next reauth due in ~7 days."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _restart_backend() -> None:
    """Kill hmt-backend's MainPID so systemd (Restart=always) respawns it with the freshly
    minted token. No sudo: `systemctl show` is read-only and the process runs as us."""
    try:
        res = subprocess.run(
            ["systemctl", "show", "hmt-backend", "--property", "MainPID", "--value"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        pid = int((res.stdout or "0").strip() or "0")
        if pid <= 1:
            print(f"[reauth] backend restart skipped: MainPID={pid} (not running?)")
            return
        os.kill(pid, signal.SIGTERM)
        print(f"[reauth] restarted hmt-backend (SIGTERM → pid {pid}); systemd respawns with the fresh token")
    except Exception as exc:  # a restart failure must never fail the reauth
        print(
            f"[reauth] backend restart FAILED ({exc}); token IS saved — restart manually: "
            f"kill $(systemctl show hmt-backend -p MainPID --value)"
        )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Headless Schwab OAuth re-authentication")
    parser.add_argument(
        "--skip-if-fresh",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip reauth when the on-disk token is <5 days old (default: on)",
    )
    parser.add_argument(
        "--restart-backend",
        action="store_true",
        default=False,
        help=(
            "After a successful mint, SIGTERM hmt-backend's MainPID so systemd "
            "respawns it with the fresh token (the 07-08 stale-in-memory fix). "
            "DENY-BY-DEFAULT: this flag is how the cron opts in — nothing that "
            "merely imports/calls main() may kill a live service. (Found the hard "
            "way 07-21: a pytest test exercising main() TERM'd the production "
            "backend on every full-suite CI run.)"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    print(
        f"[reauth] Schwab headless re-authentication starting "
        f"({datetime.now(UTC).isoformat(timespec='seconds')})"
    )

    from brokers.schwab_auth import token_path as get_token_path

    if args.skip_if_fresh:
        age_days = _token_age_days(get_token_path())
        if age_days is not None and age_days < 5.0:
            print(f"[reauth] token fresh ({age_days:.1f} days) — skipping reauth")
            return 0

    try:
        run_headless_reauth()
    except SystemExit as exc:
        if exc.code not in (None, 0):
            reason = str(exc.code) if isinstance(exc.code, str) else "Schwab re-auth failed"
            _notify_reauth_failure(reason)
        raise
    except KeyboardInterrupt:
        print("\n[reauth] Interrupted.", file=sys.stderr)
        _notify_reauth_failure("Schwab re-auth interrupted")
        return 1
    except Exception as exc:
        _notify_reauth_failure(f"Schwab re-auth failed: {exc}")
        raise
    if args.restart_backend:
        _restart_backend()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as exc:
        raise SystemExit(exc.code if exc.code is not None else 1) from None
