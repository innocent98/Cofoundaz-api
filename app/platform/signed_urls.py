"""Short-lived, tamper-evident tokens for private files we serve ourselves (local storage).

A token is `base64url(json{k: storage key, e: unix expiry}) . base64url(hmac_sha256)`. The HMAC key
is derived from SECRET_KEY under a purpose label, so a token minted here can never be replayed as
anything else that uses SECRET_KEY. Cloudinary does its own signing (see `CloudinaryStorage`).
"""

import base64
import binascii
import hashlib
import hmac
import json
import time

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


def sign_key(key: str, *, ttl_seconds: int, now: float | None = None) -> tuple[str, int]:
    """Return (token, unix expiry) granting read access to `key` for `ttl_seconds`."""
    expires = int((time.time() if now is None else now) + ttl_seconds)
    body = _b64(json.dumps({"k": key, "e": expires}, separators=(",", ":")).encode())
    return f"{body}.{_b64(_mac(body))}", expires


def verify_key(token: str, *, now: float | None = None) -> str:
    """Return the storage key the token grants, or raise `InvalidSignedToken`."""
    try:
        body, mac = token.split(".", 1)
        if not hmac.compare_digest(_unb64(mac), _mac(body)):
            raise InvalidSignedToken
        claims = json.loads(_unb64(body))
        key, expires = str(claims["k"]), int(claims["e"])
    except InvalidSignedToken:
        raise
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise InvalidSignedToken from exc
    if (time.time() if now is None else now) >= expires:
        raise InvalidSignedToken
    return key
