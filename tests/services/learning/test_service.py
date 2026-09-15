import pytest

from app.core.errors import NotFound
from app.db.models.enums import CourseLevel, MembershipRole
from app.db.models.job import Job
from app.db.models.learning import Certificate, Enrollment, LessonProgress
from app.services.learning.catalog import COURSES, Course
from app.services.learning.service import (
    complete_lesson,
    course_progress,
    get_or_create_enrollment,
    list_certificates,
    serialize_certificate,
    serialize_enrollment,
)
from tests.factories import create_membership, create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    db.flush()
    return u, s


def _finish(db, s, u, course_id):
    """Complete every lesson of a course; return the certificate from each call."""
    return [complete_lesson(db, s.id, u.id, lesson.id)[1] for lesson in COURSES[course_id].lessons]


def test_enrol_is_idempotent(db):
    u, s = _ctx(db)
    first, created = get_or_create_enrollment(db, s.id, u.id, "idea-shape-the-problem")
    again, created_again = get_or_create_enrollment(db, s.id, u.id, "idea-shape-the-problem")
    assert created is True and created_again is False and again.id == first.id
    assert db.query(Enrollment).filter_by(startup_id=s.id, user_id=u.id).count() == 1


def test_enrol_in_unknown_course_404(db):
    u, s = _ctx(db)
    with pytest.raises(NotFound):
        get_or_create_enrollment(db, s.id, u.id, "nope")


def test_completing_a_lesson_auto_enrols(db):
    u, s = _ctx(db)
    enrollment, cert = complete_lesson(db, s.id, u.id, "idea-shape-the-problem-1")
    assert enrollment.course_id == "idea-shape-the-problem"
    assert enrollment.progress == 33 and cert is None
    assert db.query(Enrollment).filter_by(startup_id=s.id, user_id=u.id).count() == 1


def test_completing_the_same_lesson_twice_changes_nothing(db):
    u, s = _ctx(db)
    complete_lesson(db, s.id, u.id, "idea-shape-the-problem-1")
    enrollment, cert = complete_lesson(db, s.id, u.id, "idea-shape-the-problem-1")
    assert enrollment.progress == 33 and cert is None
    assert db.query(LessonProgress).filter_by(startup_id=s.id, user_id=u.id).count() == 1


def test_completing_an_unknown_lesson_404(db):
    u, s = _ctx(db)
    with pytest.raises(NotFound):
        complete_lesson(db, s.id, u.id, "nope")


def test_course_percentage_is_rounded_completed_over_total(db):
    u, s = _ctx(db)
    course = COURSES["idea-shape-the-problem"]  # 3 lessons
    complete_lesson(db, s.id, u.id, "idea-shape-the-problem-1")
    assert course_progress(db, s.id, u.id, course) == 33
    complete_lesson(db, s.id, u.id, "idea-shape-the-problem-2")
    assert course_progress(db, s.id, u.id, course) == 67


def test_a_course_with_no_lessons_is_zero_percent_not_an_error(db):
    u, s = _ctx(db)
    empty = Course(id="empty", title="t", level=CourseLevel.beginner, stage_tags=(), lessons=())
    assert course_progress(db, s.id, u.id, empty) == 0


def test_finishing_a_course_issues_one_certificate_event_and_job(db, monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.services.learning.service.event_bus.publish",
        lambda db, e, p: events.append((e, p)),
    )
    u, s = _ctx(db)
    certs = _finish(db, s, u, "build-scope-the-mvp")  # 2 lessons
    assert certs[0] is None and certs[1] is not None
    cert = certs[1]

    enrollment = db.query(Enrollment).filter_by(startup_id=s.id, user_id=u.id).one()
    assert enrollment.progress == 100 and enrollment.completed_at is not None
    assert events == [
        (
            "learning.course.completed",
            {
                "startup_id": str(s.id),
                "user_id": str(u.id),
                "course_id": "build-scope-the-mvp",
                "certificate_id": str(cert.id),
            },
        )
    ]
    jobs = db.query(Job).filter_by(type="learning.certificate.generate", startup_id=s.id).all()
    assert [job.payload for job in jobs] == [{"certificate_id": str(cert.id)}]

    # A repeat completion on an already-finished course issues nothing new.
    _enrollment, again = complete_lesson(db, s.id, u.id, "build-scope-the-mvp-2")
    assert again is None
    assert db.query(Certificate).filter_by(startup_id=s.id, user_id=u.id).count() == 1
    assert len(events) == 1
    job_count = (
        db.query(Job).filter_by(type="learning.certificate.generate", startup_id=s.id).count()
    )
    assert job_count == 1


def test_credential_codes_are_unique_and_unguessable(db):
    u, s = _ctx(db)
    first = _finish(db, s, u, "build-scope-the-mvp")[-1]
    second = _finish(db, s, u, "launch-plan-go-to-market")[-1]
    assert first.credential_code != second.credential_code
    assert len(first.credential_code) >= 20  # secrets.token_urlsafe(16) -> 22 characters


def test_certificates_are_listed_newest_first_and_serialized(db):
    u, s = _ctx(db)
    older = _finish(db, s, u, "build-scope-the-mvp")[-1]
    newer = _finish(db, s, u, "launch-plan-go-to-market")[-1]
    assert [c.id for c in list_certificates(db, s.id, u.id)] == [newer.id, older.id]
    out = serialize_certificate(newer)
    assert out["id"] == str(newer.id)
    assert out["course_id"] == "launch-plan-go-to-market"
    assert out["credential_code"] == newer.credential_code


def test_progress_is_per_person(db):
    u, s = _ctx(db)
    teammate = create_user(db)
    create_membership(db, teammate, s, role=MembershipRole.team_member)
    db.flush()
    complete_lesson(db, s.id, teammate.id, "idea-shape-the-problem-1")
    assert course_progress(db, s.id, u.id, COURSES["idea-shape-the-problem"]) == 0
    assert list_certificates(db, s.id, u.id) == []


def test_same_person_progresses_separately_in_two_workspaces(db):
    u, first = _ctx(db)
    second = create_startup(db, owner=u, name="Second")
    create_membership(db, u, second)
    db.flush()
    complete_lesson(db, first.id, u.id, "idea-shape-the-problem-1")
    course = COURSES["idea-shape-the-problem"]
    assert course_progress(db, first.id, u.id, course) == 33
    assert course_progress(db, second.id, u.id, course) == 0


def test_serialize_enrollment(db):
    u, s = _ctx(db)
    enrollment, _created = get_or_create_enrollment(db, s.id, u.id, "idea-shape-the-problem")
    assert serialize_enrollment(enrollment) == {
        "course_id": "idea-shape-the-problem",
        "progress": 0,
        "completed_at": None,
    }
