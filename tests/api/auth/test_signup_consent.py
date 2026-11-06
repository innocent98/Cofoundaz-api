"""Gap 14: sign-up requires the Terms/Privacy checkbox and stores when and which version."""

import pytest

from app.core.config import settings
from app.db.models.audit import AuditLog
from app.db.models.user import User
from tests.auth_helpers import record_emails

SIGNUP = "/api/v1/auth/signup"


@pytest.fixture(autouse=True)
def _enforce_terms(monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_REQUIRE_TERMS", True)
    monkeypatch.setattr(settings, "TERMS_VERSION", "2026-11-05")


@pytest.mark.parametrize("extra", [{}, {"accepted_terms": False}])
def test_signup_without_consent_is_422_and_creates_nothing(client, db, monkeypatch, extra):
    sender = record_emails(monkeypatch)
    r = client.post(SIGNUP, json={"email": "noconsent@x.com", "password": "password1", **extra})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "TERMS_NOT_ACCEPTED"
    assert err["message"] == "Accept the Terms and Privacy Policy to create your account."
    assert err["field_errors"][0]["field"] == "accepted_terms"
    assert db.query(User).filter_by(email="noconsent@x.com").count() == 0
    assert sender.sent == []


def test_signup_with_consent_stores_time_and_version(client, db, monkeypatch):
    record_emails(monkeypatch)
    r = client.post(
        SIGNUP, json={"email": "consent@x.com", "password": "password1", "accepted_terms": True}
    )
    assert r.status_code == 201, r.text
    user = db.query(User).filter_by(email="consent@x.com").one()
    assert user.terms_accepted_at is not None
    assert user.terms_version == "2026-11-05"
    audit = db.query(AuditLog).filter_by(action="auth.user.registered", actor_user_id=user.id).one()
    assert audit.details == {"terms_version": "2026-11-05"}


def test_consent_is_not_required_when_the_deploy_flag_is_off(client, db, monkeypatch):
    record_emails(monkeypatch)
    monkeypatch.setattr(settings, "SIGNUP_REQUIRE_TERMS", False)
    r = client.post(SIGNUP, json={"email": "legacy-fe@x.com", "password": "password1"})
    assert r.status_code == 201
    user = db.query(User).filter_by(email="legacy-fe@x.com").one()
    assert user.terms_accepted_at is None and user.terms_version is None
