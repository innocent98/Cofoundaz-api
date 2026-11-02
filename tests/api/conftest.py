"""Fixtures shared by the API tests of one module."""

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.platform.email import ConsoleEmailSender
from app.services.calendar import invites
from app.services.calendar.stub_transport import stub_reset
from tests.documents_helpers import team


@pytest.fixture()
def cal(db, monkeypatch):
    """(startup, {role: user}, sender): Google configured against the fake, mail recorded."""
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client-id.apps.example")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "placeholder-not-a-real-secret")
    monkeypatch.setattr(settings, "REFRESH_COOKIE_SECURE", False)  # http://testserver
    monkeypatch.setattr(settings, "CALENDAR_PROVIDER_STUB", True)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")  # CI sets no LLM env
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "API_PUBLIC_URL", "https://api.example.test")
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.test")
    monkeypatch.setattr(
        settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    stub_reset()
    sender = ConsoleEmailSender()
    monkeypatch.setattr(invites, "get_email_sender", lambda: sender)
    startup, users = team(db)
    return startup, users, sender
