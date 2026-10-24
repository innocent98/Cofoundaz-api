import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.learning import (
    AcademyArticle,
    AcademyCourse,
    AcademyLesson,
    AcademyPath,
    Certificate,
    CertificateFile,
    Enrollment,
    LessonNote,
    LessonPosition,
    LessonProgress,
)
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.learning import certificates, recommendations
from app.services.learning.catalog import (
    course_duration_min,
    get_course,
    get_lesson,
    list_articles,
    list_courses,
)
from app.services.learning.content import video_payload

RELATED_LIMIT = 3


# --- the learner's state ------------------------------------------------------------------


@dataclass
class LearnerState:
    """Everything one learner has done in one workspace, loaded in three queries."""

    enrollments: dict[str, Enrollment] = field(default_factory=dict)
    done: dict[str, set[str]] = field(default_factory=dict)  # course_id -> completed lesson ids
    positions: dict[str, LessonPosition] = field(default_factory=dict)  # lesson_id -> row
    touched: dict[str, tuple[datetime, str]] = field(
        default_factory=dict
    )  # course -> (when, lesson)


def load_state(db: Session, startup_id: uuid.UUID, user_id: uuid.UUID) -> LearnerState:
    state = LearnerState()
    for enrollment in db.query(Enrollment).filter_by(startup_id=startup_id, user_id=user_id):
        state.enrollments[enrollment.course_id] = enrollment

    def touch(course_id: str, lesson_id: str, when: datetime) -> None:
        current = state.touched.get(course_id)
        if current is None or when > current[0]:
            state.touched[course_id] = (when, lesson_id)

    for done in db.query(LessonProgress).filter_by(startup_id=startup_id, user_id=user_id):
        state.done.setdefault(done.course_id, set()).add(done.lesson_id)
        touch(done.course_id, done.lesson_id, done.completed_at)
    for pos in db.query(LessonPosition).filter_by(startup_id=startup_id, user_id=user_id):
        state.positions[pos.lesson_id] = pos
        touch(pos.course_id, pos.lesson_id, pos.updated_at)
    return state


# --- enrolment and progress ---------------------------------------------------------------


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
    get_course(db, course_id)
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


def course_progress(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course: AcademyCourse
) -> int:
    """Course % = round(100 x lessons completed / total lessons), spec section 5.

    Counts only this caller's completions in this workspace, and only lessons still in the
    catalog. ``round`` rounds exact halves to even, matching ``recompute_milestone_progress``.
    """
    lesson_ids = [lesson.id for lesson in course.lessons]
    if not lesson_ids:
        return 0
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
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course: AcademyCourse
) -> Certificate:
    cert = certificates.issue_certificate(db, startup_id, user_id, course.id)
    event_bus.publish(
        db,
        "learning.course.completed",
        {
            "startup_id": str(startup_id),
            "user_id": str(user_id),
            "course_id": course.id,
            "course_title": course.title,
            "certificate_id": str(cert.id),
        },
    )
    job_dispatcher.enqueue(
        db, "learning.certificate.generate", {"certificate_id": str(cert.id)}, startup_id
    )
    return cert


def save_position(
    db: Session,
    startup_id: uuid.UUID,
    user_id: uuid.UUID,
    course_id: str,
    lesson_id: str,
    position_s: int,
) -> None:
    """Remember where the learner paused. One row per lesson, upserted; never shared."""
    row = (
        db.query(LessonPosition)
        .filter_by(startup_id=startup_id, user_id=user_id, lesson_id=lesson_id)
        .first()
    )
    if row is None:
        try:
            with db.begin_nested():
                db.add(
                    LessonPosition(
                        startup_id=startup_id,
                        user_id=user_id,
                        course_id=course_id,
                        lesson_id=lesson_id,
                        position_s=position_s,
                    )
                )
                db.flush()
            return
        except IntegrityError:
            row = (
                db.query(LessonPosition)
                .filter_by(startup_id=startup_id, user_id=user_id, lesson_id=lesson_id)
                .one()
            )
    row.position_s = position_s
    row.updated_at = datetime.now(UTC)
    db.flush()


def update_lesson_progress(
    db: Session,
    startup_id: uuid.UUID,
    user_id: uuid.UUID,
    lesson_id: str,
    *,
    completed: bool,
    position_s: int | None,
) -> tuple[Enrollment, Certificate | None, int | None]:
    """Mark a lesson complete and/or remember the playback position (D-ACA-4).

    Lessons may be taken in any order. The caller is enrolled automatically if needed (spec D9).
    Returns ``(enrollment, certificate, position_s)``; ``certificate`` is set only when this call
    took the course to 100%. Idempotent: a repeat completion changes nothing and issues nothing.
    """
    course, _lesson = get_lesson(db, lesson_id)
    enrollment, _created = get_or_create_enrollment(db, startup_id, user_id, course.id)
    if position_s is not None:
        save_position(db, startup_id, user_id, course.id, lesson_id, position_s)
    certificate: Certificate | None = None
    if completed:
        # Lock the enrolment row so concurrent completions in the same course roll up one at a
        # time. Without it, two callers could each count only their own lesson and store a stale
        # percentage -- the drift the spec forbids.
        enrollment = (
            db.query(Enrollment)
            .filter_by(id=enrollment.id)
            .with_for_update()
            .populate_existing()
            .one()
        )
        _record_lesson(db, startup_id, user_id, course.id, lesson_id)
        enrollment.progress = course_progress(db, startup_id, user_id, course)
        if enrollment.progress == 100 and enrollment.completed_at is None:
            enrollment.completed_at = datetime.now(UTC)
            certificate = _issue_certificate(db, startup_id, user_id, course)
    db.flush()
    return enrollment, certificate, position_s


