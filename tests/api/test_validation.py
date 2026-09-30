from datetime import UTC, datetime

import pytest

from app.core.rate_limit import limiter
from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/validation"

NON_MEMBER_ROLES = [
    MembershipRole.mentor,
    MembershipRole.accountant,
    MembershipRole.legal_advisor,
    MembershipRole.business_consultant,
    MembershipRole.investor,
]

ROUTES = [
    ("GET", "/assumptions", None),
    ("POST", "/assumptions", {"statement": "They will pay", "risk": "high"}),
    ("GET", "/experiments", None),
    ("POST", "/experiments", {"name": "Fake door", "type": "smoke_test"}),
    ("GET", "/interviews", None),
    ("GET", "/surveys", None),
    ("POST", "/surveys", {"title": "Pricing"}),
    ("POST", "/synthesize", {}),
    ("POST", "/scripts/generate", {}),
]


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _member(db, *, role=MembershipRole.founder, verified=True, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC) if verified else None)
    if startup is None:
        startup = create_startup(db, owner=u)
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, startup, _headers(u, startup)


def _call(client, method, path, body, headers=None):
    return client.request(method, BASE + path, json=body, headers=headers)


def _open_survey(client, headers, questions=None):
    questions = questions or [
        {"type": "choice", "prompt": "Pick one", "options": ["A", "B"], "required": True},
        {"type": "open", "prompt": "Anything else?"},
    ]
    created = client.post(
        f"{BASE}/surveys", json={"title": "Pricing", "questions": questions}, headers=headers
    ).json()["data"]
    opened = client.patch(
        f"{BASE}/surveys/{created['id']}", json={"status": "open"}, headers=headers
    ).json()["data"]
    return opened["survey"], opened["public_token"]


@pytest.mark.parametrize("role", NON_MEMBER_ROLES)
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_other_roles_are_forbidden(client, db, role, method, path, body):
    _founder, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    assert _call(client, method, path, body, h).status_code == 403


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_unauthenticated_is_rejected(client, method, path, body):
    assert _call(client, method, path, body).status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_unverified_member_is_forbidden(client, db, method, path, body):
    _u, _s, h = _member(db, verified=False)
    r = _call(client, method, path, body, h)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


@pytest.mark.parametrize("role", [MembershipRole.founder, MembershipRole.team_member])
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_founders_and_team_members_are_allowed(client, db, role, method, path, body):
    _u, _s, h = _member(db, role=role)
    assert _call(client, method, path, body, h).status_code in (200, 201, 202)


def test_assumption_crud_and_evidence_count(client, db):
    _u, _s, h = _member(db)
    created = client.post(
        f"{BASE}/assumptions", json={"statement": "They will pay", "risk": "high"}, headers=h
    )
    assert created.status_code == 201
    assumption = created.json()["data"]
    assert assumption["status"] == "untested" and assumption["evidence_count"] == 0

    client.post(
        f"{BASE}/experiments",
        json={"name": "Fake door", "type": "smoke_test", "assumption_ids": [assumption["id"]]},
        headers=h,
    )
    listed = client.get(f"{BASE}/assumptions", headers=h).json()["data"]["assumptions"]
    assert listed[0]["evidence_count"] == 1

    moved = client.patch(
        f"{BASE}/assumptions/{assumption['id']}", json={"status": "validated"}, headers=h
    )
    assert moved.status_code == 200 and moved.json()["data"]["status"] == "validated"
    filtered = client.get(f"{BASE}/assumptions?status=validated", headers=h).json()["data"]
    assert [a["id"] for a in filtered["assumptions"]] == [assumption["id"]]


def test_unknown_ids_are_404(client, db):
    _u, _s, h = _member(db)
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.patch(f"{BASE}/assumptions/{missing}", json={}, headers=h).status_code == 404
    assert client.get(f"{BASE}/smoke-tests/{missing}/stats", headers=h).status_code == 404
    assert client.get(f"{BASE}/surveys/{missing}/analytics", headers=h).status_code == 404


