"""Gaps 1, 2 and 3 (D-AUTH-2): lockout per (email, IP), the same answer for an unknown email,
minutes in the message, and a password reset that lifts the lock."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.models.enums import AuthTokenPurpose
from app.main import app
from app.services.auth.tokens import issue_auth_token
from tests.auth_helpers import PASSWORD, active_user

LOGIN = "/api/v1/auth/login"


def _fail(client, email, n=1):
    last = None
    for _ in range(n):
        last = client.post(LOGIN, json={"email": email, "password": "wrong-password"})
    return last


def _from_ip(ip: str) -> TestClient:
    """A second client that the API sees as coming from `ip` (shares the test's DB override)."""
    return TestClient(app, client=(ip, 50000))


def test_unknown_email_locks_exactly_like_a_real_one(client, db):
    active_user(db, "real@x.com")
    db.flush()
    real = _fail(client, "real@x.com", settings.LOGIN_MAX_FAILS + 1)
    ghost = _fail(client, "ghost@x.com", settings.LOGIN_MAX_FAILS + 1)
    assert real.status_code == ghost.status_code == 429
    assert real.json()["error"]["code"] == ghost.json()["error"]["code"] == "ACCOUNT_LOCKED"
    assert real.json()["error"]["message"] == ghost.json()["error"]["message"]


def test_before_the_limit_both_answer_401(client, db):
    active_user(db, "real2@x.com")
    db.flush()
    for email in ("real2@x.com", "ghost2@x.com"):
        r = _fail(client, email, settings.LOGIN_MAX_FAILS - 1)
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_locked_body_has_minutes_retry_after_and_header(client, db):
    active_user(db, "mins@x.com")
    db.flush()
    r = _fail(client, "mins@x.com", settings.LOGIN_MAX_FAILS + 1)
    err = r.json()["error"]
    assert err["message"] == "Too many attempts. Try again in 15 minutes or reset your password."
    assert 0 < err["retry_after_seconds"] <= settings.LOGIN_LOCKOUT_MINUTES * 60
    assert r.headers["retry-after"] == str(err["retry_after_seconds"])


def test_the_right_password_is_refused_while_locked(client, db):
    active_user(db, "held@x.com")
    db.flush()
    _fail(client, "held@x.com", settings.LOGIN_MAX_FAILS)
    r = client.post(LOGIN, json={"email": "held@x.com", "password": PASSWORD})
    assert r.status_code == 429


def test_lockout_is_per_ip_so_a_stranger_cannot_lock_the_owner_out(client, db):
    active_user(db, "victim@x.com")
    db.flush()
    attacker = _from_ip("203.0.113.7")
    _fail(attacker, "victim@x.com", settings.LOGIN_MAX_FAILS + 1)
    resp = _fail(attacker, "victim@x.com")
    assert resp.status_code == 429
    owner = _from_ip("198.51.100.20")
    ok = owner.post(LOGIN, json={"email": "victim@x.com", "password": PASSWORD})
    assert ok.status_code == 200, ok.text


def test_a_distributed_guess_hits_the_per_email_ceiling(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_EMAIL_MAX_FAILS", 6)
    active_user(db, "spread@x.com")
    db.flush()
    for i in range(6):  # one failure from each of six addresses: no pair is anywhere near locked
        _fail(_from_ip(f"192.0.2.{i + 1}"), "spread@x.com")
    r = _from_ip("192.0.2.200").post(LOGIN, json={"email": "spread@x.com", "password": PASSWORD})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "ACCOUNT_LOCKED"


def test_a_good_login_resets_the_counter(client, db):
    active_user(db, "reset-count@x.com")
    db.flush()
    _fail(client, "reset-count@x.com", settings.LOGIN_MAX_FAILS - 1)
    assert (
        client.post(LOGIN, json={"email": "reset-count@x.com", "password": PASSWORD}).status_code
        == 200
    )
    # four more wrong passwords do not lock: the earlier four no longer count
    resp = _fail(client, "reset-count@x.com", settings.LOGIN_MAX_FAILS - 1)
    assert resp.status_code == 401


def test_a_suspended_account_counts_failures_like_any_other(client, db):
    from app.db.models.enums import UserStatus

    active_user(
        db,
        "susp@x.com",
    ).status = UserStatus.disabled
    db.flush()
    r = client.post(LOGIN, json={"email": "susp@x.com", "password": PASSWORD})
    assert r.status_code == 401
    for _ in range(settings.LOGIN_MAX_FAILS - 1):
        client.post(LOGIN, json={"email": "susp@x.com", "password": PASSWORD})
    resp = client.post(LOGIN, json={"email": "susp@x.com", "password": PASSWORD})
    assert resp.status_code == 429


@pytest.mark.parametrize("locked_from_other_ip", [False, True])
def test_a_password_reset_lifts_the_lock(client, db, locked_from_other_ip):
    user = active_user(db, f"unlock{int(locked_from_other_ip)}@x.com")
    db.flush()
    locker = _from_ip("203.0.113.55") if locked_from_other_ip else client
    _fail(locker, user.email, settings.LOGIN_MAX_FAILS)
    resp = locker.post(LOGIN, json={"email": user.email, "password": PASSWORD})
    assert resp.status_code == 429

    raw = issue_auth_token(db, user, AuthTokenPurpose.password_reset, timedelta(hours=1))
    db.flush()
    reset = client.post(
        "/api/v1/auth/password/reset", json={"token": raw, "password": "brand-new-pass1"}
    )
    assert reset.status_code == 200, reset.text

    again = locker.post(LOGIN, json={"email": user.email, "password": "brand-new-pass1"})
    assert again.status_code == 200, again.text
