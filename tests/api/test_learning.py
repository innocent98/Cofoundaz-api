import re
import uuid
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
    ("GET", "/articles/article-pricing-basics", None),
    ("GET", "/lessons/idea-shape-the-problem-1/notes", None),
    ("PUT", "/lessons/idea-shape-the-problem-1/notes", {"body": "x"}),
    ("POST", "/enrollments", {"course_id": "idea-shape-the-problem"}),
    ("PATCH", "/lessons/idea-shape-the-problem-1/progress", {"completed": True}),
    ("GET", "/certificates", None),
    ("GET", f"/certificates/{uuid.uuid4()}/pdf", None),
]


@pytest.fixture(autouse=True)
def _content(academy):
    return academy


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
    expected = (404,) if path.endswith("/pdf") else (200, 201)  # a stranger's certificate id
    assert r.status_code in expected, r.text


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
    assert first.json()["data"]["progress"] == 33
    assert first.json()["data"]["certificate"] is None
    _complete(client, h, "build-scope-the-mvp-2")

    last = _complete(client, h, "build-scope-the-mvp-3").json()["data"]
    assert last["progress"] == 100 and last["completed_at"] is not None
    cert = last["certificate"]
    assert cert["course_id"] == "build-scope-the-mvp" and cert["course_title"] == "Scope the MVP"
    assert cert["holder_name"] == "Cofoundaz member"  # no profile name set
    assert re.fullmatch(r"CFZ-[A-Z2-9]{4}-[A-Z2-9]{4}", cert["credential_id"])
    assert cert["pdf_ready"] is False and cert["pdf_url"] is None
    assert cert["linkedin_url"].startswith("https://www.linkedin.com/profile/add?")

    repeat = _complete(client, h, "build-scope-the-mvp-3").json()["data"]
    assert repeat["certificate"] is None  # nothing issued twice

    listed = client.get(f"{BASE}/certificates", headers=h).json()["data"]["certificates"]
    assert [c["id"] for c in listed] == [cert["id"]]

    detail = client.get(f"{BASE}/courses/build-scope-the-mvp", headers=h).json()["data"]
    assert detail["completed"] is True
    assert [lesson["completed"] for lesson in detail["lessons"]] == [True, True, True]


def test_position_can_be_saved_alone_and_resumes(client, db):
    _u, _s, h = _member(db)
    saved = client.patch(
        f"{BASE}/lessons/build-scope-the-mvp-2/progress", json={"position_s": 140}, headers=h
    )
    assert saved.status_code == 200, saved.text
    data = saved.json()["data"]
    assert data["position_s"] == 140 and data["progress"] == 0 and data["certificate"] is None
    course = client.get(f"{BASE}/courses/build-scope-the-mvp", headers=h).json()["data"]
    assert course["last_lesson_id"] == "build-scope-the-mvp-2"
    assert course["next_lesson_id"] == "build-scope-the-mvp-2" and course["position_s"] == 140
    both = client.patch(
        f"{BASE}/lessons/build-scope-the-mvp-2/progress",
        json={"completed": True, "position_s": 600},
        headers=h,
    ).json()["data"]
    assert both["progress"] == 33 and both["position_s"] == 600


@pytest.mark.parametrize(
    "body",
    [{}, {"position_s": -1}, {"position_s": 86_401}, {"position_s": "soon"}, {"completed": "yes"}],
)
def test_bad_progress_bodies_are_422(client, db, body):
    _u, _s, h = _member(db)
    r = client.patch(f"{BASE}/lessons/build-scope-the-mvp-1/progress", json=body, headers=h)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_recommendations_carry_a_hero_with_its_own_reason(client, db):
    _u, _s, h = _member(db)
    client.post(
        f"{BASE}/enrollments", json={"course_id": "validation-talk-to-customers"}, headers=h
    )
    _complete(client, h, "validation-talk-to-customers-1")
    data = client.get(f"{BASE}/recommendations", headers=h).json()["data"]
    assert data["stage"] == "validation"
    ids = [c["id"] for c in data["recommended"]]
    assert set(ids) == {
        "idea-shape-the-problem",
        "validation-talk-to-customers",
        "validation-price-your-product",
        "build-scope-the-mvp",
    }
    hero = data["hero"]
    assert hero["course_id"] == ids[0]
    assert hero["reason"] == data["recommendation_reason"] and hero["reason_source"] == "template"
    assert hero["matched_gaps"]  # the new workspace has logged no interviews or experiments
    assert [c["id"] for c in data["continue_watching"]] == ["validation-talk-to-customers"]
    assert all("[Placeholder]" not in c["title"] for c in data["recommended"])


