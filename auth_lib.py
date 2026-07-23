"""
auth_lib.py — shared JWT + Google-account authorization helpers for the
emeraldbride Lambda functions.

This repo has no dependency/layer packaging (see deploy_upload_lambda.py —
each function is a single zipped .py file), so this module is bundled into
every Lambda zip that needs it rather than imported from a shared layer.
Keep it dependency-free (stdlib only) so it works unmodified in the Lambda
runtime.
"""
import base64
import hashlib
import hmac
import json
import os
import time

JWT_SECRET = os.environ.get("JWT_SECRET", "")
SESSION_COOKIE_NAME = "eb_session"
SESSION_TTL_SECONDS = 8 * 60 * 60  # 8 hours


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def sign_session_jwt(email: str, extra_claims: dict = None) -> str:
    """Issues an HS256-signed session JWT. Raises if JWT_SECRET isn't configured
    — we never want to silently issue an unsigned/unsignable token."""
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET environment variable is not set")
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"email": email, "iat": now, "exp": now + SESSION_TTL_SECONDS}
    if extra_claims:
        payload.update(extra_claims)
    signing_input = (
        f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url_encode(json.dumps(payload, separators=(',', ':')).encode())}"
    )
    signature = hmac.new(JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def verify_session_jwt(token: str):
    """Returns the decoded claims dict if the signature is valid and the token
    hasn't expired, otherwise None. Never raises on malformed input — callers
    should treat None as "not authenticated", not as an error."""
    if not JWT_SECRET or not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}"
    expected_sig = hmac.new(JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    try:
        actual_sig = _b64url_decode(sig_b64)
    except Exception:
        return None
    # Constant-time comparison — a naive `==` here would leak timing info
    # about how many leading signature bytes matched.
    if not hmac.compare_digest(expected_sig, actual_sig):
        return None
    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except Exception:
        return None
    if not isinstance(claims, dict) or claims.get("exp", 0) < int(time.time()):
        return None
    return claims


def build_session_cookie(token: str, max_age: int = SESSION_TTL_SECONDS) -> str:
    # SameSite=None + Secure is required because the admin page (CloudFront
    # domain) and this API (execute-api domain) are different sites from the
    # browser's point of view — a same-site default would silently drop the
    # cookie on cross-origin fetches.
    return (
        f"{SESSION_COOKIE_NAME}={token}; HttpOnly; Secure; SameSite=None; "
        f"Path=/; Max-Age={max_age}"
    )


def build_logout_cookie() -> str:
    return f"{SESSION_COOKIE_NAME}=; HttpOnly; Secure; SameSite=None; Path=/; Max-Age=0"


def get_token_from_event(event: dict):
    # HTTP API (payload format 2.0) parses the Cookie header into event["cookies"].
    for raw_cookie in event.get("cookies") or []:
        name, _, value = raw_cookie.strip().partition("=")
        if name == SESSION_COOKIE_NAME:
            return value
    # Fallback: Authorization: Bearer <token>, for non-browser/manual testing.
    headers = event.get("headers") or {}
    auth_header = headers.get("authorization") or headers.get("Authorization") or ""
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


def require_auth(event: dict):
    """Returns the claims dict for a valid session, or None if unauthenticated."""
    token = get_token_from_event(event)
    if not token:
        return None
    return verify_session_jwt(token)


def is_authorized_email(email: str, hd_claim: str = "") -> bool:
    """Checks an authenticated Google email against the configured allowlist.

    ALLOWED_EMAILS: comma-separated exact addresses, e.g. "you@gmail.com,partner@gmail.com"
    ALLOWED_DOMAINS: comma-separated Google Workspace domains, e.g. "emeraldbride.com"

    A domain match also requires Google's `hd` claim (present only on Workspace
    accounts, and only settable by Google) to equal that domain — matching just
    the string after "@" in the email would let anyone claim any domain.
    """
    if not email:
        return False
    email = email.lower().strip()

    allowed_emails = {
        e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()
    }
    if email in allowed_emails:
        return True

    allowed_domains = {
        d.strip().lower() for d in os.environ.get("ALLOWED_DOMAINS", "").split(",") if d.strip()
    }
    if not allowed_domains:
        return False

    email_domain = email.rsplit("@", 1)[-1]
    hd = (hd_claim or "").lower().strip()
    return bool(hd) and email_domain in allowed_domains and hd == email_domain
