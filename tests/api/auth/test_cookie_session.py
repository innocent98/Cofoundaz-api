"""D-AUTH-6 (backend side): the HttpOnly refresh cookie, cookie-mode responses, and the CSRF
guard on the two endpoints that accept the cookie as their only credential."""

import pytest

from app.core.config import settings
from tests.auth_helpers import PASSWORD, active_user

COOKIE_MODE = {"X-Auth-Mode": "cookie"}
ALLOWED_ORIGIN = settings.BACKEND_CORS_ORIGINS[0]


def _login(client, db, email, headers=None):
    active_user(db, email)
    db.flush()
    r = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}, headers=headers or {}
    )
    assert r.status_code == 200, r.text
    return r


def _holding_only_the_cookie(client, refresh):
    client.cookies.clear()
    client.cookies.set(settings.REFRESH_COOKIE_NAME, refresh, path="/api/v1/auth")


def test_login_sets_an_httponly_secure_samesite_path_scoped_cookie(client, db):
    r = _login(client, db, "attrs@x.com")
    header = r.headers["set-cookie"].lower()
    assert f"{settings.REFRESH_COOKIE_NAME.lower()}=" in header
    for attr in ("httponly", "secure", "samesite=lax", "path=/api/v1/auth", "max-age="):
        assert attr in header, header


def test_login_without_the_header_still_returns_the_refresh_token_for_the_current_fe(client, db):
    data = _login(client, db, "legacy@x.com").json()["data"]
    assert data["refresh_token"] and data["access_token"]


def test_login_in_cookie_mode_keeps_the_refresh_token_out_of_the_body(client, db):
    r = _login(client, db, "cookiemode@x.com", COOKIE_MODE)
    data = r.json()["data"]
    assert data["refresh_token"] is None
    assert data["access_token"]
    assert data["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert settings.REFRESH_COOKIE_NAME in r.headers["set-cookie"]  # it is in the cookie


def test_silent_refresh_with_the_cookie_alone(client, db):
    first = _login(client, db, "silent@x.com", COOKIE_MODE)
    cookie = first.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    _holding_only_the_cookie(client, cookie)
    r = client.post("/api/v1/auth/refresh", headers={**COOKIE_MODE, "Origin": ALLOWED_ORIGIN})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["refresh_token"] is None
    rotated = r.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    assert rotated and rotated != cookie  # rotation: a new cookie value every refresh


def test_cookie_only_refresh_without_the_header_is_rejected(client, db):
    refresh = _login(client, db, "nohdr@x.com").json()["data"]["refresh_token"]
    _holding_only_the_cookie(client, refresh)
    r = client.post("/api/v1/auth/refresh")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_REJECTED"


def test_cookie_only_refresh_from_a_foreign_origin_is_rejected(client, db):
    refresh = _login(client, db, "evil@x.com").json()["data"]["refresh_token"]
    _holding_only_the_cookie(client, refresh)
    r = client.post(
        "/api/v1/auth/refresh", headers={**COOKIE_MODE, "Origin": "https://evil.example"}
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_REJECTED"


def test_a_rejected_csrf_attempt_does_not_rotate_or_revoke_anything(client, db):
    refresh = _login(client, db, "intact@x.com").json()["data"]["refresh_token"]
    _holding_only_the_cookie(client, refresh)
    client.post("/api/v1/auth/refresh", headers={"Origin": "https://evil.example"})
    client.cookies.clear()
    ok = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert ok.status_code == 200  # the token was untouched by the forged request


def test_a_body_token_needs_no_header_so_the_current_fe_keeps_working(client, db):
    refresh = _login(client, db, "body@x.com").json()["data"]["refresh_token"]
    client.cookies.clear()
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200 and r.json()["data"]["refresh_token"]


def test_refresh_with_no_credential_at_all_is_401_not_403(client):
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401


def test_cookie_only_logout_needs_the_header_and_then_revokes_and_clears(client, db):
    refresh = _login(client, db, "bye@x.com").json()["data"]["refresh_token"]
    _holding_only_the_cookie(client, refresh)
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 403
    r = client.post("/api/v1/auth/logout", headers={**COOKIE_MODE, "Origin": ALLOWED_ORIGIN})
    assert r.status_code == 200 and r.json()["data"] == {"logged_out": True}
    assert "max-age=0" in r.headers["set-cookie"].lower()
    client.cookies.clear()
    dead = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert dead.status_code == 401  # revoked server side, not just cleared in the browser


@pytest.mark.parametrize("origin", [None, ALLOWED_ORIGIN])
def test_requests_without_a_browser_origin_or_from_ours_pass(client, db, origin):
    refresh = _login(client, db, f"o{origin is None}@x.com").json()["data"]["refresh_token"]
    _holding_only_the_cookie(client, refresh)
    headers = dict(COOKIE_MODE)
    if origin:
        headers["Origin"] = origin
    resp = client.post("/api/v1/auth/refresh", headers=headers)
    assert resp.status_code == 200
