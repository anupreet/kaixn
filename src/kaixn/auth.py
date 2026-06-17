"""GitHub OAuth + signed-cookie sessions for the PM copilot gate.

Minimal and dependency-light: the session is an HMAC-signed cookie (stdlib), and
the OAuth dance uses httpx (already a dependency). No database, no session store —
the cookie *is* the session. The whole feature is **flag-gated** on the presence
of the GitHub OAuth credentials, so the app runs fully open until they're set.

Required env to enable (set in kaixn's secrets):
  KAIXN_GITHUB_CLIENT_ID, KAIXN_GITHUB_CLIENT_SECRET  — the GitHub OAuth App
  KAIXN_SESSION_SECRET                                — cookie-signing key (stable)
  KAIXN_PUBLIC_URL (optional)                         — e.g. https://app.kaixn.com
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

_AUTHORIZE = "https://github.com/login/oauth/authorize"
_TOKEN = "https://github.com/login/oauth/access_token"
_USER = "https://api.github.com/user"
_TTL = 7 * 86400  # 1 week

# A stable signing key from env; a per-process random fallback keeps single-task
# dev working (sessions just reset on restart) without leaving the key unset.
_FALLBACK_SECRET = secrets.token_urlsafe(32)


def auth_enabled() -> bool:
    """The gate is active only when the GitHub OAuth App is configured."""
    return bool(os.getenv("KAIXN_GITHUB_CLIENT_ID") and os.getenv("KAIXN_GITHUB_CLIENT_SECRET"))


def _secret() -> bytes:
    return (os.getenv("KAIXN_SESSION_SECRET") or _FALLBACK_SECRET).encode()


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# --- signed-cookie session --------------------------------------------------
def make_session(user: dict, *, ttl: int = _TTL) -> str:
    body = {"login": user.get("login"), "id": user.get("id"),
            "avatar": user.get("avatar_url", ""), "exp": int(time.time()) + ttl}
    payload = json.dumps(body, separators=(",", ":")).encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return f"{_b64e(payload)}.{_b64e(sig)}"


def read_session(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    enc_payload, enc_sig = token.split(".", 1)
    try:
        payload, sig = _b64d(enc_payload), _b64d(enc_sig)
    except (ValueError, base64.binascii.Error):
        return None
    if not hmac.compare_digest(sig, hmac.new(_secret(), payload, hashlib.sha256).digest()):
        return None
    try:
        body = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if body.get("exp", 0) < time.time():
        return None
    return body


# --- OAuth dance ------------------------------------------------------------
def new_state() -> str:
    return secrets.token_urlsafe(16)


def authorize_url(redirect_uri: str, state: str) -> str:
    return _AUTHORIZE + "?" + urlencode({
        "client_id": os.getenv("KAIXN_GITHUB_CLIENT_ID", ""),
        "redirect_uri": redirect_uri, "scope": "read:user",
        "state": state, "allow_signup": "true"})


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Trade the OAuth code for an access token, then fetch the GitHub user.
    Returns the user dict (login, id, avatar_url). Raises on failure."""
    import httpx

    tok_resp = httpx.post(_TOKEN, headers={"Accept": "application/json"}, data={
        "client_id": os.getenv("KAIXN_GITHUB_CLIENT_ID"),
        "client_secret": os.getenv("KAIXN_GITHUB_CLIENT_SECRET"),
        "code": code, "redirect_uri": redirect_uri}, timeout=15.0)
    tok_resp.raise_for_status()
    token = tok_resp.json().get("access_token")
    if not token:
        raise RuntimeError("GitHub returned no access_token")
    user = httpx.get(_USER, headers={"Authorization": f"Bearer {token}",
                                     "Accept": "application/vnd.github+json"}, timeout=15.0)
    user.raise_for_status()
    return user.json()
