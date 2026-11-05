"""Live journey for the Auth & Onboarding gap fixes (migration 0096).

Runs against the real server over HTTP and writes every request/response it relies on to
`e2e/_captures/auth_onboarding_gaps/*.json` (status, the headers that matter, body). The FE
integration guide `docs/fe-integration-guide-auth-onboarding.md` quotes those files verbatim.
Tokens, tickets, secrets and passwords are replaced by labelled placeholders in the captures.

Two server shapes:
* `scripts/e2e_run.sh` (CI): limits raised, consent not enforced. The tests marked `strict` skip.
* the strict server (real defaults, a few limits lowered): `E2E_AUTH_STRICT=1`. See
  docs/sop/auth-onboarding-gaps.md for the exact environment. Run the whole file there; the
  limit-exhausting tests are last on purpose.

The per-address checks send `X-Forwarded-For`; uvicorn trusts it from 127.0.0.1 only, which is
how a production proxy would present a real client address.
"""

import json
import os
import re
import uuid
from pathlib import Path

import httpx
import pyotp
import pytest

G = "auth_onboarding_gaps"
STRICT = os.environ.get("E2E_AUTH_STRICT") == "1"
strict = pytest.mark.skipif(not STRICT, reason="needs the strict server (E2E_AUTH_STRICT=1)")
COOKIE_MODE = {"X-Auth-Mode": "cookie"}
LOGIN = "/api/v1/auth/login"
_SECRET_KEYS = {
    "access_token": "<access-token>",
    "refresh_token": "<refresh-token>",
    "mfa_ticket": "<mfa-ticket>",
    "token": "<token>",
    "password": "<test-password>",
    "current_password": "<test-password>",
    "new_password": "<test-password>",
    "secret": "<totp-secret>",
    "otpauth_uri": "<otpauth-uri>",
}
_COOKIE_VALUE = re.compile(r"(cfz_refresh=)[^;]*")
_TOKEN_IN_URL = re.compile(r"(/(?:verify-email|reset-password|invite)/)[A-Za-z0-9_\-]+")


