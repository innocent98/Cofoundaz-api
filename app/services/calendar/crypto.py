"""Token encryption for calendar connections: the Finance Fernet helper, with calendar errors.

Same key (FINANCE_CONNECTIONS_ENCRYPTION_KEY) on purpose: one provider-secret key per
environment to provision and rotate, as in `app/services/finance/payments/crypto.py`.
"""

from __future__ import annotations

from app.core.errors import (
    CalendarConnectionUnreadable,
    CalendarSyncNotConfigured,
    FinanceConnectionsNotConfigured,
    FinanceConnectionUnreadable,
)
from app.services.finance.payments import crypto as _fin


def encrypt_token(plain: str) -> str:
    try:
        return _fin.encrypt_secret(plain)
    except FinanceConnectionsNotConfigured as exc:
        raise CalendarSyncNotConfigured() from exc


def decrypt_token(token: str) -> str:
    try:
        return _fin.decrypt_secret(token)
    except FinanceConnectionsNotConfigured as exc:
        raise CalendarSyncNotConfigured() from exc
    except FinanceConnectionUnreadable as exc:
        raise CalendarConnectionUnreadable() from exc
