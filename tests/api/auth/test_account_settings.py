"""Settings → Profile / Security: profile edit, avatar, password change, 2FA management, sessions."""

from datetime import UTC, datetime

import pyotp
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.redis import get_redis
from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.models.audit import AuditLog
from app.db.models.auth import AuthSession, MfaBackupCode
from app.db.models.enums import MfaType, UserStatus
from app.services.auth import mfa
from app.services.auth.sessions import issue_token_pair
from tests.factories import create_auth_session, create_user

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _user(db, email="acct@x.com", **kw):
    u = create_user(
        db,
        email=email,
        status=UserStatus.active,
        password_hash=get_password_hash("password1"),
        email_verified_at=datetime.now(UTC),
        **kw,
    )
    db.flush()
    get_redis().delete(f"password_change_fail:{u.id}", f"mfa_manage_fail:{u.id}")
    return u


def _h(u):
    return {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


# ---- profile -------------------------------------------------------------------------------


def test_patch_me_updates_every_profile_field(client, db):
    u = _user(db)
    r = client.patch(
        "/api/v1/auth/me",
        json={
            "full_name": " Ada  Obi ",
            "role_title": "CEO",
            "country": "ng",
            "phone": "+234 801 234 5678",
        },
        headers=_h(u),
    )
    assert r.status_code == 200, r.text
    p = r.json()["data"]["profile"]
    assert p == {
        "full_name": "Ada Obi",
        "role_title": "CEO",
        "country": "NG",
        "avatar_url": None,
        "phone": "+234 801 234 5678",
    }
    assert db.query(AuditLog).filter_by(action="auth.profile.updated").count() == 1


def test_patch_me_clears_country_and_phone_with_null(client, db):
    u = _user(db)
    client.patch("/api/v1/auth/me", json={"country": "NG", "phone": "08012345678"}, headers=_h(u))
    r = client.patch("/api/v1/auth/me", json={"country": None, "phone": None}, headers=_h(u))
    assert r.status_code == 200
    assert r.json()["data"]["profile"]["country"] is None
    assert r.json()["data"]["profile"]["phone"] is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"nickname": "x"},
        {"full_name": None},
        {"full_name": "   "},
        {"full_name": "a" * 121},
        {"full_name": "bad\x00name"},
        {"role_title": None},
        {"role_title": " "},
        {"role_title": "r" * 81},
        {"role_title": "x\x07"},
        {"country": "NGA"},
        {"phone": "abc"},
    ],
)
def test_patch_me_rejects_bad_input(client, db, body):
    u = _user(db)
    r = client.patch("/api/v1/auth/me", json=body, headers=_h(u))
    assert r.status_code == 422, r.text