def _clean(obj):
    if isinstance(obj, dict):
        return {
            k: (_SECRET_KEYS[k] if k in _SECRET_KEYS and v is not None else _clean(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def _write(name: str, doc: dict) -> None:
    out = Path(__file__).parent / "_captures" / G
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def call(client, method, path, *, name=None, json_body=None, headers=None):
    """Make a real request; when `name` is given, capture request + response."""
    r = client.request(method, path, json=json_body, headers=headers)
    if name:
        shown = {
            k: v
            for k, v in (headers or {}).items()
            if k.lower() in {"x-auth-mode", "origin", "x-forwarded-for"}
        }
        kept = {}
        for h in ("retry-after", "set-cookie"):
            if h in r.headers:
                kept[h] = _COOKIE_VALUE.sub(r"\1<refresh-token>", r.headers[h])
        try:
            body = _clean(r.json())
        except ValueError:
            body = None
        _write(
            name,
            {
                "request": {
                    "method": method,
                    "path": path,
                    **({"headers": shown} if shown else {}),
                    **({"json": _clean(json_body)} if json_body is not None else {}),
                },
                "response": {
                    "status": r.status_code,
                    **({"headers": kept} if kept else {}),
                    "body": body,
                },
            },
        )
    return r


def _bearer(access):
    return {"Authorization": f"Bearer {access}"}


def _pw():
    return f"Ev-{uuid.uuid4().hex[:12]}-5"


def _login_access(client, u, headers=None):
    r = client.post(LOGIN, json=u, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]["access_token"]


@pytest.fixture()
def ip():
    """A distinct client address per test, so one test's counters never touch another's."""
    return {"X-Forwarded-For": f"198.51.100.{uuid.uuid4().int % 250 + 1}"}


# --- gap 14: consent --------------------------------------------------------------------------
@strict
def test_signup_consent(http, unique_email, mailbox, ip):
    email, pw = unique_email("consent"), _pw()
    r = call(
        http,
        "POST",
        "/api/v1/auth/signup",
        name="signup_without_terms_422",
        json_body={"email": email, "password": pw},
        headers=ip,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "TERMS_NOT_ACCEPTED"
    r = call(
        http,
        "POST",
        "/api/v1/auth/signup",
        name="signup_terms_false_422",
        json_body={"email": email, "password": pw, "accepted_terms": False},
        headers=ip,
    )
    assert r.status_code == 422
    r = call(
        http,
        "POST",
        "/api/v1/auth/signup",
        name="signup_with_terms_201",
        json_body={"email": email, "password": pw, "accepted_terms": True},
        headers=ip,
    )
    assert r.status_code == 201, r.text


# --- gaps 1, 2, 3 (+22 password-changed mail): lockout -----------------------------------------
def test_lockout_per_email_and_ip_same_for_unknown_and_reset_lifts_it(
    http, make_verified_user, unique_email, mailbox, ip
):
    ghost = unique_email("ghost")
    for i in range(5):
        r = call(
            http,
            "POST",
            LOGIN,
            name="login_unknown_email_401" if i == 0 else None,
            json_body={"email": ghost, "password": "wrong-pass-1"},
            headers=ip,
        )
        assert r.status_code == 401
    ghost_locked = call(
        http,
        "POST",
        LOGIN,
        name="lockout_unknown_email_429",
        json_body={"email": ghost, "password": "wrong-pass-1"},
        headers=ip,
    )
    assert ghost_locked.status_code == 429

    u = make_verified_user(http)
    for i in range(5):
        r = call(
            http,
            "POST",
            LOGIN,
            name="login_wrong_password_401" if i == 0 else None,
            json_body={"email": u["email"], "password": "wrong-pass-1"},
            headers=ip,
        )
        assert r.status_code == 401
    known_locked = call(
        http,
        "POST",
        LOGIN,
        name="lockout_known_email_429",
        json_body={"email": u["email"], "password": "wrong-pass-1"},
        headers=ip,
    )
    assert known_locked.status_code == 429
    gerr, kerr = ghost_locked.json()["error"], known_locked.json()["error"]
    assert gerr["code"] == kerr["code"] == "ACCOUNT_LOCKED"
    assert gerr["message"] == kerr["message"]  # no enumeration: identical answer
    assert known_locked.headers["retry-after"] == str(kerr["retry_after_seconds"])

    held = call(http, "POST", LOGIN, name="lockout_right_password_429", json_body=u, headers=ip)
    assert held.status_code == 429  # even the right password is refused while locked

    other = {"X-Forwarded-For": "203.0.113.77"}  # a different address: not locked
    ok = call(http, "POST", LOGIN, name="lockout_other_ip_login_200", json_body=u, headers=other)
    assert ok.status_code == 200

    # gap 3: a reset lifts the lock, so the message's own advice works
    new_pw = _pw()
    f = call(
        http,
        "POST",
        "/api/v1/auth/password/forgot",
        json_body={"email": u["email"]},
        headers=other,
    )
    assert f.status_code == 200
    token = mailbox.latest_token_for(u["email"], subject_contains="Reset")
    rr = call(
        http,
        "POST",
        "/api/v1/auth/password/reset",
        name="reset_while_locked_200",
        json_body={"token": token, "password": new_pw},
        headers=other,
    )
    assert rr.status_code == 200
    back = call(
        http,
        "POST",
        LOGIN,
        name="login_after_reset_from_locked_ip_200",
        json_body={"email": u["email"], "password": new_pw},
        headers=ip,
    )
    assert back.status_code == 200, back.text

    mail = mailbox.latest_for(u["email"])
    assert mail["subject"] == "Your Cofoundaz password was changed"
    _write("email_password_changed", {"subject": mail["subject"], "html": mail["html"]})


# --- gap 8: MFA ticket attempts ----------------------------------------------------------------
def _enable_mfa(http, u, ip):
    access = _login_access(http, u, ip)
    setup = http.post("/api/v1/auth/mfa/totp/setup", headers={**_bearer(access), **ip})
    secret = setup.json()["data"]["secret"]
    ver = http.post(
        "/api/v1/auth/mfa/totp/verify",
        json={"code": pyotp.TOTP(secret).now()},
        headers={**_bearer(access), **ip},
    )
    assert ver.status_code == 200, ver.text
    return secret


def test_mfa_ticket_survives_a_typo_then_burns_after_five(http, make_verified_user, ip):
    u = make_verified_user(http)
    secret = _enable_mfa(http, u, ip)

    gate = call(http, "POST", LOGIN, name="login_mfa_required_200", json_body=u, headers=ip)
    ticket = gate.json()["data"]["mfa_ticket"]
    typo = call(
        http,
        "POST",
        "/api/v1/auth/mfa/challenge",
        name="mfa_wrong_code_401",
        json_body={"mfa_ticket": ticket, "code": "000000"},
        headers=ip,
    )
    assert typo.status_code == 401 and typo.json()["error"]["code"] == "MFA_INVALID_CODE"
    ok = call(
        http,
        "POST",
        "/api/v1/auth/mfa/challenge",
        name="mfa_right_code_after_typo_200",
        json_body={"mfa_ticket": ticket, "code": pyotp.TOTP(secret).now()},
        headers=ip,
    )
    assert ok.status_code == 200, ok.text
    again = call(
        http,
        "POST",
        "/api/v1/auth/mfa/challenge",
        name="mfa_ticket_reuse_401",
        json_body={"mfa_ticket": ticket, "code": pyotp.TOTP(secret).now()},
        headers=ip,
    )
    assert again.json()["error"]["code"] == "MFA_TICKET_EXPIRED"

    ticket2 = http.post(LOGIN, json=u, headers=ip).json()["data"]["mfa_ticket"]
    last = None
    for i in range(5):
        last = call(
            http,
            "POST",
            "/api/v1/auth/mfa/challenge",
            name="mfa_fifth_wrong_code_401" if i == 4 else None,
            json_body={"mfa_ticket": ticket2, "code": "000000"},
            headers=ip,
        )
    assert last.status_code == 401 and last.json()["error"]["code"] == "MFA_TICKET_EXPIRED"
    dead = call(
        http,
        "POST",
        "/api/v1/auth/mfa/challenge",
        name="mfa_burned_ticket_right_code_401",
        json_body={"mfa_ticket": ticket2, "code": pyotp.TOTP(secret).now()},
        headers=ip,
    )
    assert dead.json()["error"]["code"] == "MFA_TICKET_EXPIRED"
    unknown = call(
        http,
        "POST",
        "/api/v1/auth/mfa/challenge",
        name="mfa_unknown_ticket_401",
        json_body={"mfa_ticket": "not-a-ticket", "code": "123456"},
        headers=ip,
    )
    assert unknown.json()["error"]["code"] == "MFA_TICKET_EXPIRED"


# --- D-AUTH-6: cookie mode + CSRF ---------------------------------------------------------------
def test_cookie_mode_login_refresh_and_csrf_guard(http, make_verified_user, ip):
    u = make_verified_user(http)
    r = call(
        http,
        "POST",
        LOGIN,
        name="login_cookie_mode_200",
        json_body=u,
        headers={**COOKIE_MODE, **ip},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["refresh_token"] is None and data["access_token"]
    assert "httponly" in r.headers["set-cookie"].lower()

    # the cookie alone, no header: refused (a forged cross-site request looks like this)
    no_hdr = call(
        http, "POST", "/api/v1/auth/refresh", name="refresh_cookie_no_header_403", headers=ip
    )
    assert no_hdr.status_code == 403 and no_hdr.json()["error"]["code"] == "CSRF_REJECTED"
    evil = call(
        http,
        "POST",
        "/api/v1/auth/refresh",
        name="refresh_cookie_foreign_origin_403",
        headers={**COOKIE_MODE, "Origin": "https://evil.example", **ip},
    )
    assert evil.status_code == 403
    ok = call(
        http,
        "POST",
        "/api/v1/auth/refresh",
        name="refresh_cookie_mode_200",
        headers={**COOKIE_MODE, **ip},
    )
    assert ok.status_code == 200 and ok.json()["data"]["refresh_token"] is None
    assert ok.json()["data"]["access_token"]
    # a rotation happened: the next call works with the new cookie, not the old one
    again = call(http, "POST", "/api/v1/auth/refresh", headers={**COOKIE_MODE, **ip})
    assert again.status_code == 200

    out_no = call(
        http, "POST", "/api/v1/auth/logout", name="logout_cookie_no_header_403", headers=ip
    )
    assert out_no.status_code == 403
    out = call(
        http,
        "POST",
        "/api/v1/auth/logout",
        name="logout_cookie_mode_200",
        headers={**COOKIE_MODE, **ip},
    )
    assert out.status_code == 200
    gone = call(
        http,
        "POST",
        "/api/v1/auth/refresh",
        name="refresh_after_logout_401",
        headers={**COOKIE_MODE, **ip},
    )
    assert gone.status_code == 401


def test_header_clients_still_get_the_refresh_token_in_the_body(http, make_verified_user, ip):
    u = make_verified_user(http)
    r = call(http, "POST", LOGIN, name="login_legacy_200", json_body=u, headers=ip)
    data = r.json()["data"]
    assert data["refresh_token"] and data["access_token"]
    http.cookies.clear()
    rot = call(
        http,
        "POST",
        "/api/v1/auth/refresh",
        name="refresh_body_token_200",
        json_body={"refresh_token": data["refresh_token"]},
        headers=ip,
    )
    assert rot.status_code == 200 and rot.json()["data"]["refresh_token"]


# --- gap 25: access tokens outlive sign-out (documented, not changed) ---------------------------
def test_access_token_still_works_after_logout_until_it_expires(http, make_verified_user, ip):
    u = make_verified_user(http)
    r = http.post(LOGIN, json=u, headers=ip)
    access, refresh = r.json()["data"]["access_token"], r.json()["data"]["refresh_token"]
    http.post("/api/v1/auth/logout", json={"refresh_token": refresh}, headers=ip)
    me = call(
        http,
        "GET",
        "/api/v1/auth/me",
        name="me_with_old_access_token_after_logout_200",
        headers={**_bearer(access), **ip},
    )
    assert me.status_code == 200
    dead = http.post("/api/v1/auth/refresh", json={"refresh_token": refresh}, headers=ip)
    assert (
        dead.status_code == 401
    )  # the refresh token is dead; only the 15-minute access token lives on


# --- gap 12: resend retry_after -----------------------------------------------------------------
def test_resend_reports_sent_and_retry_after(http, unique_email, ip):
    email = unique_email("resend")
    http.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": _pw(), "accepted_terms": True},
        headers=ip,
    )
    first = call(
        http,
        "POST",
        "/api/v1/auth/verify/resend",
        name="resend_first_200",
        json_body={"email": email},
        headers=ip,
    )
    second = call(
        http,
        "POST",
        "/api/v1/auth/verify/resend",
        name="resend_within_60s_200",
        json_body={"email": email},
        headers=ip,
    )
    unknown = call(
        http,
        "POST",
        "/api/v1/auth/verify/resend",
        name="resend_unknown_email_200",
        json_body={"email": unique_email("nobody")},
        headers=ip,
    )
    assert first.json()["data"] == {"sent": True, "retry_after": 60}
    assert second.json()["data"]["sent"] is False and 0 < second.json()["data"]["retry_after"] <= 60
    assert unknown.json()["data"] == {"sent": True, "retry_after": 60}  # same as a real address


# --- gaps 6, 7, 11, 21, 22: onboarding ------------------------------------------------------------
def test_onboarding_validation_invites_draft_guard_and_welcome_email(
    http, make_verified_user, unique_email, mailbox, ip
):
    founder = make_verified_user(http)
    h = {**_bearer(_login_access(http, founder, ip)), **ip}
    st = call(http, "GET", "/api/v1/onboarding/state", name="onboarding_state_200", headers=h)
    assert st.json()["data"]["pending_invite"] is False

    # gap 6: unsafe or empty input is refused, nothing is stored
    for name, body in [
        ("patch_website_javascript_422", {"step": 2, "website": "javascript:alert(1)"}),
        ("patch_phone_abc_422", {"step": 1, "phone": "abc"}),
        ("patch_goals_script_422", {"step": 4, "goals": ["<script>x</script>"]}),
        ("patch_empty_name_422", {"step": 2, "name": ""}),
        ("patch_empty_full_name_422", {"step": 1, "full_name": "   "}),
    ]:
        r = call(http, "PATCH", "/api/v1/onboarding/state", name=name, json_body=body, headers=h)
        assert r.status_code == 422, (name, r.text)
    good = call(
        http,
        "PATCH",
        "/api/v1/onboarding/state",
        name="patch_step1_200",
        json_body={
            "step": 1,
            "full_name": "Ada Founder",
            "country": "ng",
            "phone": "+234 801 234 5678",
        },
        headers=h,
    )
    assert good.status_code == 200
    web = call(
        http,
        "PATCH",
        "/api/v1/onboarding/state",
        name="patch_website_bare_domain_200",
        json_body={"step": 2, "name": "WalkCo", "website": "walkco.example"},
        headers=h,
    )
    assert web.json()["data"]["startup"]["website"] == "https://walkco.example"
    call(
        http,
        "PATCH",
        "/api/v1/onboarding/state",
        json_body={"step": 3, "industry": "Fintech", "stage": "idea"},
        headers=h,
    )
    call(
        http,
        "PATCH",
        "/api/v1/onboarding/state",
        json_body={"step": 4, "goals": ["Get first customers", "Raise funding"]},
        headers=h,
    )

    # gap 7: per-row invite results; gap 22: the invite subject
    mate = unique_email("mate")
    inv = call(
        http,
        "POST",
        "/api/v1/onboarding/invites",
        name="invites_results_200",
        json_body={
            "invites": [
                {"email": "nope", "role": "team_member"},
                {"email": mate, "role": "mentor"},
                {"email": mate, "role": "mentor"},
                {"email": founder["email"], "role": "team_member"},
            ]
        },
        headers=h,
    )
    assert inv.status_code == 200, inv.text
    rows = [(x["status"], x["reason"]) for x in inv.json()["data"]["results"]]
    assert rows == [
        ("invalid", "invalid_email"),
        ("created", None),
        ("skipped", "duplicate_in_request"),
        ("skipped", "self_invite"),
    ]
    mail = mailbox.latest_for(mate)
    assert mail["subject"] == "Ada Founder invited you to join WalkCo on Cofoundaz"
    _write(
        "email_invitation",
        {"subject": mail["subject"], "html": _TOKEN_IN_URL.sub(r"\1<invite-token>", mail["html"])},
    )

    # gap 11: the invited person (verified, no workspace) must not get a draft startup
    with httpx.Client(base_url=http.base_url, timeout=10.0) as mate_http:
        pw = _pw()
        mate_http.post(
            "/api/v1/auth/signup",
            json={"email": mate, "password": pw, "accepted_terms": True},
            headers=ip,
        )
        mate_http.post(
            "/api/v1/auth/verify",
            json={"token": mailbox.latest_token_for(mate, subject_contains="Verify")},
        )
        mh = {**_bearer(_login_access(mate_http, {"email": mate, "password": pw}, ip)), **ip}
        s1 = call(
            mate_http,
            "GET",
            "/api/v1/onboarding/state",
            name="invitee_state_pending_invite_200",
            headers=mh,
        )
        assert s1.json()["data"]["pending_invite"] is True and s1.json()["data"]["startup"] is None
        s2 = call(
            mate_http,
            "PATCH",
            "/api/v1/onboarding/state",
            name="invitee_patch_409",
            json_body={"step": 1, "full_name": "Mate"},
            headers=mh,
        )
        assert s2.status_code == 409 and s2.json()["error"]["code"] == "ONBOARDING_NOT_FOR_INVITED"
        s3 = call(
            mate_http,
            "POST",
            "/api/v1/onboarding/complete",
            name="invitee_complete_409",
            headers=mh,
        )
        assert s3.status_code == 409

        # accept the invite; the member's state is a member state and still no draft appears
        tok = mailbox.latest_token_for(mate, subject_contains="invited")
        acc = mate_http.post("/api/v1/invitations/accept", json={"token": tok}, headers=mh)
        assert acc.status_code == 200, acc.text
        s4 = call(mate_http, "GET", "/api/v1/onboarding/state", name="member_state_200", headers=mh)
        assert (
            s4.json()["data"]["completed"] is True and s4.json()["data"]["pending_invite"] is False
        )
        me = call(mate_http, "GET", "/api/v1/auth/me", name="member_me_200", headers=mh)
        assert me.status_code == 200

    # gap 21: completing sends the welcome email once, and the in-app notification appears
    done = call(http, "POST", "/api/v1/onboarding/complete", name="complete_200", headers=h)
    assert done.status_code == 200, done.text
    welcome = mailbox.latest_for(founder["email"])
    assert welcome["subject"] == "Welcome to Cofoundaz: let's build WalkCo"
    _write("email_welcome", {"subject": welcome["subject"], "html": welcome["html"]})
    again = call(http, "POST", "/api/v1/onboarding/complete", headers=h)
    assert again.status_code == 200
    after = mailbox.count_for(founder["email"])
    ws = {
        **h,
        "X-Workspace-Id": call(http, "GET", "/api/v1/onboarding/state", headers=h).json()["data"][
            "startup"
        ]["id"],
    }
    feed = call(
        http, "GET", "/api/v1/notifications", name="notifications_after_complete_200", headers=ws
    )
    assert feed.status_code == 200
    types = [n["type"] for n in feed.json()["data"]["notifications"]]
    assert "notification.onboarding_complete" in types, types  # the in-app "workspace is ready"
    assert mailbox.count_for(founder["email"]) == after  # a repeat call sent nothing more


# --- gap 4: limits (strict server only; exhaust the budget, so these run last) -------------------
@strict
def test_zz_signup_is_limited_per_ip(http, unique_email):
    ip = {"X-Forwarded-For": "192.0.2.50"}
    limit = int(os.environ["AUTH_SIGNUP_PER_IP_HOURLY"])
    for i in range(limit):
        r = http.post(
            "/api/v1/auth/signup",
            json={"email": unique_email("lim"), "password": _pw(), "accepted_terms": True},
            headers=ip,
        )
        assert r.status_code == 201, (i, r.text)
    r = call(
        http,
        "POST",
        "/api/v1/auth/signup",
        name="signup_rate_limited_429",
        json_body={"email": unique_email("lim"), "password": _pw(), "accepted_terms": True},
        headers=ip,
    )
    assert r.status_code == 429
    err = r.json()["error"]
    assert err["code"] == "RATE_LIMITED" and r.headers["retry-after"] == str(
        err["retry_after_seconds"]
    )


@strict
def test_zz_forgot_per_email_cap_is_silent_and_per_ip_cap_is_429(
    http, unique_email, make_verified_user, mailbox
):
    ip = {"X-Forwarded-For": "192.0.2.60"}
    u = make_verified_user(http)
    first = call(
        http,
        "POST",
        "/api/v1/auth/password/forgot",
        name="forgot_200",
        json_body={"email": u["email"]},
        headers=ip,
    )
    n_mail = mailbox.count_for(u["email"])
    # the 60 s cooldown also answers the same generic body: capture a repeat for the guide
    repeat = call(
        http,
        "POST",
        "/api/v1/auth/password/forgot",
        name="forgot_repeat_200",
        json_body={"email": u["email"]},
        headers=ip,
    )
    assert repeat.json() == first.json() and mailbox.count_for(u["email"]) == n_mail
    limit = int(os.environ["AUTH_FORGOT_PER_IP_HOURLY"])
    for _ in range(limit + 1):
        r = http.post("/api/v1/auth/password/forgot", json={"email": unique_email("f")}, headers=ip)
        if r.status_code == 429:
            break
    assert r.status_code == 429
    r = call(
        http,
        "POST",
        "/api/v1/auth/password/forgot",
        name="forgot_rate_limited_429",
        json_body={"email": unique_email("f")},
        headers=ip,
    )
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"


@strict
def test_zz_login_is_limited_per_ip_across_emails(http, unique_email):
    ip = {"X-Forwarded-For": "192.0.2.70"}
    limit = int(os.environ["AUTH_LOGIN_PER_IP_15MIN"])
    for _ in range(limit + 1):
        r = http.post(
            LOGIN,
            json={"email": unique_email("stuff"), "password": "wrong-pass-1"},
            headers=ip,
        )
        if r.status_code == 429:
            break
    assert r.status_code == 429
    r = call(
        http,
        "POST",
        LOGIN,
        name="login_ip_rate_limited_429",
        json_body={"email": unique_email("stuff"), "password": "wrong-pass-1"},
        headers=ip,
    )
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"