def test_courses_list_and_detail_shapes(client, db):
    _u, _s, h = _member(db)
    courses = client.get(f"{BASE}/courses", headers=h).json()["data"]["courses"]
    assert len(courses) == 8 and all(c["review_status"] == "draft" for c in courses)
    assert {
        "id",
        "title",
        "summary",
        "level",
        "stage_tags",
        "lesson_count",
        "duration_min",
        "enrolled",
        "progress",
        "completed",
        "last_lesson_id",
        "next_lesson_id",
        "position_s",
        "review_status",
    } == set(courses[0])
    detail = client.get(f"{BASE}/courses/idea-shape-the-problem", headers=h).json()["data"]
    assert detail["description"] and len(detail["outcomes"]) == 3
    lesson = detail["lessons"][0]
    assert lesson["video"] is None and lesson["transcript"] and "video_ref" not in lesson


def test_a_lesson_with_a_youtube_video_returns_the_embed(client, db):
    from app.db.models.learning import AcademyLesson

    db.get(AcademyLesson, "idea-shape-the-problem-1").video_id = "dQw4w9WgXcQ"
    db.flush()
    _u, _s, h = _member(db)
    lesson = client.get(f"{BASE}/courses/idea-shape-the-problem", headers=h).json()["data"][
        "lessons"
    ][0]
    assert lesson["video"] == {
        "provider": "youtube",
        "id": "dQw4w9WgXcQ",
        "embed_url": "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
    }


def test_path_progress_and_ordered_courses_over_http(client, db):
    _u, _s, h = _member(db)
    for n in (1, 2, 3):
        _complete(client, h, f"idea-shape-the-problem-{n}")
    paths = client.get(f"{BASE}/paths", headers=h).json()["data"]["paths"]
    by_id = {p["id"]: p for p in paths}
    assert len(paths) == 6
    path = by_id["path-validation-foundations"]
    assert path["progress"] == 25  # (100 + 0 + 0 + 0) / 4
    assert [c["id"] for c in path["courses"]][0] == "idea-shape-the-problem"
    assert path["courses"][0]["completed"] is True
    assert by_id["path-scale-the-team"]["progress"] == 0


def test_articles_search_tags_and_reader(client, db):
    _u, _s, h = _member(db)
    listing = client.get(f"{BASE}/articles", headers=h).json()["data"]["articles"]
    assert len(listing) == 10
    assert all("body" not in a and a["review_status"] == "draft" for a in listing)
    found = client.get(f"{BASE}/articles?q=runway", headers=h).json()["data"]["articles"]
    assert found and found[0]["id"] == "article-read-your-runway"
    tagged = client.get(f"{BASE}/articles?tag=pricing", headers=h).json()["data"]["articles"]
    assert tagged and all("pricing" in a["tags"] for a in tagged)
    none = client.get(f"{BASE}/articles?q=zzzqqq", headers=h).json()["data"]["articles"]
    assert none == []
    detail = client.get(f"{BASE}/articles/article-pricing-basics", headers=h).json()["data"]
    assert detail["body"] and detail["read_min"] >= 1
    assert detail["related"]["courses"] and detail["related"]["articles"]
    assert client.get(f"{BASE}/articles/nope", headers=h).status_code == 404


