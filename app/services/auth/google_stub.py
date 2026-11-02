"""In-process fake of Google's OIDC endpoints for tests and live e2e (GOOGLE_SIGNIN_STUB).

Reachable only outside production (see google_oidc.http_client). It serves a JWKS and a token
endpoint that returns a REAL RS256-signed id_token, so the production verification code (signature,
iss, aud, exp, nonce) and PKCE run unchanged against it.

There is no consent screen here. `stub_authorize` plays Google's part: given the auth URL we built,
it returns the redirect Google would send the browser to. The "authorization code" is self-describing
(identity + the nonce and PKCE challenge from the auth URL), so the server process and the e2e
process need share no state.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

KID = "stub-key-1"
ISSUER = "https://accounts.google.com"
CODE_PREFIX = "stubcode."


@lru_cache(maxsize=1)
def _private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks() -> dict[str, Any]:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_private_key().public_key()))
    return {"keys": [{**jwk, "kid": KID, "alg": "RS256", "use": "sig"}]}


def _b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def _unb64(raw: str) -> dict[str, Any]:
    value = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    return value if isinstance(value, dict) else {}


def stub_authorize(
    auth_url: str,
    *,
    sub: str,
    email: str,
    email_verified: bool = True,
    name: str | None = "Stub Person",
    error: str | None = None,
) -> str:
    """What Google would redirect the browser to after the person consents (or denies)."""
    q = {k: v[0] for k, v in parse_qs(urlparse(auth_url).query).items()}
    if error:
        return f"{q['redirect_uri']}?{urlencode({'error': error, 'state': q['state']})}"
    claims = {
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "name": name,
        "aud": q["client_id"],
        "nonce": q.get("nonce"),
        "challenge": q.get("code_challenge"),
        "redirect_uri": q["redirect_uri"],
    }
    return f"{q['redirect_uri']}?{urlencode({'code': CODE_PREFIX + _b64(claims), 'state': q['state']})}"


def _token(request: httpx.Request) -> httpx.Response:
    form = dict(parse_qsl(request.content.decode()))
    code = form.get("code", "")
    if not code.startswith(CODE_PREFIX):
        return httpx.Response(400, json={"error": "invalid_grant"})
    try:
        c = _unb64(code[len(CODE_PREFIX) :])
    except ValueError:
        return httpx.Response(400, json={"error": "invalid_grant"})
    verifier = form.get("code_verifier", "")
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode()
    if c.get("challenge") != digest.rstrip("=") or c.get("redirect_uri") != form.get(
        "redirect_uri"
    ):
        return httpx.Response(400, json={"error": "invalid_grant"})
    now = int(time.time())
    id_claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": c["aud"],
        "sub": c["sub"],
        "email": c["email"],
        "email_verified": c["email_verified"],
        "name": c.get("name"),
        "nonce": c.get("nonce"),
        "iat": now,
        "exp": now + 3600,
    }
    id_token = jwt.encode(id_claims, _private_key(), algorithm="RS256", headers={"kid": KID})
    return httpx.Response(
        200,
        json={
            "access_token": "stub-access-token",  # nosec B105 - fake token for the in-process stub
            "expires_in": 3599,
            "scope": "openid https://www.googleapis.com/auth/userinfo.email",
            "token_type": "Bearer",  # nosec B105 - fake token for the in-process stub
            "id_token": id_token,
        },
    )


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/oauth2/v3/certs":
        return httpx.Response(200, json=jwks())
    if request.url.path == "/token":
        return _token(request)
    return httpx.Response(404, json={})


def stub_transport() -> httpx.MockTransport:
    return httpx.MockTransport(_handler)
