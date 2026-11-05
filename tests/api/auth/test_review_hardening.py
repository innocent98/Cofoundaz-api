"""Opus-review fixes on the sessions, lockout and MFA paths: reuse revocation really commits,
the cookie CSRF guard cannot be dodged with a decoy body token, attempts are reserved atomically,
known addresses are never locked out by a spread-out guess, IPv6 counters key on the /64, and a
few small races and oracles."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.api.deps import Unauthorized
from app.core.config import settings
from app.core.errors import AccountLocked
from app.core.redis import get_redis
from app.db.models.auth import AuthSession
from app.main import app
from app.services.auth import mfa, ratelimit
from app.services.auth.sessions import issue_token_pair, rotate_refresh
from tests.auth_helpers import PASSWORD, active_user

LOGIN = "/api/v1/auth/login"
COOKIE_MODE = {"X-Auth-Mode": "cookie"}


def _from_ip(ip):
    return TestClient(app, client=(ip, 50000))


def _login(client, email, **kw):
    return client.post(LOGIN, json={"email": email, "password": PASSWORD}, **kw)


# 1. refresh-token reuse really revokes the family


def test_reuse_revocation_is_committed_not_rolled_back_with_the_error(db):
    user = active_user(db, "reuse@x.com")
    _a, first = issue_token_pair(db, user)
    db.commit()
    _a2, second, _u = rotate_refresh(db, first)  # the thief or the owner rotates once
    db.commit()
    with pytest.raises(Unauthorized):
        rotate_refresh(db, first)  # the old token is presented again: reuse
    db.rollback()  # what a failed request does to the session
    live = db.query(AuthSession).filter_by(user_id=user.id, revoked_at=None).count()
    assert live == 0  # the whole family stayed revoked after the rollback


def test_after_a_reuse_the_newest_token_of_the_family_is_dead(client, db):
    active_user(db, "reuse2@x.com")
    db.flush()
    r0 = _login(client, "reuse2@x.com").json()["data"]["refresh_token"]
    client.cookies.clear()
    r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": r0}).json()["data"]
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": r0})
    assert resp.status_code == 401
    newest = client.post("/api/v1/auth/refresh", json={"refresh_token": r1["refresh_token"]})
    assert newest.status_code == 401


# 2. a decoy body token cannot dodge the CSRF guard


def _cookie_only(client, db, email):
    active_user(db, email)
    db.flush()
    refresh = _login(client, email).json()["data"]["refresh_token"]
    client.cookies.clear()
    client.cookies.set(settings.REFRESH_COOKIE_NAME, refresh, path="/api/v1/auth")
    return refresh


def test_cookie_plus_junk_body_token_cannot_rotate_the_cookie(client, db):
    refresh = _cookie_only(client, db, "decoy@x.com")
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": "junk"})
    assert r.status_code == 401  # the body token is the one used; it is junk
    assert "set-cookie" not in r.headers  # the cookie was not rotated or touched
    ok = client.post("/api/v1/auth/refresh", headers=COOKIE_MODE)
    assert ok.status_code == 200  # the real cookie token is still alive
    assert refresh != ok.headers["set-cookie"]


def test_cookie_plus_junk_body_token_cannot_force_a_logout(client, db):
    _cookie_only(client, db, "decoy2@x.com")
    r = client.post("/api/v1/auth/logout", json={"refresh_token": "junk"})
    assert r.status_code == 200
    assert "set-cookie" not in r.headers  # the decoy does not clear the browser's cookie
    ok = client.post("/api/v1/auth/refresh", headers=COOKIE_MODE)
    assert ok.status_code == 200  # nothing was revoked


def test_a_valid_body_token_logout_still_clears_the_cookie(client, db):
    active_user(db, "legit@x.com")
    db.flush()
    refresh = _login(client, "legit@x.com").json()["data"]["refresh_token"]
    r = client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    assert r.status_code == 200 and "max-age=0" in r.headers["set-cookie"].lower()


# 3. attempts are reserved atomically


def test_login_attempts_are_reserved_before_the_check(db):
    """Nothing records a failure here: the reservation alone must stop attempt MAX+1."""
    refused = 0
    for _ in range(settings.LOGIN_MAX_FAILS + 3):
        try:
            ratelimit.reserve_login_attempt("race@x.com", "203.0.113.1")
        except AccountLocked:
            refused += 1
    assert refused == 3


def test_a_parallel_burst_gets_no_more_than_the_limit(db):
    allowed = []
    lock = threading.Lock()

    def attempt():
        try:
            ratelimit.reserve_login_attempt("burst@x.com", "203.0.113.2")
        except AccountLocked:
            return
        with lock:
            allowed.append(1)

    with ThreadPoolExecutor(max_workers=16) as pool:
        for f in [pool.submit(attempt) for _ in range(40)]:
            f.result()
    assert len(allowed) == settings.LOGIN_MAX_FAILS


def test_mfa_attempts_are_reserved_before_the_check(db):
    refused = 0
    for _ in range(settings.MFA_USER_MAX_FAILS + 2):
        try:
            ratelimit.reserve_mfa_attempt("user-1")
        except AccountLocked:
            refused += 1
    assert refused == 2


# 4. known addresses, IPv6, mail caps


def test_a_known_address_is_not_locked_out_by_a_spread_out_guess(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_EMAIL_MAX_FAILS", 6)
    active_user(db, "known@x.com")
    db.flush()
    first = _login(_from_ip("198.51.100.9"), "known@x.com")  # the owner's address
    assert first.status_code == 200
    for i in range(8):  # strangers from eight addresses
        _from_ip(f"192.0.2.{i + 1}").post(
            LOGIN, json={"email": "known@x.com", "password": "wrong-pass-1"}
        )
    stranger = _login(_from_ip("192.0.2.99"), "known@x.com")
    assert stranger.status_code == 429  # an unknown address meets the email-wide ceiling
    owner = _login(_from_ip("198.51.100.9"), "known@x.com")
    assert owner.status_code == 200, owner.text  # the owner's own address does not


def test_ipv6_counters_key_on_the_slash_64(client, db):
    active_user(db, "v6@x.com")
    db.flush()
    a = _from_ip("2001:db8:1:1::1")
    for _ in range(settings.LOGIN_MAX_FAILS):
        a.post(LOGIN, json={"email": "v6@x.com", "password": "wrong-pass-1"})
    same_block = _login(_from_ip("2001:db8:1:1:ffff::9"), "v6@x.com")
    assert same_block.status_code == 429  # another address of the same /64: same counter
    other_block = _login(_from_ip("2001:db8:1:2::1"), "v6@x.com")
    assert other_block.status_code == 200


def test_ip_bucket_collapses_ipv6_only():
    assert ratelimit.ip_bucket("203.0.113.5") == "203.0.113.5"
    assert ratelimit.ip_bucket("2001:db8:1:1::1") == ratelimit.ip_bucket("2001:db8:1:1::2")
    assert ratelimit.ip_bucket("2001:db8:1:1::1") != ratelimit.ip_bucket("2001:db8:1:2::1")
    assert ratelimit.ip_bucket(None) is None


def test_one_caller_cannot_use_up_the_owners_reset_mail_share(db, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_FORGOT_PER_EMAIL_IP_HOURLY", 2)
    monkeypatch.setattr(settings, "AUTH_FORGOT_PER_EMAIL_HOURLY", 4)

    def blocked(ip):
        return ratelimit.mail_blocked_for("forgot", "victim@x.com", ip, pair_limit=2, email_limit=4)

    results = [blocked("203.0.113.50") for _ in range(10)]  # a stranger hammers it
    assert results[:2] == [None, None] and all(r is not None for r in results[2:])
    # the stranger's refused calls did not count overall: the owner and two more callers still fit
    assert blocked("198.51.100.1") is None
    assert blocked("198.51.100.1") is None
    assert blocked("198.51.100.2") is not None  # the overall cap of 4 is now reached


# 6. a counter that lost its expiry repairs itself


def test_a_counter_without_an_expiry_gets_one(db):
    redis = get_redis()
    key = "rl:test_scope:" + ratelimit._h("x")
    redis.set(key, 3)  # as if a worker died between INCR and EXPIRE
    assert redis.ttl(key) == -1
    count, ttl = ratelimit._hit("test_scope", "x", 60)
    assert count == 4 and 0 < ttl <= 60
    assert 0 < redis.ttl(key) <= 60


# 7. a backup code is spent once


def test_a_backup_code_can_be_spent_only_once(db, monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    user = active_user(db, "backup@x.com")
    codes = mfa.generate_backup_codes(db, user)
    assert mfa.consume_backup_code(db, user, codes[0]) is True
    assert mfa.consume_backup_code(db, user, codes[0]) is False
    assert mfa.consume_backup_code(db, user, "0000000000") is False


# 8. unknown email and password-less accounts pay for a bcrypt check too


def test_every_failed_login_runs_one_password_check(client, db, monkeypatch):
    from app.api.v1.endpoints.auth import login as login_module

    calls = []
    real = login_module.verify_password

    def spy(plain, hashed):
        calls.append(hashed)
        return real(plain, hashed)

    monkeypatch.setattr(login_module, "verify_password", spy)
    active_user(db, "has-pw@x.com")
    active_user(db, "google-only@x.com").password_hash = None
    db.flush()
    for email in ("has-pw@x.com", "ghost@x.com", "google-only@x.com"):
        r = client.post(LOGIN, json={"email": email, "password": "wrong-pass-1"})
        assert r.status_code == 401
    assert len(calls) == 3 and all(c.startswith("$2") for c in calls)


# 9. onboarding invites cannot offer the investor role


def test_the_investor_role_cannot_be_invited_from_onboarding(client, db):
    from tests.auth_helpers import bearer

    user = active_user(db, "inviter@x.com")
    db.commit()
    client.get("/api/v1/onboarding/state", headers=bearer(user))
    r = client.post(
        "/api/v1/onboarding/invites",
        headers=bearer(user),
        json={"invites": [{"email": "inv@x.com", "role": "investor"}]},
    )
    assert r.status_code == 422
