import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.enums import CourseLevel, StartupStage
from app.db.models.learning import Certificate, Enrollment, LessonProgress
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.learning.catalog import (
    ALL_COURSES,
    COURSES,
    Article,
    Course,
    LearningPath,
    course_duration_min,
    get_course,
    get_lesson,
    path_duration_min,
)


def _enrollment(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course_id: str
) -> Enrollment | None:
    return (
        db.query(Enrollment)
        .filter_by(startup_id=startup_id, user_id=user_id, course_id=course_id)
        .first()
    )


def get_or_create_enrollment(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course_id: str
) -> tuple[Enrollment, bool]:
    """Fetch or create the enrolment for ``(startup_id, user_id, course_id)``.

    Returns ``(enrollment, created)``; an unknown course raises ``NotFound``. An unguarded
    check-then-INSERT would let two concurrent callers both insert, and the loser's flush would
    raise ``IntegrityError`` against ``uq_enrollments_startup_user_course``. Mirrors
    ``get_or_create_canvas``: insert inside a SAVEPOINT, and on ``IntegrityError`` re-select the
    winner's now-committed row.
    """
    get_course(course_id)
    row = _enrollment(db, startup_id, user_id, course_id)
    if row is not None:
        return row, False
    try:
        with db.begin_nested():
            row = Enrollment(
                startup_id=startup_id, user_id=user_id, course_id=course_id, progress=0
            )
            db.add(row)
            db.flush()
    except IntegrityError:
        # A concurrent caller won the race -- their row is now committed and visible.
        existing = (
            db.query(Enrollment)
            .filter_by(startup_id=startup_id, user_id=user_id, course_id=course_id)
            .one()
        )
        return existing, False
    return row, True


def course_progress(db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course: Course) -> int:
    """Course % = round(100 x lessons completed / total lessons), spec section 5.

    Counts only this caller's completions in this workspace, and only lessons still in the
    catalog. ``round`` rounds exact halves to even, matching ``recompute_milestone_progress``.
    """
    lesson_ids = [lesson.id for lesson in course.lessons]
    done = (
        db.query(LessonProgress)
        .filter_by(startup_id=startup_id, user_id=user_id)
        .filter(LessonProgress.lesson_id.in_(lesson_ids))
        .count()
    )
    return round(100 * done / len(lesson_ids))


def _record_lesson(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course_id: str, lesson_id: str
) -> None:
    # Called only while holding the enrolment row lock, so concurrent completions of the same
    # lesson are serialised and the existence check cannot race.
    exists = (
        db.query(LessonProgress.id)
        .filter_by(startup_id=startup_id, user_id=user_id, lesson_id=lesson_id)
        .first()
    )
    if exists is not None:
        return
    db.add(
        LessonProgress(
            startup_id=startup_id,
            user_id=user_id,
            course_id=course_id,
            lesson_id=lesson_id,
            completed_at=datetime.now(UTC),
        )
    )
    db.flush()


def _issue_certificate(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course_id: str
) -> Certificate:
    cert = Certificate(
        startup_id=startup_id,
        user_id=user_id,
        course_id=course_id,
        credential_code=secrets.token_urlsafe(16),
        issued_at=datetime.now(UTC),
    )
    db.add(cert)
    db.flush()
    event_bus.publish(
        "learning.course.completed",
        {
            "startup_id": str(startup_id),
            "user_id": str(user_id),
            "course_id": course_id,
            "certificate_id": str(cert.id),
        },
    )
    job_dispatcher.enqueue(
        db, "learning.certificate.generate", {"certificate_id": str(cert.id)}, startup_id
    )
    return cert


def complete_lesson(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, lesson_id: str
) -> tuple[Enrollment, Certificate | None]:
    """Mark a lesson complete, enrolling the caller automatically if needed (spec D9).

    Returns ``(enrollment, certificate)``; ``certificate`` is set only when this call took the
    course to 100%. Idempotent: a repeat completion changes nothing and issues nothing.
    """
    course, _lesson = get_lesson(lesson_id)
    enrollment, _created = get_or_create_enrollment(db, startup_id, user_id, course.id)
    # Lock the enrolment row so concurrent completions in the same course roll up one at a time.
    # Without it, two callers could each count only their own lesson and store a stale
    # percentage -- the drift the spec forbids.
    enrollment = (
        db.query(Enrollment).filter_by(id=enrollment.id).with_for_update().populate_existing().one()
    )
    _record_lesson(db, startup_id, user_id, course.id, lesson_id)
    enrollment.progress = course_progress(db, startup_id, user_id, course)
    certificate: Certificate | None = None
    if enrollment.progress == 100 and enrollment.completed_at is None:
        enrollment.completed_at = datetime.now(UTC)
        certificate = _issue_certificate(db, startup_id, user_id, course.id)
    db.flush()
    return enrollment, certificate