def test_another_workspace_cannot_read_or_edit(client, db):
    _u, _startup, h = _member(db)
    _outsider, _own, outsider_h = _member(db)
    created = client.post(
        f"{BASE}/assumptions", json={"statement": "Mine", "risk": "low"}, headers=h
    ).json()["data"]
    assert (
        client.patch(
            f"{BASE}/assumptions/{created['id']}", json={"risk": "high"}, headers=outsider_h
        ).status_code
        == 404
    )
    assert client.get(f"{BASE}/assumptions", headers=outsider_h).json()["data"]["assumptions"] == []


def test_smoke_test_stats(client, db):
    _u, _s, h = _member(db)
    created = client.post(
        f"{BASE}/experiments",
        json={"name": "Fake door", "type": "smoke_test", "metrics": {"visits": 50, "signups": 5}},
        headers=h,
    ).json()["data"]
    stats = client.get(f"{BASE}/smoke-tests/{created['id']}/stats", headers=h).json()["data"]
    assert stats["conversion"] == 10.0


def test_opening_a_survey_returns_the_token_once(client, db):
    _u, _s, h = _member(db)
    survey, token = _open_survey(client, h)
    assert token and survey["status"] == "open" and survey["has_link"] is True
    again = client.patch(
        f"{BASE}/surveys/{survey['id']}", json={"status": "closed"}, headers=h
    ).json()["data"]
    assert again["public_token"] is None


def test_public_read_and_submit_need_no_login(client, db):
    _u, _s, h = _member(db)
    survey, token = _open_survey(client, h)

    form = client.get(f"{BASE}/surveys/{token}")
    assert form.status_code == 200
    data = form.json()["data"]
    assert set(data) == {"title", "questions"}
    assert str(survey["id"]) not in str(data)

    choice = data["questions"][0]["id"]
    sent = client.post(f"{BASE}/surveys/{token}/responses", json={"answers": {choice: "A"}})
    assert sent.status_code == 201
    assert sent.json()["data"] == {"received": True}


def test_public_routes_leak_nothing_when_the_token_is_wrong(client, db):
    _u, _s, h = _member(db)
    _survey, token = _open_survey(client, h)
    assert client.get(f"{BASE}/surveys/not-a-real-token").status_code == 404
    # A draft survey's id used as a token answers identically to an unknown one.
    draft = client.post(f"{BASE}/surveys", json={"title": "Draft"}, headers=h).json()["data"]
    assert client.get(f"{BASE}/surveys/{draft['id']}").status_code == 404
    assert (
        client.post(f"{BASE}/surveys/{token}x/responses", json={"answers": {}}).status_code == 404
    )


def test_bad_answers_are_422(client, db):
    _u, _s, h = _member(db)
    _survey, token = _open_survey(client, h)
    assert client.post(f"{BASE}/surveys/{token}/responses", json={"answers": {}}).status_code == 422


def test_analytics_are_members_only_and_count_responses(client, db):
    _u, _s, h = _member(db)
    survey, token = _open_survey(client, h)
    form = client.get(f"{BASE}/surveys/{token}").json()["data"]
    choice = form["questions"][0]["id"]
    client.post(f"{BASE}/surveys/{token}/responses", json={"answers": {choice: "A"}})
    assert client.get(f"{BASE}/surveys/{survey['id']}/analytics").status_code == 401
    out = client.get(f"{BASE}/surveys/{survey['id']}/analytics", headers=h).json()["data"]
    assert out["responses"] == 1 and out["completion_rate"] == 100
    assert out["questions"][0]["counts"] == {"A": 1, "B": 0}


def test_job_stubs_enqueue_and_return_metadata(client, db):
    _u, _s, h = _member(db)
    for path in ("/synthesize", "/scripts/generate"):
        r = client.post(BASE + path, json={}, headers=h)
        assert r.status_code == 202
        body = r.json()["data"]
        assert body["job_id"] and body["status"] == "queued"


def test_the_public_response_route_carries_its_own_rate_limit():
    registered = " ".join(str(key) for key in limiter._route_limits)
    assert "submit_survey_response_endpoint" in registered