# --- private notes ------------------------------------------------------------------------


def get_note(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, lesson_id: str
) -> dict[str, Any]:
    """The caller's own note for a lesson. There is no way to ask for anyone else's."""
    get_lesson(db, lesson_id)  # 404 for an unknown lesson
    row = (
        db.query(LessonNote)
        .filter_by(startup_id=startup_id, user_id=user_id, lesson_id=lesson_id)
        .first()
    )
    return serialize_note(lesson_id, row)


def put_note(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, lesson_id: str, body: str
) -> dict[str, Any]:
    get_lesson(db, lesson_id)
    row = (
        db.query(LessonNote)
        .filter_by(startup_id=startup_id, user_id=user_id, lesson_id=lesson_id)
        .first()
    )
    if row is None:
        try:
            with db.begin_nested():
                row = LessonNote(
                    startup_id=startup_id, user_id=user_id, lesson_id=lesson_id, body=body
                )
                db.add(row)
                db.flush()
        except IntegrityError:
            row = (
                db.query(LessonNote)
                .filter_by(startup_id=startup_id, user_id=user_id, lesson_id=lesson_id)
                .one()
            )
            row.body = body
    else:
        row.body = body
    row.updated_at = datetime.now(UTC)
    db.flush()
    return serialize_note(lesson_id, row)


def serialize_note(lesson_id: str, row: LessonNote | None) -> dict[str, Any]:
    return {
        "lesson_id": lesson_id,
        "body": row.body if row is not None else "",
        "updated_at": row.updated_at.isoformat() if row is not None else None,
    }


# --- certificates -------------------------------------------------------------------------


