from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.errors import JournalContentUnreadable, JournalNotConfigured


def _get_fernet() -> Fernet:
    key = settings.JOURNAL_ENCRYPTION_KEY

    if not key:
        raise JournalNotConfigured()

    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise JournalNotConfigured() from exc


def encrypt_content(content: str) -> str:
    """Encrypt journal plaintext and return a Fernet token."""
    if not isinstance(content, str):
        raise TypeError("Journal content must be a string.")

    return _get_fernet().encrypt(content.encode("utf-8")).decode("utf-8")


def decrypt_content(content_encrypted: str) -> str:
    """Decrypt a stored journal token back to plaintext."""
    if not isinstance(content_encrypted, str):
        raise TypeError("Encrypted journal content must be a string.")

    try:
        return _get_fernet().decrypt(content_encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise JournalContentUnreadable() from exc
