"""Signed public unsubscribe tokens.

A token is `<send id, 16 bytes b64url>.<HMAC-SHA256, 32 bytes b64url>`. It is a capability: whoever
holds it can unsubscribe that one address in that one workspace, which is exactly what the email
footer promises. Nothing is stored (the MAC is the proof), a forged or edited token is rejected
before any database read, and it never expires: an unsubscribe link must keep working for years.
The key is domain-separated from every other use of the app secret.
"""

import base64
import hashlib
import hmac
import uuid

from app.core.config import settings

_CONTEXT = b"cofoundaz.sales.unsubscribe.v1"


def _key() -> bytes:
    secret = settings.SALES_OUTREACH_UNSUBSCRIBE_KEY or settings.SECRET_KEY
    return hmac.new(secret.encode(), _CONTEXT, hashlib.sha256).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_send(send_id: uuid.UUID) -> str:
    mac = hmac.new(_key(), send_id.bytes, hashlib.sha256).digest()
    return f"{_b64(send_id.bytes)}.{_b64(mac)}"


def verify_token(token: str) -> uuid.UUID | None:
    """The send id the token was issued for, or None for anything not signed by us."""
    if len(token) > 200 or token.count(".") != 1:
        return None
    left, right = token.split(".")
    try:
        raw_id, mac = _unb64(left), _unb64(right)
        send_id = uuid.UUID(bytes=raw_id)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(_key(), send_id.bytes, hashlib.sha256).digest()
    return send_id if hmac.compare_digest(mac, expected) else None
