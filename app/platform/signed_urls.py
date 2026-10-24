"""Short-lived, tamper-evident tokens for private files we serve ourselves (local storage).

A token is `base64url(json{k: storage key, s: owning workspace id, e: unix expiry}) . base64url(hmac_sha256)`. The HMAC key
is derived from SECRET_KEY under a purpose label, so a token minted here can never be replayed as
anything else that uses SECRET_KEY. Cloudinary does its own signing (see `CloudinaryStorage`).
"""

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import NamedTuple

from app.core.config import settings

_PURPOSE = b"cofoundaz.private-file.v1"


class InvalidSignedToken(Exception):  # noqa: N818
    """The token is malformed, tampered with or expired."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(body: str) -> bytes:
    key = hmac.new(settings.SECRET_KEY.encode(), _PURPOSE, hashlib.sha256).digest()
    return hmac.new(key, body.encode(), hashlib.sha256).digest()


class SignedKey(NamedTuple):
    key: str
    startup_id: str


def sign_key(
    key: str, *, startup_id: str, ttl_seconds: int, now: float | None = None
) -> tuple[str, int]:
    """Return (token, unix expiry) granting read access to `key` of one workspace."""
    expires = int((time.time() if now is None else now) + ttl_seconds)
    claims = {"k": key, "s": startup_id, "e": expires}
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    return f"{body}.{_b64(_mac(body))}", expires


def verify_key(token: str, *, now: float | None = None) -> SignedKey:
    """Return the (key, workspace) the token grants, or raise `InvalidSignedToken`."""
    try:
        body, mac = token.split(".", 1)
        if not hmac.compare_digest(_unb64(mac), _mac(body)):
            raise InvalidSignedToken
        claims = json.loads(_unb64(body))
        key, startup, expires = str(claims["k"]), str(claims["s"]), int(claims["e"])
    except InvalidSignedToken:
        raise
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise InvalidSignedToken from exc
    if (time.time() if now is None else now) >= expires:
        raise InvalidSignedToken
    return SignedKey(key, startup)
