from datetime import UTC, datetime

import pyotp
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.security import create_access_token, get_password_hash
from app.db.models.enums import MfaType, UserStatus
from app.services.auth import mfa
from tests.factories import create_user


@pytest.fixture(autouse=True)
def _mfa_key(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _auth_headers(user):
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def test_totp_setup_then_verify_enables(client, db):
    u = create_user(
        db, email="t@x.com", status=UserStatus.active, password_hash=get_password_hash("password1")
    )
    db.flush()
    r = client.post("/api/v1/auth/mfa/totp/setup", headers=_auth_headers(u))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["secret"] and data["otpauth_uri"].startswith("otpauth://")
    code = pyotp.TOTP(data["secret"]).now()
    r2 = client.post("/api/v1/auth/mfa/totp/verify", json={"code": code}, headers=_auth_headers(u))
    assert r2.status_code == 200
    assert len(r2.json()["data"]["backup_codes"]) == 10
    db.refresh(u)
    assert u.mfa_type == MfaType.totp and u.mfa_secret and u.mfa_enabled_at


def test_challenge_with_totp_issues_tokens(client, db):
    secret = mfa.generate_totp_secret()
    u = create_user(
        db,
        email="c@x.com",
        status=UserStatus.active,
        mfa_type=MfaType.totp,
        mfa_secret=mfa.encrypt_secret(secret),
        mfa_enabled_at=datetime.now(UTC),
        password_hash=get_password_hash("password1"),
    )
    db.flush()
    ticket = mfa.issue_mfa_ticket(u.id)
    code = pyotp.TOTP(secret).now()
    r = client.post("/api/v1/auth/mfa/challenge", json={"mfa_ticket": ticket, "code": code})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["access_token"]


def test_challenge_bad_code_401(client, db):
    secret = mfa.generate_totp_secret()
    u = create_user(
        db,
        email="c2@x.com",
        status=UserStatus.active,
        mfa_type=MfaType.totp,
        mfa_secret=mfa.encrypt_secret(secret),
        mfa_enabled_at=datetime.now(UTC),
    )
    db.flush()
    ticket = mfa.issue_mfa_ticket(u.id)
    r = client.post("/api/v1/auth/mfa/challenge", json={"mfa_ticket": ticket, "code": "000000"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "MFA_INVALID_CODE"
