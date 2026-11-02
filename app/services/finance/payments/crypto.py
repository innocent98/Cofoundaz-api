from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.errors import FinanceConnectionsNotConfigured, FinanceConnectionUnreadable


def _get_fernet() -> Fernet:
    key = settings.FINANCE_CONNECTIONS_ENCRYPTION_KEY

    if not key:
        raise FinanceConnectionsNotConfigured()

    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise FinanceConnectionsNotConfigured() from exc


def encrypt_secret(plain: str) -> str:
    """Encrypt a provider secret and return a Fernet token."""
    if not isinstance(plain, str):
        raise TypeError("Secret must be a string.")

    return _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    """Decrypt a stored token back to the provider secret."""
    if not isinstance(token, str):
        raise TypeError("Encrypted secret must be a string.")

    fernet = _get_fernet()
    try:
        return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise FinanceConnectionUnreadable() from exc


def encryption_configured() -> bool:
    """True when the server has a usable FINANCE_CONNECTIONS_ENCRYPTION_KEY."""
    try:
        _get_fernet()
    except FinanceConnectionsNotConfigured:
        return False
    return True