def certificate_views(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = certificates.list_certificates(db, startup_id, user_id)
    if not rows:
        return []
    ready = {
        r[0]
        for r in db.query(CertificateFile.certificate_id).filter(
            CertificateFile.certificate_id.in_([c.id for c in rows])
        )
    }
    titles = {c.id: c.title for c in db.query(AcademyCourse).all()}
    return [
        certificates.serialize_certificate(c, titles.get(c.course_id), pdf_ready=c.id in ready)
        for c in rows
    ]


def certificate_view(db: Session, cert: Certificate) -> dict[str, Any]:
    course = db.get(AcademyCourse, cert.course_id)
    ready = db.get(CertificateFile, cert.id) is not None
    return certificates.serialize_certificate(
        cert, course.title if course is not None else None, pdf_ready=ready
    )


# --- views --------------------------------------------------------------------------------


def serialize_enrollment(enrollment: Enrollment) -> dict[str, Any]:
    return {
        "course_id": enrollment.course_id,
        "progress": enrollment.progress,
        "completed_at": (enrollment.completed_at.isoformat() if enrollment.completed_at else None),
    }


def _next_lesson(course: AcademyCourse, state: LearnerState) -> str | None:
    """Where Continue opens: the lesson last touched if unfinished, else the first unfinished."""
    done = state.done.get(course.id, set())
    unfinished = [lesson.id for lesson in course.lessons if lesson.id not in done]
    if not unfinished:
        return None
    last = state.touched.get(course.id)
    if last is not None and last[1] in unfinished:
        return last[1]
    return unfinished[0]


def course_summary(course: AcademyCourse, state: LearnerState) -> dict[str, Any]:
    enrollment = state.enrollments.get(course.id)
    lesson_ids = {lesson.id for lesson in course.lessons}
    touched = state.touched.get(course.id)
    last_lesson_id = touched[1] if touched is not None and touched[1] in lesson_ids else None
    next_lesson_id = _next_lesson(course, state)
    position = state.positions.get(next_lesson_id) if next_lesson_id else None
    return {
        "id": course.id,
        "title": course.title,
        "summary": course.summary,
        "level": course.level.value,
        "stage_tags": list(course.stage_tags),
        "lesson_count": len(course.lessons),
        "duration_min": course_duration_min(course),
        "review_status": course.review_status.value,
        "enrolled": enrollment is not None,
        "progress": enrollment.progress if enrollment is not None else 0,
        "completed": enrollment is not None and enrollment.completed_at is not None,
        "last_lesson_id": last_lesson_id,
        "next_lesson_id": next_lesson_id,
        "position_s": position.position_s if position is not None else 0,
    }


def _lesson_view(lesson: AcademyLesson, state: LearnerState) -> dict[str, Any]:
    position = state.positions.get(lesson.id)
    return {
        "id": lesson.id,
        "title": lesson.title,
        "order": lesson.position,
        "duration_min": lesson.duration_min,
        "video": video_payload(lesson.video_id),
        "transcript": lesson.transcript,
        "completed": lesson.id in state.done.get(lesson.course_id, set()),
        "position_s": position.position_s if position is not None else 0,
    }


def course_detail(course: AcademyCourse, state: LearnerState) -> dict[str, Any]:
    return {
        **course_summary(course, state),
        "description": course.description,
        "outcomes": list(course.outcomes),
        "lessons": [_lesson_view(lesson, state) for lesson in course.lessons],
    }


def path_view(
    path: AcademyPath, courses: dict[str, AcademyCourse], state: LearnerState
) -> dict[str, Any]:
    members = [courses[cid] for cid in path.course_ids if cid in courses]
    progress = {
        c.id: (state.enrollments[c.id].progress if c.id in state.enrollments else 0)
        for c in members
    }
    return {
        "id": path.id,
        "title": path.title,
        "summary": path.summary,
        "stage": path.stage,
        "review_status": path.review_status.value,
        "course_ids": [c.id for c in members],
        "course_count": len(members),
        "duration_min": sum(course_duration_min(c) for c in members),
        "progress": round(sum(progress.values()) / len(members)) if members else 0,
        "courses": [
            {
                "id": c.id,
                "title": c.title,
                "level": c.level.value,
                "duration_min": course_duration_min(c),
                "progress": progress[c.id],
                "completed": c.id in state.enrollments
                and state.enrollments[c.id].completed_at is not None,
                "next_lesson_id": _next_lesson(c, state),
            }
            for c in members
        ],
    }


def article_view(article: AcademyArticle) -> dict[str, Any]:
    return {
        "id": article.id,
        "title": article.title,
        "summary": article.summary,
        "tags": list(article.tags),
        "read_min": article.read_min,
        "review_status": article.review_status.value,
    }


def article_detail(db: Session, article: AcademyArticle) -> dict[str, Any]:
    """The reader view: the body plus related articles and courses (shared tags)."""
    tags = set(article.tags)
    others = [(len(tags & set(a.tags)), a) for a in list_articles(db) if a.id != article.id]
    related_articles = [a for score, a in sorted(others, key=lambda p: -p[0]) if score > 0]
    scored = [(len(tags & (set(c.gap_tags) | set(c.stage_tags))), c) for c in list_courses(db)]
    related_courses = [c for score, c in sorted(scored, key=lambda p: -p[0]) if score > 0]
    return {
        **article_view(article),
        "body": article.body,
        "related": {
            "articles": [article_view(a) for a in related_articles[:RELATED_LIMIT]],
            "courses": [
                {
                    "id": c.id,
                    "title": c.title,
                    "summary": c.summary,
                    "level": c.level.value,
                    "duration_min": course_duration_min(c),
                }
                for c in related_courses[:RELATED_LIMIT]
            ],
        },
    }


def continue_watching(courses: list[AcademyCourse], state: LearnerState) -> list[AcademyCourse]:
    """Started, not finished: some progress or a saved position. Most recently touched first."""
    started = [
        c
        for c in courses
        if c.id in state.enrollments
        and state.enrollments[c.id].completed_at is None
        and (state.enrollments[c.id].progress > 0 or c.id in state.touched)
    ]
    return sorted(
        started,
        key=lambda c: (
            state.touched[c.id][0] if c.id in state.touched else datetime.min.replace(tzinfo=UTC)
        ),
        reverse=True,
    )


def build_recommendations(
    db: Session, startup: Startup, user_id: uuid.UUID, state: LearnerState
) -> dict[str, Any]:
    """The Learn next payload. Flushes the reason row; the caller commits."""
    courses = list_courses(db)
    completed = {cid for cid, e in state.enrollments.items() if e.completed_at is not None}
    gaps = recommendations.gather_gaps(db, startup.id)
    shelf = recommendations.rank_courses(courses, startup.stage, completed, gaps)
    hero: dict[str, Any] | None = None
    reason: str
    if shelf:
        row = recommendations.hero_reason(
            db,
            startup_id=startup.id,
            user_id=user_id,
            stage=startup.stage,
            hero=shelf[0],
            gaps=gaps,
        )
        hero = recommendations.hero_view(row, shelf[0], gaps)
        reason = row.reason or ""
    else:
        reason = recommendations.templated_reason(startup.stage, None, [])
    return {
        "stage": startup.stage.value if startup.stage is not None else None,
        "recommendation_reason": reason,
        "hero": hero,
        "recommended": [course_summary(c, state) for c in shelf],
        "continue_watching": [course_summary(c, state) for c in continue_watching(courses, state)],
    }
