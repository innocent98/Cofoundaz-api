from datetime import UTC, datetime

from app.core.config import settings
from app.core.security import get_password_hash
from app.db.models.enums import UserStatus
from tests.factories import create_user


def _login(client, db, email="s@x.com"):
    create_user(
        db,
        email=email,
        password_hash=get_password_hash("password1"),
        status=UserStatus.active,
        email_verified_at=datetime.now(UTC),
    )
    db.flush()
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "password1"})
    return r.json()["data"]["refresh_token"]


def test_refresh_rotates(client, db):
    refresh = _login(client, db)
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["access_token"] and data["refresh_token"] != refresh


def test_refresh_reuse_is_401(client, db):
    refresh = _login(client, db, email="s2@x.com")
    client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})  # rotate once
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})  # reuse
    assert r.status_code == 401


def test_logout_clears(client, db):
    refresh = _login(client, db, email="s3@x.com")
    r = client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    assert r.status_code == 200
    # subsequent refresh with the revoked token fails
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r2.status_code == 401


def test_refresh_cookie_only_no_body(client, db):
    # httpx's TestClient jar doesn't reliably auto-attach a Set-Cookie response
    # cookie to the next request against a single-label host ("testserver") --
    # the cookie gets stored with domain "testserver.local" but domain-matching
    # against the follow-up request silently fails to resend it. Set it on the
    # jar explicitly (same path the browser cookie carries) so this test proves
    # the cookie-only *code path* (server reads cookie, ignores empty body),
    # independent of that httpx jar quirk.
    refresh = _login(client, db, email="s4@x.com")
    client.cookies.set(settings.REFRESH_COOKIE_NAME, refresh, path="/api/v1/auth")
    r = client.post("/api/v1/auth/refresh")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["access_token"] and data["refresh_token"] != refresh
    assert "cfz_refresh" in r.cookies


def test_logout_no_body_no_cookie_is_200(client, db):
    _login(client, db, email="s5@x.com")
    client.cookies.clear()
    r = client.post("/api/v1/auth/logout")
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"logged_out": True}


def test_refresh_no_body_no_cookie_is_401(client, db):
    _login(client, db, email="s6@x.com")
    client.cookies.clear()
    r = client.post("/api/v1/auth/refresh")
    assert r.status_code == 401, r.text
