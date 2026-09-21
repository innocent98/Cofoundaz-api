import re
from datetime import timedelta

import pytest

from app.api.v1.endpoints.auth import registration as registration_module
from app.core.redis import get_redis
from app.db.models.enums import AuthTokenPurpose, UserStatus
from app.db.models.user import User
from app.platform.email import ConsoleEmailSender
from app.services.auth.tokens import issue_auth_token
from tests.factories import create_user


def _install_recording_sender(monkeypatch: pytest.MonkeyPatch) -> ConsoleEmailSender:
    """`get_email_sender()` returns a *new* ConsoleEmailSender on every call, so a
    shared instance can't be observed via the factory. Patch the registration
    module's bound reference to always hand back the same instance, so its
    `.sent` list accumulates across calls within a test."""
    sender = ConsoleEmailSender()
    monkeypatch.setattr(registration_module, "get_email_sender", lambda: sender)
    return sender


def test_signup_creates_pending_user(client, db):
    r = client.post("/api/v1/auth/signup", json={"email": "New@x.com", "password": "password1"})
    assert r.status_code == 201, r.text
    body = r.json()["data"]
    assert body["verification_sent"] is True
    assert body["user"]["email"] == "New@x.com"
    u = db.query(User).filter(User.email == "New@x.com").one()
    assert u.status == UserStatus.pending_verification


def test_signup_weak_password(client):
    r = client.post("/api/v1/auth/signup", json={"email": "a@x.com", "password": "short"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "WEAK_PASSWORD"


def test_signup_duplicate_email(client, db):
    create_user(db, email="dup@x.com")
    db.flush()
    r = client.post("/api/v1/auth/signup", json={"email": "dup@x.com", "password": "password1"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EMAIL_TAKEN"


def test_verify_activates_user(client, db):
    u = create_user(db, email="v@x.com", status=UserStatus.pending_verification)
    raw = issue_auth_token(db, u, AuthTokenPurpose.email_verification, timedelta(hours=24))
    db.flush()
    r = client.post("/api/v1/auth/verify", json={"token": raw})
    assert r.status_code == 200
    db.refresh(u)
    assert u.status == UserStatus.active and u.email_verified_at is not None


def test_verify_bad_token(client):
    r = client.post("/api/v1/auth/verify", json={"token": "nope"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "TOKEN_INVALID"


def test_resend_is_generic_for_unknown_email(client):
    r = client.post("/api/v1/auth/verify/resend", json={"email": "ghost@x.com"})
    assert r.status_code == 200  # no enumeration


def test_resend_invalidates_prior_unconsumed_verification_token(client, db, monkeypatch):
    sender = _install_recording_sender(monkeypatch)
    # This test depends on the resend actually firing (not throttled), so clear any
    # cooldown key a prior run within the last 60s may have left behind for this email.
    get_redis().delete("verify_resend_cooldown:old@x.com")
    u = create_user(db, email="old@x.com", status=UserStatus.pending_verification)
    old_raw = issue_auth_token(db, u, AuthTokenPurpose.email_verification, timedelta(hours=24))
    db.flush()

    r = client.post("/api/v1/auth/verify/resend", json={"email": "old@x.com"})
    assert r.status_code == 200
    assert len(sender.sent) == 1
    new_raw = re.search(  # token now lives in the verify-email link, not a <code> tag
        r"/verify-email/([A-Za-z0-9_-]+)", sender.sent[-1].html
    ).group(
        1
    )  # type: ignore[union-attr]

    stale = client.post("/api/v1/auth/verify", json={"token": old_raw})
    assert stale.status_code == 400
    assert stale.json()["error"]["code"] == "TOKEN_INVALID"

    fresh = client.post("/api/v1/auth/verify", json={"token": new_raw})
    assert fresh.status_code == 200


def test_resend_does_not_email_already_active_user(client, db, monkeypatch):
    sender = _install_recording_sender(monkeypatch)
    create_user(db, email="active@x.com", status=UserStatus.active)

    r = client.post("/api/v1/auth/verify/resend", json={"email": "active@x.com"})
    assert r.status_code == 200
    assert r.json()["data"] == {"sent": True}  # still the generic response — no enumeration
    assert sender.sent == []  # but nothing was actually sent


def test_resend_is_throttled_60s_per_email(client, db, monkeypatch):
    sender = _install_recording_sender(monkeypatch)
    email = "throttle-resend@x.com"
    get_redis().delete(f"verify_resend_cooldown:{email}")
    create_user(db, email=email, status=UserStatus.pending_verification)

    first = client.post("/api/v1/auth/verify/resend", json={"email": email})
    assert first.status_code == 200
    assert len(sender.sent) == 1

    second = client.post("/api/v1/auth/verify/resend", json={"email": email})
    assert second.status_code == 200
    assert second.json() == first.json()  # byte-identical generic response
    assert len(sender.sent) == 1  # no second send — still throttled

    get_redis().delete(f"verify_resend_cooldown:{email}")
