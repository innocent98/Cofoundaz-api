"""Live end-to-end journeys against the running server with persisted real data.

Each test drives the real HTTP surface (httpx), reads one-time tokens back from
the file email backend, and computes real TOTP codes. Covers the full happy path
plus the security-critical negatives.
"""

import uuid

import httpx
import pyotp


def _pw() -> str:
    # Generated per-use (policy: >=8 chars, one digit); never a hardcoded literal.
    return f"Ev-{uuid.uuid4().hex[:12]}-7"


def test_signup_verify_login_me(http: httpx.Client, make_verified_user):
    u = make_verified_user(http)

    r = http.post("/api/v1/auth/login", json=u)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["access_token"] and data["refresh_token"]
    assert data.get("mfa_required") in (False, None)
    assert http.cookies.get("cfz_refresh"), "refresh cookie must be set on login"

    me = http.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.status_code == 200, me.text
    body = me.json()["data"]
    assert body["user"]["email"].lower() == u["email"].lower()
    # No workspace yet (onboarding is Plan 3): empty memberships, null active workspace.
    assert body["memberships"] == []
    assert body["active_workspace_id"] is None


def test_mfa_enable_then_challenge_login(http: httpx.Client, make_verified_user):
    u = make_verified_user(http)
    login = http.post("/api/v1/auth/login", json=u).json()["data"]
    auth = {"Authorization": f"Bearer {login['access_token']}"}

    setup = http.post("/api/v1/auth/mfa/totp/setup", headers=auth)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["data"]["secret"]
    assert setup.json()["data"]["otpauth_uri"].startswith("otpauth://")

    verify = http.post(
        "/api/v1/auth/mfa/totp/verify", json={"code": pyotp.TOTP(secret).now()}, headers=auth
    )
    assert verify.status_code == 200, verify.text
    assert len(verify.json()["data"]["backup_codes"]) == 10

    assert http.post("/api/v1/auth/logout").status_code == 200

    # Re-login now demands MFA: ticket, no tokens.
    relogin = http.post("/api/v1/auth/login", json=u)
    assert relogin.status_code == 200
    gate = relogin.json()["data"]
    assert gate["mfa_required"] is True and gate.get("mfa_ticket")
    assert gate.get("access_token") is None

    chal = http.post(
        "/api/v1/auth/mfa/challenge",
        json={"mfa_ticket": gate["mfa_ticket"], "code": pyotp.TOTP(secret).now()},
    )
    assert chal.status_code == 200, chal.text
    assert chal.json()["data"]["access_token"]


def test_refresh_rotation_and_reuse_revokes_family(http: httpx.Client, make_verified_user):
    u = make_verified_user(http)
    r0 = http.post("/api/v1/auth/login", json=u).json()["data"]["refresh_token"]

    # Use body transport only; clear cookies so the cookie copy doesn't mask reuse.
    http.cookies.clear()
    rot = http.post("/api/v1/auth/refresh", json={"refresh_token": r0})
    assert rot.status_code == 200, rot.text
    assert rot.json()["data"]["refresh_token"] != r0

    http.cookies.clear()
    reuse = http.post("/api/v1/auth/refresh", json={"refresh_token": r0})
    assert reuse.status_code == 401, "reused (rotated) refresh token must be rejected"


def test_cookie_only_refresh(http: httpx.Client, make_verified_user):
    u = make_verified_user(http)
    login = http.post("/api/v1/auth/login", json=u)
    assert login.status_code == 200
    # No body at all — relies purely on the httpOnly cookie set by login.
    r = http.post("/api/v1/auth/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["access_token"]


def test_forgot_reset_then_login_with_new_password(http: httpx.Client, make_verified_user, mailbox):
    u = make_verified_user(http)
    new_pw = _pw()

    forgot = http.post("/api/v1/auth/password/forgot", json={"email": u["email"]})
    assert forgot.status_code == 200
    token = mailbox.latest_token_for(u["email"], subject_contains="Reset")

    reset = http.post("/api/v1/auth/password/reset", json={"token": token, "password": new_pw})
    assert reset.status_code == 200, reset.text

    # Old password rejected; new password accepted.
    old = http.post("/api/v1/auth/login", json=u)
    assert old.status_code == 401
    assert old.json()["error"]["code"] == "INVALID_CREDENTIALS"

    fresh = http.post("/api/v1/auth/login", json={"email": u["email"], "password": new_pw})
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["data"]["access_token"]


def test_password_reset_revokes_existing_sessions(http: httpx.Client, make_verified_user, mailbox):
    u = make_verified_user(http)
    r0 = http.post("/api/v1/auth/login", json=u).json()["data"]["refresh_token"]

    forgot = http.post("/api/v1/auth/password/forgot", json={"email": u["email"]})
    assert forgot.status_code == 200
    token = mailbox.latest_token_for(u["email"], subject_contains="Reset")
    assert (
        http.post(
            "/api/v1/auth/password/reset", json={"token": token, "password": _pw()}
        ).status_code
        == 200
    )

    # The pre-reset refresh token must no longer rotate.
    http.cookies.clear()
    assert http.post("/api/v1/auth/refresh", json={"refresh_token": r0}).status_code == 401


# --- security negatives ---


def test_duplicate_email_409(http: httpx.Client, unique_email):
    email, pw = unique_email(), _pw()
    assert (
        http.post("/api/v1/auth/signup", json={"email": email, "password": pw}).status_code == 201
    )
    dup = http.post("/api/v1/auth/signup", json={"email": email, "password": pw})
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "EMAIL_TAKEN"


def test_weak_password_422(http: httpx.Client, unique_email):
    r = http.post("/api/v1/auth/signup", json={"email": unique_email(), "password": "short"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "WEAK_PASSWORD"


def test_login_lockout_after_5_fails(http: httpx.Client, make_verified_user):
    u = make_verified_user(http)
    for _ in range(5):
        bad = http.post("/api/v1/auth/login", json={"email": u["email"], "password": "wrong-9x"})
        assert bad.status_code == 401
    locked = http.post("/api/v1/auth/login", json=u)  # correct password, but locked
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "ACCOUNT_LOCKED"


def test_resend_is_throttled_within_60s(
    http: httpx.Client, make_verified_user, unique_email, mailbox
):
    # Fresh pending user (signup sends email #1).
    email, pw = unique_email(), _pw()
    assert (
        http.post("/api/v1/auth/signup", json={"email": email, "password": pw}).status_code == 201
    )

    http.post("/api/v1/auth/verify/resend", json={"email": email})
    after_first = mailbox.count_for(email)
    # Second resend inside the 60s window must NOT send another email.
    http.post("/api/v1/auth/verify/resend", json={"email": email})
    assert mailbox.count_for(email) == after_first, "resend throttle should suppress the 2nd email"


def test_forgot_is_generic_for_unknown_email(http: httpx.Client, unique_email):
    r = http.post("/api/v1/auth/password/forgot", json={"email": unique_email()})
    assert r.status_code == 200
    assert r.json()["data"]["sent"] is True
