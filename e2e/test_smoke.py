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
        # onboarding surface
        "/api/v1/onboarding/state",
        "/api/v1/onboarding/logo",
        "/api/v1/onboarding/invites",
        "/api/v1/onboarding/complete",
        "/api/v1/invitations/{token}",
        "/api/v1/invitations/accept",
        # assessment surface
        "/api/v1/assessments",
        "/api/v1/assessments/{assessment_id}/next-question",
        "/api/v1/assessments/{assessment_id}/answers",
        "/api/v1/assessments/{assessment_id}/complete",
        "/api/v1/assessments/{assessment_id}",
        "/api/v1/assessments/compare",
        # health-score surface
        "/api/v1/health-score",
        "/api/v1/health-score/dimensions/{dim}",
        "/api/v1/health-score/history",
        "/api/v1/health-score/benchmarks",
        "/api/v1/health-score/recommendations",
        "/api/v1/health-score/recommendations/{rec_id}/accept",
        # roadmap surface
        "/api/v1/roadmap",
        "/api/v1/roadmap/generate",
        "/api/v1/roadmap/phases",
        "/api/v1/roadmap/phases/{phase_id}",
        "/api/v1/roadmap/milestones",
        "/api/v1/roadmap/milestones/{milestone_id}",
        "/api/v1/roadmap/tasks",
        "/api/v1/roadmap/tasks/{task_id}",
        "/api/v1/roadmap/dependencies",
        "/api/v1/roadmap/tasks/{task_id}/dependencies",
        "/api/v1/roadmap/tasks/{task_id}/dependencies/{depends_on_task_id}",
        "/api/v1/roadmap/templates",
        "/api/v1/roadmap/templates/{template_id}",
        "/api/v1/roadmap/templates/{template_id}/apply",
        "/api/v1/roadmap/replan/preview",
        "/api/v1/roadmap/replan/apply",
        "/api/v1/roadmap/replan/history",
        # mission ("Today's Mission") surface
        "/api/v1/missions/today",
        "/api/v1/missions/tasks",
        "/api/v1/missions/tasks/{task_id}",
        "/api/v1/missions/history",
        "/api/v1/missions/settings",
        # dashboard surface
        "/api/v1/dashboard/summary",
        "/api/v1/dashboard/activity",
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


def test_onboarding_state_requires_auth(http: httpx.Client):
    # The wizard surface is protected — no token → 401.
    assert http.get("/api/v1/onboarding/state").status_code == 401


def test_invitation_preview_unknown_token_404(http: httpx.Client):
    # Public preview endpoint answers cleanly (404) for a bogus token.
    r = http.get("/api/v1/invitations/definitely-not-a-real-token")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_assessments_requires_auth(http: httpx.Client):
    # Assessment surface is protected — workspace header present but no token → 401.
    r = http.get(
        "/api/v1/assessments",
        headers={"X-Workspace-Id": "00000000-0000-0000-0000-000000000000"},
    )
    assert r.status_code == 401
