from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/learning"

NON_ACADEMY_ROLES = [
    MembershipRole.mentor,
    MembershipRole.accountant,
    MembershipRole.legal_advisor,
    MembershipRole.business_consultant,
    MembershipRole.investor,
]

ROUTES = [
    ("GET", "/recommendations", None),
    ("GET", "/courses", None),
    ("GET", "/courses/idea-shape-the-problem", None),
    ("GET", "/paths", None),
    ("GET", "/articles", None),
    ("POST", "/enrollments", {"course_id": "idea-shape-the-problem"}),
    ("PATCH", "/lessons/idea-shape-the-problem-1/progress", {"completed": True}),
    ("GET", "/certificates", None),
]


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _member(db, *, role=MembershipRole.founder, verified=True, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC) if verified else None)
    if startup is None:
        startup = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, startup, _headers(u, startup)


def _call(client, method, path, body, headers=None):
    return client.request(method, BASE + path, json=body, headers=headers)


def _complete(client, headers, lesson_id, completed=True):
    return client.patch(
        f"{BASE}/lessons/{lesson_id}/progress", json={"completed": completed}, headers=headers
    )


@pytest.mark.parametrize("role", NON_ACADEMY_ROLES)
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_roles_other_than_founder_and_team_member_are_forbidden(
    client, db, role, method, path, body
):
    _founder, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    r = _call(client, method, path, body, h)
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("role", [MembershipRole.founder, MembershipRole.team_member])
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_founders_and_team_members_are_allowed(client, db, role, method, path, body):
    _u, _s, h = _member(db, role=role)
    r = _call(client, method, path, body, h)
    assert r.status_code in (200, 201), r.text


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_unauthenticated_is_rejected(client, method, path, body):
    r = _call(client, method, path, body)
    assert r.status_code == 401, r.text


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_unverified_member_is_forbidden(client, db, method, path, body):
    _u, _s, h = _member(db, verified=False)
    r = _call(client, method, path, body, h)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


def test_non_member_of_the_workspace_is_forbidden(client, db):
    _owner, startup, _h = _member(db)
    outsider, _own, _oh = _member(db)
    r = client.get(f"{BASE}/courses", headers=_headers(outsider, startup))
    assert r.status_code == 403, r.text


def test_enrol_returns_201_then_200(client, db):
    _u, _s, h = _member(db)
    body = {"course_id": "idea-shape-the-problem"}
    first = client.post(f"{BASE}/enrollments", json=body, headers=h)
    again = client.post(f"{BASE}/enrollments", json=body, headers=h)
    assert first.status_code == 201, first.text
    assert again.status_code == 200, again.text
    assert first.json()["data"] == {
        "course_id": "idea-shape-the-problem",
        "progress": 0,
        "completed_at": None,
    }


def test_unknown_course_and_lesson_are_404(client, db):
    _u, _s, h = _member(db)
    enrol = client.post(f"{BASE}/enrollments", json={"course_id": "nope"}, headers=h)
    assert enrol.status_code == 404, enrol.text
    assert client.get(f"{BASE}/courses/nope", headers=h).status_code == 404
    assert _complete(client, h, "nope").status_code == 404


def test_uncompleting_a_lesson_is_rejected(client, db):
    _u, _s, h = _member(db)
    r = _complete(client, h, "idea-shape-the-problem-1", completed=False)
    assert r.status_code == 422, r.text


def test_finishing_a_course_over_http(client, db):
    _u, _s, h = _member(db)
    first = _complete(client, h, "build-scope-the-mvp-1")
    assert first.status_code == 200, first.text
    assert first.json()["data"]["progress"] == 50
    assert first.json()["data"]["certificate"] is None

    last = _complete(client, h, "build-scope-the-mvp-2").json()["data"]
    assert last["progress"] == 100 and last["completed_at"] is not None
    cert = last["certificate"]
    assert cert["course_id"] == "build-scope-the-mvp" and cert["credential_code"]

    listed = client.get(f"{BASE}/certificates", headers=h).json()["data"]["certificates"]
    assert [c["id"] for c in listed] == [cert["id"]]

    detail = client.get(f"{BASE}/courses/build-scope-the-mvp", headers=h).json()["data"]
    assert detail["completed"] is True
    assert [lesson["completed"] for lesson in detail["lessons"]] == [True, True]


def test_recommendations_include_continue_watching(client, db):
    _u, _s, h = _member(db)
    client.post(
        f"{BASE}/enrollments", json={"course_id": "validation-talk-to-customers"}, headers=h
    )
    data = client.get(f"{BASE}/recommendations", headers=h).json()["data"]
    assert data["stage"] == "validation"
    assert [c["id"] for c in data["recommended"]] == [
        "idea-shape-the-problem",
        "validation-talk-to-customers",
        "build-scope-the-mvp",
    ]
    assert [c["id"] for c in data["continue_watching"]] == ["validation-talk-to-customers"]


def test_recommendations_include_reason(client, db):
    _u, _s, h = _member(db)
    resp = client.get(f"{BASE}/recommendations", headers=h)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "recommendation_reason" in data
    assert isinstance(data["recommendation_reason"], str) and data["recommendation_reason"]


def test_path_progress_over_http(client, db):
    _u, _s, h = _member(db)
    for n in (1, 2, 3):
        _complete(client, h, f"idea-shape-the-problem-{n}")
    paths = client.get(f"{BASE}/paths", headers=h).json()["data"]["paths"]
    by_id = {p["id"]: p for p in paths}
    assert by_id["path-validation-foundations"]["progress"] == 33  # (100 + 0 + 0) / 3
    assert by_id["path-launch-to-growth"]["progress"] == 0


def test_a_teammate_cannot_see_your_progress_or_certificates(client, db):
    _u, startup, h = _member(db)
    _t, _s, teammate_h = _member(db, role=MembershipRole.team_member, startup=startup)
    for n in (1, 2):
        _complete(client, h, f"build-scope-the-mvp-{n}")
    courses = client.get(f"{BASE}/courses", headers=teammate_h).json()["data"]["courses"]
    assert all(c["progress"] == 0 and c["enrolled"] is False for c in courses)
    certs = client.get(f"{BASE}/certificates", headers=teammate_h).json()["data"]
    assert certs["certificates"] == []


def test_the_same_person_progresses_separately_in_two_workspaces(client, db):
    u, _first, h_first = _member(db)
    second = create_startup(db, owner=u, name="Second", stage=StartupStage.validation)
    create_membership(db, u, second)
    db.flush()
    _complete(client, h_first, "idea-shape-the-problem-1")
    courses = client.get(f"{BASE}/courses", headers=_headers(u, second)).json()["data"]
    progress = {c["id"]: c["progress"] for c in courses["courses"]}
    assert progress["idea-shape-the-problem"] == 0


def test_articles_are_listed_and_labelled_placeholder(client, db):
    _u, _s, h = _member(db)
    articles = client.get(f"{BASE}/articles", headers=h).json()["data"]["articles"]
    assert len(articles) >= 2
    assert all(a["title"].startswith("[Placeholder] ") for a in articles)