def test_notes_are_private_to_their_author(client, db):
    _u, startup, h = _member(db)
    _t, _s, mate = _member(db, role=MembershipRole.team_member, startup=startup)
    url = f"{BASE}/lessons/idea-shape-the-problem-1/notes"
    assert client.get(url, headers=h).json()["data"] == {
        "lesson_id": "idea-shape-the-problem-1",
        "body": "",
        "updated_at": None,
    }
    put = client.put(url, json={"body": "my private plan"}, headers=h)
    assert put.status_code == 200, put.text
    assert client.get(url, headers=h).json()["data"]["body"] == "my private plan"
    assert client.get(url, headers=mate).json()["data"]["body"] == ""  # same workspace, other user
    client.put(url, json={"body": "teammate's own"}, headers=mate)
    assert client.get(url, headers=h).json()["data"]["body"] == "my private plan"
    other = f"{BASE}/lessons/idea-shape-the-problem-2/notes"
    assert client.get(other, headers=h).json()["data"]["body"] == ""  # per lesson
    assert client.put(url, json={"body": "x" * 20_001}, headers=h).status_code == 422
    assert client.get(f"{BASE}/lessons/nope/notes", headers=h).status_code == 404
    assert (
        client.put(f"{BASE}/lessons/nope/notes", json={"body": "x"}, headers=h).status_code == 404
    )


def test_a_teammate_cannot_see_your_progress_or_certificates(client, db):
    _u, startup, h = _member(db)
    _t, _s, teammate_h = _member(db, role=MembershipRole.team_member, startup=startup)
    for n in (1, 2, 3):
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


# --- certificate PDF: owner only ---------------------------------------------------------


def _run_worker(db):
    from app.worker import runner
    from app.worker.handlers import learning as handlers

    runner.register_handler("learning.certificate.generate", handlers.handle_certificate_generate)
    runner.run_once(db)


def _finish(client, h):
    for n in (1, 2, 3):
        out = _complete(client, h, f"build-scope-the-mvp-{n}").json()["data"]
    return out["certificate"]


def test_pdf_is_a_409_until_the_worker_has_rendered_it_then_a_pdf_for_the_owner(client, db):
    _u, _s, h = _member(db)
    cert = _finish(client, h)
    url = f"{BASE}/certificates/{cert['id']}/pdf"
    early = client.get(url, headers=h)
    assert early.status_code == 409 and early.json()["error"]["code"] == "CERTIFICATE_NOT_READY"

    _run_worker(db)
    ready = client.get(url, headers=h)
    assert ready.status_code == 200
    assert ready.headers["content-type"] == "application/pdf"
    assert ready.content.startswith(b"%PDF")
    assert cert["credential_id"] in ready.headers["content-disposition"]
    assert "no-store" in ready.headers["cache-control"]
    listed = client.get(f"{BASE}/certificates", headers=h).json()["data"]["certificates"][0]
    assert listed["pdf_ready"] is True and listed["pdf_url"].endswith(f"/{cert['id']}/pdf")


def test_nobody_else_can_download_someone_elses_certificate(client, db):
    _u, startup, h = _member(db)
    cert = _finish(client, h)
    _run_worker(db)
    url = f"{BASE}/certificates/{cert['id']}/pdf"
    assert client.get(url, headers=h).status_code == 200
    _t, _s, teammate = _member(db, role=MembershipRole.team_member, startup=startup)
    _o, _os, outsider = _member(db)  # a founder of another workspace
    mentor = _member(db, role=MembershipRole.mentor, startup=startup)[2]
    founder_of_other = _member(db)[2]
    for who in (teammate, outsider, mentor, founder_of_other):
        r = client.get(url, headers=who)
        assert r.status_code in (403, 404), r.text
        assert not r.content.startswith(b"%PDF")
    # the same id presented with the right workspace header but a different user is a 404
    assert client.get(url, headers=teammate).status_code == 404
    assert client.get(f"{BASE}/certificates/not-a-uuid/pdf", headers=h).status_code == 404
    assert client.get(url).status_code == 401
