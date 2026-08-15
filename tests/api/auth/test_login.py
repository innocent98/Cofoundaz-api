from datetime import UTC, datetime

from app.core.security import get_password_hash
from app.db.models.enums import MfaType, UserStatus
from tests.factories import create_user


def _active(db, email="l@x.com", pw="password1", **kw):
    return create_user(
        db,
        email=email,
        password_hash=get_password_hash(pw),
        status=UserStatus.active,
        email_verified_at=datetime.now(UTC),
        **kw,
    )


def test_login_success_returns_tokens(client, db):
    _active(db)
    db.flush()
    r = client.post("/api/v1/auth/login", json={"email": "l@x.com", "password": "password1"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["access_token"] and body["refresh_token"]
    assert body.get("mfa_required") in (False, None)
    assert client.cookies.get("cfz_refresh")  # cookie set


def test_login_wrong_password_401_and_increments(client, db):
    u = _active(db, email="w@x.com")
    db.flush()
    r = client.post("/api/v1/auth/login", json={"email": "w@x.com", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    db.refresh(u)
    assert u.failed_login_count == 1


def test_login_lockout_after_5(client, db):
    _active(db, email="lock@x.com")
    db.flush()
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "lock@x.com", "password": "bad"})
    r = client.post("/api/v1/auth/login", json={"email": "lock@x.com", "password": "password1"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "ACCOUNT_LOCKED"


def test_login_mfa_required_returns_ticket_not_tokens(client, db):
    _active(db, email="m@x.com", mfa_type=MfaType.totp)
    db.flush()
    r = client.post("/api/v1/auth/login", json={"email": "m@x.com", "password": "password1"})
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["mfa_required"] is True and body.get("mfa_ticket")
    assert body.get("access_token") is None


def test_login_unknown_email_401_no_enumeration(client, db):
    r = client.post("/api/v1/auth/login", json={"email": "nobody@x.com", "password": "whatever"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_disabled_user_401(client, db):
    create_user(
        db,
        email="d@x.com",
        password_hash=get_password_hash("password1"),
        status=UserStatus.disabled,
        email_verified_at=datetime.now(UTC),
    )
    db.flush()
    r = client.post("/api/v1/auth/login", json={"email": "d@x.com", "password": "password1"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"
