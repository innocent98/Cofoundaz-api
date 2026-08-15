"""Smoke layer: every auth surface answers with its EXPECTED status on the live
server (not 404/500). Confirms the whole surface is wired through the real ASGI
stack — middleware, exception handlers, rate limiter — not just in-process.
"""

import httpx


def test_health_ok(http: httpx.Client):
    r = http.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_api_health_ok(http: httpx.Client):
    r = http.get("/api/v1/health")
    assert r.status_code == 200


def test_openapi_served(http: httpx.Client):
    r = http.get("/api/v1/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    # The auth surface is actually registered on the live app.
    for p in [
        "/api/v1/auth/signup",
        "/api/v1/auth/login",
        "/api/v1/auth/verify",
        "/api/v1/auth/refresh",
        "/api/v1/auth/me",
        "/api/v1/auth/password/forgot",
        "/api/v1/auth/mfa/totp/setup",
    ]:
        assert p in paths, f"missing route {p}"


def test_me_requires_auth(http: httpx.Client):
    assert http.get("/api/v1/auth/me").status_code == 401


def test_unknown_route_404(http: httpx.Client):
    assert http.get("/api/v1/auth/nope").status_code == 404


def test_validation_error_is_enveloped(http: httpx.Client):
    # Missing password -> 422 in the standard error envelope, from the live handler.
    r = http.post("/api/v1/auth/signup", json={"email": "x@y.com"})
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(body["error"]["field_errors"], list)


def test_seams_return_501(http: httpx.Client):
    for path in [
        "/api/v1/auth/oauth/google",
        "/api/v1/auth/oauth/apple",
        "/api/v1/auth/mfa/sms/setup",
        "/api/v1/auth/mfa/sms/verify",
    ]:
        r = http.post(path, json={})
        assert r.status_code == 501, path
        assert r.json()["error"]["code"] == "FEATURE_NOT_ENABLED"
