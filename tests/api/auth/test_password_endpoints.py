from datetime import timedelta

from app.core.security import get_password_hash, verify_password
from app.db.models.auth import AuthSession, AuthToken
from app.db.models.enums import AuthTokenPurpose, UserStatus
from app.services.auth.sessions import issue_token_pair
from app.services.auth.tokens import issue_auth_token
from tests.factories import create_user


def test_forgot_is_generic_for_unknown(client):
    r = client.post("/api/v1/auth/password/forgot", json={"email": "ghost@x.com"})
    assert r.status_code == 200
    assert r.json()["data"]["sent"] is True


def test_forgot_disabled_account_is_generic_and_issues_no_token(client, db):
    u = create_user(db, email="disabled@x.com", status=UserStatus.disabled)
    db.flush()

    r = client.post("/api/v1/auth/password/forgot", json={"email": "disabled@x.com"})

    # Same generic response as the unknown-email case -- no enumeration.
    assert r.status_code == 200
    assert r.json()["data"] == {
        "sent": True,
        "message": "If that email has an account, a reset link is on its way.",
    }
    # But no reset token was actually issued for the disabled account.
    tokens = (
        db.query(AuthToken)
        .filter(AuthToken.user_id == u.id, AuthToken.purpose == AuthTokenPurpose.password_reset)
        .all()
    )
    assert tokens == []


def test_reset_updates_password_and_revokes_sessions(client, db):
    u = create_user(
        db,
        email="r@x.com",
        password_hash=get_password_hash("password1"),
        status=UserStatus.active,
    )
    issue_token_pair(db, u)  # an active session
    raw = issue_auth_token(db, u, AuthTokenPurpose.password_reset, timedelta(hours=1))
    db.flush()
    r = client.post("/api/v1/auth/password/reset", json={"token": raw, "password": "newpass123"})
    assert r.status_code == 200, r.text
    db.refresh(u)
    assert verify_password("newpass123", u.password_hash)
    sessions = db.query(AuthSession).filter(AuthSession.user_id == u.id).all()
    assert all(s.revoked_at is not None for s in sessions)


def test_reset_weak_password_rejected(client, db):
    u = create_user(db, email="r2@x.com", status=UserStatus.active)
    raw = issue_auth_token(db, u, AuthTokenPurpose.password_reset, timedelta(hours=1))
    db.flush()
    r = client.post("/api/v1/auth/password/reset", json={"token": raw, "password": "weak"})
    assert r.status_code == 422
