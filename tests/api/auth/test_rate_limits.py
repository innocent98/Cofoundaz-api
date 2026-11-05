"""Gap 4: per-route limits on the unauthenticated auth surface (sign-up, forgot, verify-resend,
login). Counters live in Redis and are keyed on hashes; the e2e runner raises the limits."""

from app.core.config import settings
from tests.auth_helpers import PASSWORD, active_user, record_emails

SIGNUP = "/api/v1/auth/signup"
FORGOT = "/api/v1/auth/password/forgot"
RESEND = "/api/v1/auth/verify/resend"
LOGIN = "/api/v1/auth/login"


def _signup(client, n):
    return client.post(SIGNUP, json={"email": f"rl{n}@x.com", "password": "password1"})


def test_signup_is_limited_per_ip(client, monkeypatch):
    record_emails(monkeypatch)
    monkeypatch.setattr(settings, "AUTH_SIGNUP_PER_IP_HOURLY", 3)
    assert [_signup(client, i).status_code for i in range(3)] == [201, 201, 201]
    r = _signup(client, 3)
    assert r.status_code == 429
    err = r.json()["error"]
    assert err["code"] == "RATE_LIMITED"
    assert 0 < err["retry_after_seconds"] <= 3600
    assert r.headers["retry-after"] == str(err["retry_after_seconds"])
    assert err["message"] == "Too many requests. Try again in 60 minutes."


def test_forgot_is_limited_per_ip(client, monkeypatch):
    record_emails(monkeypatch)
    monkeypatch.setattr(settings, "AUTH_FORGOT_PER_IP_HOURLY", 2)
    for i in range(2):
        assert client.post(FORGOT, json={"email": f"f{i}@x.com"}).status_code == 200
    r = client.post(FORGOT, json={"email": "f9@x.com"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"


def test_forgot_per_email_cap_is_silent_and_identical(client, db, monkeypatch):
    """Past the hourly cap for one address nothing is sent, and the answer is the same generic
    one a normal call gets, whether or not the address has an account."""
    sender = record_emails(monkeypatch)
    monkeypatch.setattr(settings, "AUTH_FORGOT_PER_EMAIL_HOURLY", 1)
    from app.core.redis import get_redis

    active_user(db, "cap@x.com")
    db.flush()
    first = client.post(FORGOT, json={"email": "cap@x.com"})
    assert len(sender.sent) == 1
    get_redis().delete("password_reset_cooldown:cap@x.com")  # get past the 60 s cooldown
    second = client.post(FORGOT, json={"email": "cap@x.com"})
    assert second.status_code == 200 and second.json() == first.json()
    assert len(sender.sent) == 1  # capped: no second mail


def test_resend_is_limited_per_ip(client, monkeypatch):
    record_emails(monkeypatch)
    monkeypatch.setattr(settings, "AUTH_RESEND_PER_IP_HOURLY", 2)
    for i in range(2):
        assert client.post(RESEND, json={"email": f"r{i}@x.com"}).status_code == 200
    assert client.post(RESEND, json={"email": "r9@x.com"}).status_code == 429


def test_resend_per_email_cap_reports_not_sent_with_a_wait(client, db, monkeypatch):
    record_emails(monkeypatch)
    monkeypatch.setattr(settings, "AUTH_RESEND_PER_EMAIL_HOURLY", 1)
    from app.core.redis import get_redis

    client.post(RESEND, json={"email": "capr@x.com"})
    get_redis().delete("verify_resend_cooldown:capr@x.com")
    r = client.post(RESEND, json={"email": "capr@x.com"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["sent"] is False and 60 < data["retry_after"] <= 3600


def test_login_is_limited_per_ip_across_emails(client, db, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LOGIN_PER_IP_15MIN", 3)
    active_user(db, "ok@x.com")
    db.flush()
    for i in range(3):  # credential stuffing: a different address each time, no pair near locked
        client.post(LOGIN, json={"email": f"stuff{i}@x.com", "password": "x"})
    r = client.post(LOGIN, json={"email": "ok@x.com", "password": PASSWORD})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMITED"