def list_certificates(db: Session, startup_id: uuid.UUID, user_id: uuid.UUID) -> list[Certificate]:
    return (
        db.query(Certificate)
        .filter_by(startup_id=startup_id, user_id=user_id)
        .order_by(Certificate.issued_at.desc())
        .all()
    )


def serialize_enrollment(enrollment: Enrollment) -> dict[str, Any]:
    return {
        "course_id": enrollment.course_id,
        "progress": enrollment.progress,
        "completed_at": (enrollment.completed_at.isoformat() if enrollment.completed_at else None),
    }


def serialize_certificate(cert: Certificate) -> dict[str, Any]:
    return {
        "id": str(cert.id),
        "course_id": cert.course_id,
        "credential_code": cert.credential_code,
        "issued_at": cert.issued_at.isoformat(),
    }


def enrollments_by_course(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Enrollment]:
    rows = db.query(Enrollment).filter_by(startup_id=startup_id, user_id=user_id).all()
    return {row.course_id: row for row in rows}


def completed_lesson_ids(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course_id: str
) -> set[str]:
    rows = (
        db.query(LessonProgress.lesson_id)
        .filter_by(startup_id=startup_id, user_id=user_id, course_id=course_id)
        .all()
    )
    return {row.lesson_id for row in rows}


_LEVEL_RANK: dict[CourseLevel, int] = {
    CourseLevel.beginner: 0,
    CourseLevel.intermediate: 1,
    CourseLevel.advanced: 2,
}
_CATALOG_POSITION: dict[str, int] = {course.id: i for i, course in enumerate(ALL_COURSES)}

# Applied in turn. Stage match is the inclusion filter in ``recommended_courses``; the Health
# Score signal (spec D2) is added later as one more key here, without rewriting the ranker.
RECOMMENDATION_SORT_KEYS: tuple[Callable[[Course], int], ...] = (
    lambda course: _LEVEL_RANK[course.level],
    lambda course: _CATALOG_POSITION[course.id],
)


def recommended_courses(stage: StartupStage | None, completed_ids: set[str]) -> list[Course]:
    """Deterministic shelf, spec section 5.

    Stage match; completed courses excluded; beginner first; catalog order on ties. With no
    stage set, beginner courses from every stage.
    """
    if stage is None:
        candidates = [c for c in ALL_COURSES if c.level == CourseLevel.beginner]
    else:
        candidates = [c for c in ALL_COURSES if stage in c.stage_tags]
    remaining = [c for c in candidates if c.id not in completed_ids]
    return sorted(remaining, key=lambda c: tuple(key(c) for key in RECOMMENDATION_SORT_KEYS))


def continue_watching(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID
) -> list[tuple[Course, Enrollment]]:
    """Enrolled, not-completed courses for this caller in this workspace, most recent first."""
    rows = (
        db.query(Enrollment)
        .filter_by(startup_id=startup_id, user_id=user_id)
        .filter(Enrollment.completed_at.is_(None))
        .order_by(Enrollment.updated_at.desc(), Enrollment.course_id)
        .all()
    )
    return [(COURSES[row.course_id], row) for row in rows if row.course_id in COURSES]


def path_progress(path: LearningPath, progress: dict[str, int]) -> int:
    """Path % = round(mean of its courses' course %); unenrolled courses count as 0."""
    values = [progress.get(course_id, 0) for course_id in path.course_ids]
    return round(sum(values) / len(values))


def course_summary(course: Course, enrollment: Enrollment | None) -> dict[str, Any]:
    return {
        "id": course.id,
        "title": course.title,
        "level": course.level.value,
        "stage_tags": [stage.value for stage in course.stage_tags],
        "lesson_count": len(course.lessons),
        "duration_min": course_duration_min(course),
        "enrolled": enrollment is not None,
        "progress": enrollment.progress if enrollment is not None else 0,
        "completed": enrollment is not None and enrollment.completed_at is not None,
    }


def course_detail(
    course: Course, enrollment: Enrollment | None, completed_ids: set[str]
) -> dict[str, Any]:
    return {
        **course_summary(course, enrollment),
        "lessons": [
            {
                "id": lesson.id,
                "title": lesson.title,
                "order": lesson.order,
                "video_ref": lesson.video_ref,
                "duration_min": lesson.duration_min,
                "transcript": lesson.transcript,
                "completed": lesson.id in completed_ids,
            }
            for lesson in course.lessons
        ],
    }


def path_view(path: LearningPath, progress: dict[str, int]) -> dict[str, Any]:
    return {
        "id": path.id,
        "title": path.title,
        "stage": path.stage.value,
        "course_ids": list(path.course_ids),
        "course_count": len(path.course_ids),
        "duration_min": path_duration_min(path),
        "progress": path_progress(path, progress),
    }


def article_view(article: Article) -> dict[str, Any]:
    return {
        "id": article.id,
        "title": article.title,
        "tags": list(article.tags),
        "body": article.body,
    }