def test_avatar_upload_replace_and_remove(client, db):
    u = _user(db)
    r = client.post(
        "/api/v1/auth/me/avatar", files={"file": ("a.png", PNG, "image/png")}, headers=_h(u)
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["profile"]["avatar_url"]
    r = client.delete("/api/v1/auth/me/avatar", headers=_h(u))
    assert r.status_code == 200
    assert r.json()["data"]["profile"]["avatar_url"] is None


def test_avatar_rejects_wrong_type_fake_bytes_and_size(client, db):
    u = _user(db)
    h = _h(u)
    bad_type = client.post(
        "/api/v1/auth/me/avatar", files={"file": ("a.svg", b"<svg/>", "image/svg+xml")}, headers=h
    )
    assert bad_type.status_code == 422
    fake = client.post(
        "/api/v1/auth/me/avatar", files={"file": ("a.png", b"<html>", "image/png")}, headers=h
    )
    assert fake.status_code == 422
    big = client.post(
        "/api/v1/auth/me/avatar",
        files={"file": ("a.png", PNG + b"0" * (2 * 1024 * 1024), "image/png")},
        headers=h,
    )
    assert big.status_code == 422


# ---- password change -----------------------------------------------------------------------


def _signed_in(db, u):
    _, raw = issue_token_pair(db, u)
    db.flush()
    return raw


def test_change_password_keeps_this_device_and_signs_out_others(client, db):
    u = _user(db)
    mine = _signed_in(db, u)
    _signed_in(db, u)
    r = client.post(
        "/api/v1/auth/password/change",
        json={"current_password": "password1", "new_password": "newpass22", "refresh_token": mine},
        headers=_h(u),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"changed": True, "other_sessions_signed_out": 1}
    db.refresh(u)
    assert verify_password("newpass22", u.password_hash)
    live = db.query(AuthSession).filter_by(user_id=u.id, revoked_at=None).count()
    assert live == 1
    assert db.query(AuditLog).filter_by(action="auth.password.changed").count() == 1


def test_change_password_without_known_device_signs_out_everywhere(client, db):
    u = _user(db)
    _signed_in(db, u)
    r = client.post(
        "/api/v1/auth/password/change",
        json={"current_password": "password1", "new_password": "newpass22"},
        headers=_h(u),
    )
    assert r.status_code == 200
    assert r.json()["data"]["other_sessions_signed_out"] == 1


def test_change_password_rejections(client, db):
    u = _user(db)
    h = _h(u)
    wrong = client.post(
        "/api/v1/auth/password/change",
        json={"current_password": "nope", "new_password": "newpass22"},
        headers=h,
    )
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "CURRENT_PASSWORD_WRONG"
    weak = client.post(
        "/api/v1/auth/password/change",
        json={"current_password": "password1", "new_password": "short"},
        headers=h,
    )
    assert weak.status_code == 422 and weak.json()["error"]["code"] == "WEAK_PASSWORD"
    same = client.post(
        "/api/v1/auth/password/change",
        json={"current_password": "password1", "new_password": "password1"},
        headers=h,
    )
    assert same.status_code == 422 and same.json()["error"]["code"] == "PASSWORD_UNCHANGED"
    assert client.post("/api/v1/auth/password/change", json={}).status_code in (401, 422)


def test_change_password_locks_after_five_wrong_guesses(client, db):
    u = _user(db, email="lock@x.com")
    h = _h(u)
    body = {"current_password": "nope", "new_password": "newpass22"}
    for _ in range(5):
        assert client.post("/api/v1/auth/password/change", json=body, headers=h).status_code == 422
    r = client.post(
        "/api/v1/auth/password/change",
        json={"current_password": "password1", "new_password": "newpass22"},
        headers=h,
    )
    assert r.status_code == 429 and r.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"


# ---- 2FA management ------------------------------------------------------------------------


def _with_mfa(db, email="mfa@x.com"):
    secret = mfa.generate_totp_secret()
    u = _user(
        db,
        email=email,
        mfa_type=MfaType.totp,
        mfa_secret=mfa.encrypt_secret(secret),
        mfa_enabled_at=datetime.now(UTC),
    )
    mfa.generate_backup_codes(db, u)
    db.flush()
    return u, secret


def test_mfa_status_off_and_on(client, db):
    off = _user(db)
    assert client.get("/api/v1/auth/mfa", headers=_h(off)).json()["data"] == {
        "enabled": False,
        "method": None,
        "enabled_at": None,
        "backup_codes_remaining": 0,
    }
    u, _ = _with_mfa(db)
    d = client.get("/api/v1/auth/mfa", headers=_h(u)).json()["data"]
    assert d["enabled"] is True and d["method"] == "totp" and d["backup_codes_remaining"] == 10


def test_mfa_disable_with_totp_code(client, db):
    u, secret = _with_mfa(db)
    r = client.post(
        "/api/v1/auth/mfa/disable",
        json={"password": "password1", "code": pyotp.TOTP(secret).now()},
        headers=_h(u),
    )
    assert r.status_code == 200 and r.json()["data"] == {"enabled": False}
    db.refresh(u)
    assert u.mfa_type == MfaType.none and u.mfa_secret is None
    assert db.query(MfaBackupCode).filter_by(user_id=u.id).count() == 0
    assert db.query(AuditLog).filter_by(action="auth.mfa.disabled").count() == 1


def test_mfa_disable_with_backup_code(client, db):
    u, _ = _with_mfa(db, "mfa2@x.com")
    codes = mfa.generate_backup_codes(db, u)
    r = client.post(
        "/api/v1/auth/mfa/disable",
        json={"password": "password1", "code": codes[0]},
        headers=_h(u),
    )
    assert r.status_code == 200


def test_mfa_disable_rejections(client, db):
    off = _user(db, "off@x.com")
    r = client.post(
        "/api/v1/auth/mfa/disable", json={"password": "password1", "code": "1"}, headers=_h(off)
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "MFA_NOT_ENABLED"
    u, _ = _with_mfa(db, "mfa3@x.com")
    bad_pw = client.post(
        "/api/v1/auth/mfa/disable", json={"password": "wrong", "code": "000000"}, headers=_h(u)
    )
    assert bad_pw.status_code == 422
    bad_code = client.post(
        "/api/v1/auth/mfa/disable", json={"password": "password1", "code": "000000"}, headers=_h(u)
    )
    assert bad_code.status_code == 401 and bad_code.json()["error"]["code"] == "MFA_INVALID_CODE"
    db.refresh(u)
    assert u.mfa_type == MfaType.totp


def test_mfa_manage_locks_after_five_wrong_passwords(client, db):
    u, _ = _with_mfa(db, "mfa4@x.com")
    for _ in range(5):
        client.post(
            "/api/v1/auth/mfa/backup-codes/regenerate", json={"password": "x"}, headers=_h(u)
        )
    r = client.post(
        "/api/v1/auth/mfa/backup-codes/regenerate", json={"password": "password1"}, headers=_h(u)
    )
    assert r.status_code == 429


def test_regenerate_backup_codes_replaces_the_old_set(client, db):
    u, _ = _with_mfa(db, "mfa5@x.com")
    before = {c.code_hash for c in db.query(MfaBackupCode).filter_by(user_id=u.id)}
    r = client.post(
        "/api/v1/auth/mfa/backup-codes/regenerate", json={"password": "password1"}, headers=_h(u)
    )
    assert r.status_code == 200 and len(r.json()["data"]["backup_codes"]) == 10
    after = {c.code_hash for c in db.query(MfaBackupCode).filter_by(user_id=u.id)}
    assert len(after) == 10 and not (before & after)
    off = _user(db, "off2@x.com")
    r = client.post(
        "/api/v1/auth/mfa/backup-codes/regenerate", json={"password": "password1"}, headers=_h(off)
    )
    assert r.status_code == 409


# ---- sessions ------------------------------------------------------------------------------


def test_sessions_list_marks_current_and_hides_rotated_and_revoked(client, db):
    u = _user(db)
    mine = _signed_in(db, u)
    other = create_auth_session(
        db,
        u,
        refresh_token_hash="o1",
        user_agent="Mozilla/5.0 (Macintosh; Mac OS X) Chrome/120 Safari/537",
    )
    create_auth_session(db, u, refresh_token_hash="o2", revoked_at=datetime.now(UTC))
    create_auth_session(db, u, refresh_token_hash="o3", rotated_at=datetime.now(UTC))
    create_auth_session(db, u, refresh_token_hash="o4", user_agent="curl/8")
    client.cookies.set(settings.REFRESH_COOKIE_NAME, mine)
    rows = client.get("/api/v1/auth/sessions", headers=_h(u)).json()["data"]
    client.cookies.clear()
    assert len(rows) == 3
    assert sum(r["current"] for r in rows) == 1
    labels = {r["id"]: r["label"] for r in rows}
    assert labels[str(other.family_id)] == "Chrome on Mac"
    assert "Browser on unknown device" in labels.values()


def test_revoke_one_session_and_unknown_is_404(client, db):
    u = _user(db)
    s = create_auth_session(db, u, refresh_token_hash="x1")
    r = client.delete(f"/api/v1/auth/sessions/{s.family_id}", headers=_h(u))
    assert r.status_code == 200
    db.refresh(s)
    assert s.revoked_at is not None
    again = client.delete(f"/api/v1/auth/sessions/{s.family_id}", headers=_h(u))
    assert again.status_code == 404


def test_cannot_revoke_someone_elses_session(client, db):
    a, b = _user(db, "a@x.com"), _user(db, "b@x.com")
    s = create_auth_session(db, b, refresh_token_hash="bx")
    r = client.delete(f"/api/v1/auth/sessions/{s.family_id}", headers=_h(a))
    assert r.status_code == 404
    db.refresh(s)
    assert s.revoked_at is None


def test_revoke_others_needs_to_know_the_current_one(client, db):
    u = _user(db)
    create_auth_session(db, u, refresh_token_hash="y1")
    r = client.post("/api/v1/auth/sessions/revoke-others", headers=_h(u))
    assert r.status_code == 409 and r.json()["error"]["code"] == "CURRENT_SESSION_UNKNOWN"
    mine = _signed_in(db, u)
    ok = client.post(
        "/api/v1/auth/sessions/revoke-others", json={"refresh_token": mine}, headers=_h(u)
    )
    assert ok.status_code == 200 and ok.json()["data"]["revoked"] == 1
    assert db.query(AuthSession).filter_by(user_id=u.id, revoked_at=None).count() == 1
