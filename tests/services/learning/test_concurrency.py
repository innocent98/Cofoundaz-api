"""Concurrency regression tests for Module 17's write paths.

Same rationale as tests/services/business/test_concurrency.py: the per-test `db` fixture wraps
everything in one savepoint that never commits, so it cannot reproduce a cross-connection race.
These tests open real, committing `Session`s against the session-scoped `engine` fixture.

Targets:
- `get_or_create_enrollment`: two concurrent enrols produce one row (SAVEPOINT + re-select).
- `complete_lesson` on the same lesson twice: one lesson_progress row and one enrolment.
- `complete_lesson` on two different lessons of an unenrolled two-lesson course: one enrolment,
  both lessons recorded, progress 100 and exactly one certificate. The enrolment row lock makes
  the roll-up serial, so neither caller stores a stale percentage.
"""

import threading
import uuid
from collections.abc import Callable

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models.job import Job
from app.db.models.learning import Certificate, Enrollment, LessonProgress
from app.db.models.startup import Startup
from app.db.models.user import User
from app.services.learning.service import complete_lesson, get_or_create_enrollment
from tests.factories import create_startup, create_user


def _setup(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    setup = Session(bind=engine)
    try:
        user = create_user(setup, email=f"race-{uuid.uuid4().hex[:8]}@example.com")
        startup = create_startup(setup, owner=user)
        setup.commit()
        return startup.id, user.id
    finally:
        setup.close()


def _cleanup(engine: Engine, startup_id: uuid.UUID, user_id: uuid.UUID) -> None:
    cleanup = Session(bind=engine)
    try:
        # Jobs have no FK to startups, so they are removed explicitly.
        cleanup.query(Job).filter(Job.startup_id == startup_id).delete()
        # Cascades: startups -> enrollments, lesson_progress, certificates.
        cleanup.query(Startup).filter(Startup.id == startup_id).delete()
        cleanup.query(User).filter(User.id == user_id).delete()
        cleanup.commit()
    finally:
        cleanup.close()


def _race(engine: Engine, calls: list[Callable[[Session], object]]) -> list[BaseException | None]:
    barrier = threading.Barrier(len(calls))
    errors: list[BaseException | None] = []
    lock = threading.Lock()

    def run(call: Callable[[Session], object]) -> None:
        session = Session(bind=engine)
        outcome: BaseException | None
        try:
            barrier.wait(timeout=5)
            try:
                call(session)
            except BaseException as exc:  # noqa: BLE001 - captured for the assertion
                session.rollback()
                outcome = exc
            else:
                session.commit()
                outcome = None
        finally:
            session.close()
        with lock:
            errors.append(outcome)

    threads = [threading.Thread(target=run, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    return errors


def test_concurrent_enrolments_create_one_row(engine: Engine):
    startup_id, user_id = _setup(engine)

    def enrol(session: Session) -> object:
        return get_or_create_enrollment(session, startup_id, user_id, "idea-shape-the-problem")

    try:
        assert _race(engine, [enrol, enrol]) == [None, None]
        verify = Session(bind=engine)
        try:
            count = (
                verify.query(Enrollment).filter_by(startup_id=startup_id, user_id=user_id).count()
            )
            assert count == 1, "exactly one enrolment, not one per racer"
        finally:
            verify.close()
    finally:
        _cleanup(engine, startup_id, user_id)


def test_concurrent_completions_of_the_same_lesson_record_it_once(engine: Engine):
    startup_id, user_id = _setup(engine)

    def complete(session: Session) -> object:
        return complete_lesson(session, startup_id, user_id, "idea-shape-the-problem-1")

    try:
        assert _race(engine, [complete, complete]) == [None, None]
        verify = Session(bind=engine)
        try:
            scope = {"startup_id": startup_id, "user_id": user_id}
            assert verify.query(LessonProgress).filter_by(**scope).count() == 1
            enrollments = verify.query(Enrollment).filter_by(**scope).all()
            assert len(enrollments) == 1
            assert enrollments[0].progress == 33
        finally:
            verify.close()
    finally:
        _cleanup(engine, startup_id, user_id)


def test_concurrent_completions_of_different_lessons_enrol_once_and_finish(engine: Engine):
    startup_id, user_id = _setup(engine)

    def first(session: Session) -> object:
        return complete_lesson(session, startup_id, user_id, "build-scope-the-mvp-1")

    def second(session: Session) -> object:
        return complete_lesson(session, startup_id, user_id, "build-scope-the-mvp-2")

    try:
        assert _race(engine, [first, second]) == [None, None]
        verify = Session(bind=engine)
        try:
            scope = {"startup_id": startup_id, "user_id": user_id}
            enrollments = verify.query(Enrollment).filter_by(**scope).all()
            assert len(enrollments) == 1, "exactly one enrolment, not one per racer"
            assert enrollments[0].progress == 100, "the roll-up must see both completions"
            assert enrollments[0].completed_at is not None
            assert verify.query(LessonProgress).filter_by(**scope).count() == 2
            assert verify.query(Certificate).filter_by(**scope).count() == 1
        finally:
            verify.close()
    finally:
        _cleanup(engine, startup_id, user_id)
