import pytest

from app.core.errors import NotFound
from app.db.models.enums import MembershipRole
from app.db.models.job import Job
from app.db.models.learning import Certificate, LessonNote, LessonPosition
from app.platform.events import event_bus
from app.services.learning import service as svc
from app.services.learning.catalog import (
    get_article,
    get_course,
    get_lesson,
    list_articles,
    list_courses,
    list_paths,
)
from tests.factories import create_membership, create_startup, create_user
from tests.learning_helpers import founder_and_startup

COURSE = "build-scope-the-mvp"  # three lessons
L1, L2, L3 = (f"{COURSE}-{n}" for n in (1, 2, 3))


@pytest.fixture(autouse=True)
def _content(academy):
    return academy


def _done(db, user, startup, *lesson_ids):
    out = None
    for lesson_id in lesson_ids:
        out = svc.update_lesson_progress(
            db, startup.id, user.id, lesson_id, completed=True, position_s=None
        )
    return out


# --- catalog reads -----------------------------------------------------------------------


def test_catalog_lookups_and_unknown_ids(db):
    assert get_course(db, COURSE).title == "Scope the MVP"
    course, lesson = get_lesson(db, L2)
    assert course.id == COURSE and lesson.position == 2
    for call in (
        lambda: get_course(db, "nope"),
        lambda: get_lesson(db, "nope"),
        lambda: get_article(db, "nope"),
    ):
        with pytest.raises(NotFound):
            call()


def test_articles_search_and_tag_filters(db):
    everything = list_articles(db)
    assert len(everything) >= 10
    assert [a.id for a in list_articles(db, q="runway")][0] == "article-read-your-runway"
    assert all("pricing" in a.tags for a in list_articles(db, tag="PRICING"))
    assert list_articles(db, q="zzzqqq") == []
    assert list_articles(db, q="100%_") == []  # LIKE metacharacters are escaped, not wildcards
    assert [a.id for a in list_articles(db, q="runway", tag="pricing")] == []


def test_article_detail_has_related_articles_and_courses(db):
    detail = svc.article_detail(db, get_article(db, "article-pricing-basics"))
    assert detail["body"] and detail["review_status"] == "draft"
    related = detail["related"]
    assert "article-pricing-basics" not in {a["id"] for a in related["articles"]}
    assert "validation-price-your-product" in {c["id"] for c in related["courses"]}
    assert len(related["articles"]) <= 3 and len(related["courses"]) <= 3


# --- enrolment and progress --------------------------------------------------------------


def test_enrol_is_idempotent_and_rejects_unknown_courses(db):
    user, startup = founder_and_startup(db)
    row, created = svc.get_or_create_enrollment(db, startup.id, user.id, COURSE)
    again, created_again = svc.get_or_create_enrollment(db, startup.id, user.id, COURSE)
    assert created and not created_again and row.id == again.id
    with pytest.raises(NotFound):
        svc.get_or_create_enrollment(db, startup.id, user.id, "nope")


def test_lessons_complete_in_any_order_and_the_last_one_issues_one_certificate(db):
    user, startup = founder_and_startup(db)
    enrollment, cert, _ = _done(db, user, startup, L3)
    assert enrollment.progress == 33 and cert is None
    _done(db, user, startup, L1)
    enrollment, cert, _ = _done(db, user, startup, L2)
    assert enrollment.progress == 100 and enrollment.completed_at is not None
    assert cert is not None and cert.holder_name == "Ada Founder"
    assert cert.verify_code.startswith("CFZ-") and len(cert.verify_code) == 18
    # repeating changes nothing and issues nothing
    enrollment, again, _ = _done(db, user, startup, L2)
    assert again is None and db.query(Certificate).count() == 1
    jobs = db.query(Job).filter_by(type="learning.certificate.generate").all()
    assert len(jobs) == 1 and jobs[0].payload == {"certificate_id": str(cert.id)}
    assert (
        "learning.course.completed",
        {
            "startup_id": str(startup.id),
            "user_id": str(user.id),
            "course_id": COURSE,
            "course_title": "Scope the MVP",
            "certificate_id": str(cert.id),
        },
    ) in event_bus.published


def test_position_is_saved_per_lesson_and_overwritten(db):
    user, startup = founder_and_startup(db)
    svc.update_lesson_progress(db, startup.id, user.id, L1, completed=False, position_s=95)
    svc.update_lesson_progress(db, startup.id, user.id, L1, completed=False, position_s=130)
    rows = db.query(LessonPosition).filter_by(user_id=user.id).all()
    assert [(r.lesson_id, r.position_s) for r in rows] == [(L1, 130)]
    state = svc.load_state(db, startup.id, user.id)
    summary = svc.course_summary(get_course(db, COURSE), state)
    assert summary["enrolled"] and summary["progress"] == 0  # saving a position is not a completion
    assert summary["last_lesson_id"] == L1
    assert summary["next_lesson_id"] == L1 and summary["position_s"] == 130


