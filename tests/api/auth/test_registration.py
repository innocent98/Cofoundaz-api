from datetime import timedelta

from app.db.models.enums import AuthTokenPurpose, UserStatus
from app.db.models.user import User
from app.services.auth.tokens import issue_auth_token
from tests.factories import create_user


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
