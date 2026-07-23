import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from auth_lib import build_session_cookie, is_authorized_email, sign_session_jwt

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "")

ALLOWED_ORIGINS = {
    "https://d27ppwg0xrr4g5.cloudfront.net",
    "http://localhost:3000",
    "http://localhost:5500",
    "http://localhost:8000",
    "http://127.0.0.1:5500",
}


def get_cors_origin(event):
    origin = (event.get("headers") or {}).get("origin", "")
    if origin in ALLOWED_ORIGINS:
        return origin
    return "https://d27ppwg0xrr4g5.cloudfront.net"


def _cors_headers(event):
    return {
        "Access-Control-Allow-Origin": get_cors_origin(event),
        "Access-Control-Allow-Methods": "POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Credentials": "true",
    }


def _error(cors_headers, status, message, detail=None):
    body = {"error": message}
    if detail:
        body["detail"] = detail
    return {
        "statusCode": status,
        "headers": {**cors_headers, "Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _b64url_decode_json(segment: str) -> dict:
    padding = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + padding))


def _exchange_code_for_tokens(code: str) -> dict:
    payload = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            # Google requires this to exactly match the redirect_uri used in the
            # original authorize request — we always use our own single fixed
            # value rather than trusting anything from the client for this.
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    ).encode()
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read())


def lambda_handler(event, context):
    cors_headers = _cors_headers(event)

    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return {"statusCode": 200, "headers": cors_headers, "body": ""}

    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI):
        return _error(cors_headers, 500, "OAuth is not configured on the server")

    try:
        data = json.loads(event.get("body") or "{}")
    except (json.JSONDecodeError, TypeError):
        data = {}

    code = (data.get("code") or "").strip()
    if not code:
        return _error(cors_headers, 400, "Missing 'code'")

    try:
        tokens = _exchange_code_for_tokens(code)
    except urllib.error.HTTPError as e:
        return _error(
            cors_headers, 401, "Google rejected the authorization code",
            detail=e.read().decode(errors="replace"),
        )
    except Exception as e:
        return _error(cors_headers, 502, f"Token exchange failed: {e}")

    id_token = tokens.get("id_token")
    if not id_token:
        return _error(cors_headers, 401, "Google did not return an ID token")

    try:
        _header_b64, payload_b64, _sig_b64 = id_token.split(".")
        claims = _b64url_decode_json(payload_b64)
    except Exception:
        return _error(cors_headers, 401, "Malformed ID token from Google")

    # The ID token was obtained via a direct, authenticated (client_secret)
    # server-to-server HTTPS call to Google, so its transport authenticity is
    # already established — we intentionally don't re-verify Google's RS256
    # signature (that would require fetching and rotating Google's JWKS).
    # These checks are defense-in-depth against misconfiguration, not the
    # primary trust boundary.
    if claims.get("aud") != GOOGLE_CLIENT_ID:
        return _error(cors_headers, 401, "ID token audience mismatch")
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        return _error(cors_headers, 401, "ID token issuer mismatch")
    if claims.get("exp", 0) < time.time():
        return _error(cors_headers, 401, "ID token expired")
    if not claims.get("email_verified"):
        return _error(cors_headers, 401, "Google email is not verified")

    email = (claims.get("email") or "").lower().strip()
    hd = claims.get("hd", "")

    if not is_authorized_email(email, hd):
        return _error(cors_headers, 403, f"{email} is not authorized to access this admin panel")

    session_token = sign_session_jwt(email, {"hd": hd})

    return {
        "statusCode": 200,
        "headers": {**cors_headers, "Content-Type": "application/json"},
        "cookies": [build_session_cookie(session_token)],
        "body": json.dumps({"email": email}),
    }