def test_continue_opens_the_next_unfinished_lesson_after_a_completion(db):
    user, startup = founder_and_startup(db)
    _done(db, user, startup, L1)
    summary = svc.course_summary(get_course(db, COURSE), svc.load_state(db, startup.id, user.id))
    assert summary["last_lesson_id"] == L1
    assert summary["next_lesson_id"] == L2 and summary["position_s"] == 0
    _done(db, user, startup, L2, L3)
    summary = svc.course_summary(get_course(db, COURSE), svc.load_state(db, startup.id, user.id))
    assert summary["completed"] and summary["next_lesson_id"] is None


def test_course_detail_exposes_description_outcomes_video_and_review_flag(db):
    user, startup = founder_and_startup(db)
    detail = svc.course_detail(get_course(db, COURSE), svc.load_state(db, startup.id, user.id))
    assert detail["description"] and detail["outcomes"] and detail["review_status"] == "draft"
    assert [lesson["order"] for lesson in detail["lessons"]] == [1, 2, 3]
    for lesson in detail["lessons"]:
        assert lesson["video"] is None
        assert "video_ref" not in lesson and "placeholder" not in str(lesson).lower()


def test_continue_watching_needs_progress_or_a_position(db):
    user, startup = founder_and_startup(db)
    svc.get_or_create_enrollment(db, startup.id, user.id, "idea-shape-the-problem")
    courses = list_courses(db)
    state = svc.load_state(db, startup.id, user.id)
    assert svc.continue_watching(courses, state) == []  # enrolled at 0%, nothing started
    svc.update_lesson_progress(db, startup.id, user.id, L1, completed=False, position_s=5)
    _done(db, user, startup, "idea-shape-the-problem-1")
    state = svc.load_state(db, startup.id, user.id)
    watching = [c.id for c in svc.continue_watching(courses, state)]
    assert watching == ["idea-shape-the-problem", COURSE]  # most recently touched first
    _done(db, user, startup, L1, L2, L3)
    state = svc.load_state(db, startup.id, user.id)
    assert COURSE not in [c.id for c in svc.continue_watching(courses, state)]


def test_paths_list_ordered_courses_with_per_course_progress(db):
    user, startup = founder_and_startup(db)
    _done(
        db,
        user,
        startup,
        "idea-shape-the-problem-1",
        "idea-shape-the-problem-2",
        "idea-shape-the-problem-3",
    )
    state = svc.load_state(db, startup.id, user.id)
    courses = {c.id: c for c in list_courses(db)}
    paths = {p.id: svc.path_view(p, courses, state) for p in list_paths(db)}
    path = paths["path-validation-foundations"]
    assert [c["id"] for c in path["courses"]] == [
        "idea-shape-the-problem",
        "validation-talk-to-customers",
        "validation-price-your-product",
        "build-scope-the-mvp",
    ]
    assert path["courses"][0]["completed"] and path["courses"][0]["progress"] == 100
    assert path["progress"] == 25  # (100 + 0 + 0 + 0) / 4
    assert path["course_count"] == 4 and path["duration_min"] > 0
    assert {p["stage"] for p in paths.values()} == {
        "idea",
        "validation",
        "build",
        "launch",
        "growth",
        "scale",
    }


# --- private notes -----------------------------------------------------------------------


def test_notes_are_per_user_and_per_lesson(db):
    founder, startup = founder_and_startup(db)
    mate = create_user(db)
    create_membership(db, mate, startup, role=MembershipRole.team_member)
    assert svc.get_note(db, startup.id, founder.id, L1) == {
        "lesson_id": L1,
        "body": "",
        "updated_at": None,
    }
    saved = svc.put_note(db, startup.id, founder.id, L1, "my private thought")
    svc.put_note(db, startup.id, founder.id, L1, "edited")
    assert svc.get_note(db, startup.id, founder.id, L1)["body"] == "edited"
    assert saved["updated_at"]
    assert svc.get_note(db, startup.id, founder.id, L2)["body"] == ""  # a different lesson
    assert svc.get_note(db, startup.id, mate.id, L1)["body"] == ""  # a teammate sees nothing
    assert db.query(LessonNote).count() == 1


def test_notes_for_an_unknown_lesson_are_404(db):
    user, startup = founder_and_startup(db)
    with pytest.raises(NotFound):
        svc.get_note(db, startup.id, user.id, "nope")
    with pytest.raises(NotFound):
        svc.put_note(db, startup.id, user.id, "nope", "x")


def test_same_person_has_separate_state_in_two_workspaces(db):
    user, first = founder_and_startup(db)
    second = create_startup(db, owner=user, name="Second")
    _done(db, user, first, L1)
    svc.put_note(db, first.id, user.id, L1, "first only")
    assert svc.load_state(db, second.id, user.id).enrollments == {}
    assert svc.get_note(db, second.id, user.id, L1)["body"] == ""
