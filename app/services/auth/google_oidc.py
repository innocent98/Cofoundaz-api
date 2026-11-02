"""Google OpenID Connect for sign-in: authorization-code flow with PKCE, id_token verification.

Separate from the Calendar OAuth (app/services/calendar/google.py): own redirect URI, scopes
`openid email profile` only (never Calendar), and we keep NO Google token after sign-in.

The id_token is verified the way Google documents: RS256 signature against Google's JWKS, `iss`,
`aud` (our client id), `exp`, and the `nonce` we bound to this browser's login attempt.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.core.config import settings
from app.core.errors import AppError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  # nosec B105 - a URL, not a secret
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPES = ("openid", "email", "profile")
# The stub lets a caller mint any identity, so it runs ONLY where these names say so.
STUB_ENVIRONMENTS = frozenset({"development", "test"})
TIMEOUT_SECONDS = 10.0
JWKS_TTL_SECONDS = 3600

_jwks_cache: dict[str, Any] = {"at": 0.0, "keys": {}}


class GoogleSignInNotConfigured(AppError):  # noqa: N818
    code, http_status = "GOOGLE_SIGNIN_NOT_CONFIGURED", 503
    message = "Google sign-in isn't available right now."


class GoogleIdentityError(Exception):
    """The code exchange or the id_token was rejected: treat the sign-in as failed."""


def http_client() -> httpx.Client:
    if settings.GOOGLE_SIGNIN_STUB and settings.ENVIRONMENT in STUB_ENVIRONMENTS:
        from app.services.auth.google_stub import stub_transport

        return httpx.Client(transport=stub_transport(), timeout=TIMEOUT_SECONDS)
    return httpx.Client(timeout=TIMEOUT_SECONDS)


def configured() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def redirect_uri() -> str:
    base = settings.API_PUBLIC_URL.rstrip("/") or f"{settings.SERVER_HOST}:{settings.SERVER_PORT}"
    return f"{base}{settings.API_V1_STR}/auth/oauth/google/callback"


def new_pkce() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def build_auth_url(*, state: str, nonce: str, challenge: str) -> str:
    if not configured():
        raise GoogleSignInNotConfigured()
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _signing_key(kid: str) -> Any:
    now = time.monotonic()
    stale = now - float(_jwks_cache["at"]) > JWKS_TTL_SECONDS
    if stale or kid not in _jwks_cache["keys"]:
        try:
            with http_client() as http:
                resp = http.get(JWKS_URL)
            resp.raise_for_status()
            keys = {k["kid"]: k for k in resp.json().get("keys", []) if "kid" in k}
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise GoogleIdentityError("jwks_unavailable") from exc
        _jwks_cache.update(at=now, keys=keys)
    jwk = _jwks_cache["keys"].get(kid)
    if jwk is None:
        raise GoogleIdentityError("unknown_key")
    return jwt.PyJWK(jwk).key


def verify_id_token(id_token: str, *, nonce: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(id_token)
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise GoogleIdentityError("bad_alg")
        claims = jwt.decode(
            id_token,
            _signing_key(str(header["kid"])),
            algorithms=["RS256"],
            audience=str(settings.GOOGLE_CLIENT_ID),
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=30,
        )
    except jwt.PyJWTError as exc:
        raise GoogleIdentityError("invalid_token") from exc
    if claims.get("iss") not in ISSUERS:
        raise GoogleIdentityError("bad_issuer")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise GoogleIdentityError("bad_nonce")
    return dict(claims)


def exchange_code(code: str, *, verifier: str) -> str:
    """Trade the authorization code (+ PKCE verifier) for the id_token. Nothing else is kept."""
    if not configured():
        raise GoogleSignInNotConfigured()
    try:
        with http_client() as http:
            resp = http.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": str(settings.GOOGLE_CLIENT_ID),
                    "client_secret": str(settings.GOOGLE_CLIENT_SECRET),
                    "redirect_uri": redirect_uri(),
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                },
            )
        data = resp.json() if resp.status_code == 200 else {}
    except (httpx.HTTPError, ValueError) as exc:
        raise GoogleIdentityError("network") from exc
    token = data.get("id_token") if isinstance(data, dict) else None
    if not isinstance(token, str):
        raise GoogleIdentityError("exchange_failed")
    return token
