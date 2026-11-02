"""Retrievable-but-hashed public link tokens.

A public token (a share link, a signer link) must be unguessable and must not sit in the database
in a usable form, yet the owner has to be able to copy the same link again later. So the token is
derived: `HMAC-SHA256(SECRET_KEY, purpose:id:version)`. Only its SHA-256 is stored (what the public
route looks up), the raw value is recomputed on demand for an editor, and bumping `version`
rotates it. Rotating SECRET_KEY invalidates every outstanding link of this kind (documented in the
SOP); a database leak alone yields no usable token.
"""

import base64
import hashlib
import hmac
import uuid

from app.core.config import settings
from app.services.auth.sessions import hash_token

MAX_TOKEN_LENGTH = 128  # a derived token is 43 chars; anything longer is never one of ours


def derive_token(purpose: str, row_id: uuid.UUID, version: int = 0) -> str:
    digest = hmac.new(
        settings.SECRET_KEY.encode(), f"cofoundaz-{purpose}:{row_id}:{version}".encode(), "sha256"
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def token_hash(raw: str) -> str:
    return hash_token(raw)


def matches(stored_hash: str, raw: str) -> bool:
    """Constant-time comparison of a presented token with a stored hash."""
    return hmac.compare_digest(stored_hash.encode(), hash_token(raw).encode())


def plausible(raw: str) -> bool:
    return 0 < len(raw) <= MAX_TOKEN_LENGTH and raw.isascii()


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
