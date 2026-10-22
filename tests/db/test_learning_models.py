from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.learning import Certificate, Enrollment, LessonProgress
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    return u, s


def test_enrollment_defaults(db):
    u, s = _ctx(db)
    enrollment = Enrollment(startup_id=s.id, user_id=u.id, course_id="c")
    db.add(enrollment)
    db.flush()
    db.refresh(enrollment)
    assert enrollment.progress == 0
    assert enrollment.completed_at is None


def test_enrollment_is_unique_per_workspace_user_and_course(db):
    u, s = _ctx(db)
    db.add(Enrollment(startup_id=s.id, user_id=u.id, course_id="c"))
    db.flush()
    db.add(Enrollment(startup_id=s.id, user_id=u.id, course_id="c"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_same_person_can_enrol_in_the_same_course_in_two_workspaces(db):
    u, first = _ctx(db)
    second = create_startup(db, owner=u, name="Second")
    db.add(Enrollment(startup_id=first.id, user_id=u.id, course_id="c"))
    db.add(Enrollment(startup_id=second.id, user_id=u.id, course_id="c"))
    db.flush()


def test_enrollment_progress_must_be_between_0_and_100(db):
    u, s = _ctx(db)
    db.add(Enrollment(startup_id=s.id, user_id=u.id, course_id="c", progress=101))
    with pytest.raises(IntegrityError):
        db.flush()


def test_a_lesson_is_completed_once_per_person_per_workspace(db):
    u, s = _ctx(db)
    now = datetime.now(UTC)
    db.add(
        LessonProgress(
            startup_id=s.id, user_id=u.id, course_id="c", lesson_id="c-1", completed_at=now
        )
    )
    db.flush()
    db.add(
        LessonProgress(
            startup_id=s.id, user_id=u.id, course_id="c", lesson_id="c-1", completed_at=now
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_one_certificate_per_course_per_person_per_workspace(db):
    u, s = _ctx(db)
    now = datetime.now(UTC)
    db.add(
        Certificate(
            startup_id=s.id,
            user_id=u.id,
            course_id="c",
            credential_code="a",
            issued_at=now,
            holder_name="A",
            verify_code="CFZ-AAAA-AAAA-AAAA",
        )
    )
    db.flush()
    db.add(
        Certificate(
            startup_id=s.id,
            user_id=u.id,
            course_id="c",
            credential_code="b",
            issued_at=now,
            holder_name="A",
            verify_code="CFZ-BBBB-BBBB-BBBB",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_learning_recommendation_persists(db):
    from app.db.models.enums import EnrichmentStatus, StartupStage
    from app.db.models.learning import LearningRecommendation

    s = create_startup(db, owner=create_user(db))
    row = LearningRecommendation(
        startup_id=s.id,
        course_id="build-scope-the-mvp",
        context_key="abc123",
        stage=StartupStage.build.value,
        reason="Recommended for your build stage.",
        status=EnrichmentStatus.generating,
    )
    db.add(row)
    db.flush()
    got = db.query(LearningRecommendation).filter_by(startup_id=s.id).one()
    assert got.status == EnrichmentStatus.generating
    assert got.reason == "Recommended for your build stage."


def test_credential_codes_are_unique(db):
    u, s = _ctx(db)
    now = datetime.now(UTC)
    db.add(
        Certificate(
            startup_id=s.id,
            user_id=u.id,
            course_id="c1",
            credential_code="same",
            issued_at=now,
            holder_name="A",
            verify_code="CFZ-CCCC-CCCC-CCCC",
        )
    )
    db.flush()
    db.add(
        Certificate(
            startup_id=s.id,
            user_id=u.id,
            course_id="c2",
            credential_code="same",
            issued_at=now,
            holder_name="A",
            verify_code="CFZ-DDDD-DDDD-DDDD",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_verify_codes_are_unique(db):
    u, s = _ctx(db)
    now = datetime.now(UTC)
    for n, course in enumerate(("c1", "c2")):
        db.add(
            Certificate(
                startup_id=s.id,
                user_id=u.id,
                course_id=course,
                credential_code=f"code-{n}",
                issued_at=now,
                holder_name="A",
                verify_code="CFZ-SAME-SAME-SAME",
            )
        )
    with pytest.raises(IntegrityError):
        db.flush()


def test_one_reason_per_hero_course_per_workspace(db):
    from app.db.models.learning import LearningRecommendation

    s = create_startup(db, owner=create_user(db))
    for course in ("a", "a"):
        db.add(LearningRecommendation(startup_id=s.id, course_id=course, context_key="k"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_a_note_and_a_position_are_unique_per_user_and_lesson(db):
    from app.db.models.learning import LessonNote

    u, s = _ctx(db)
    db.add(LessonNote(startup_id=s.id, user_id=u.id, lesson_id="c-1", body="a"))
    db.flush()
    db.add(LessonNote(startup_id=s.id, user_id=u.id, lesson_id="c-1", body="b"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_a_playback_position_cannot_be_negative(db):
    from app.db.models.learning import LessonPosition

    u, s = _ctx(db)
    db.add(
        LessonPosition(startup_id=s.id, user_id=u.id, course_id="c", lesson_id="c-1", position_s=-1)
    )
    with pytest.raises(IntegrityError):
        db.flush()
