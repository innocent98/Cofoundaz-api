"""Shared helpers for the Auth & Onboarding gap tests."""

from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token, get_password_hash
from app.db.models.enums import UserStatus
from app.db.models.user import User
from app.platform.email import EmailMessage
from tests.factories import create_user

PASSWORD = "password1"


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, msg: EmailMessage) -> None:
        self.sent.append(msg)


def record_emails(monkeypatch: pytest.MonkeyPatch) -> RecordingSender:
    """Capture every email the auth + onboarding endpoints send (no provider is reached)."""
    sender = RecordingSender()
    for target in (
        "app.api.v1.endpoints.auth.registration.get_email_sender",
        "app.api.v1.endpoints.auth.password.get_email_sender",
        "app.api.v1.endpoints.onboarding.complete.get_email_sender",
        "app.services.onboarding.invites.get_email_sender",
    ):
        monkeypatch.setattr(target, lambda: sender)
    return sender


def active_user(db, email: str, **kw) -> User:
    return create_user(
        db,
        email=email,
        password_hash=get_password_hash(PASSWORD),
        status=UserStatus.active,
        email_verified_at=datetime.now(UTC),
        **kw,
    )


def bearer(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}
