"""Concurrency regression test for the start_or_resume double-start race.

Same rationale as tests/services/assessment/test_answers_concurrency.py and
test_complete_concurrency.py: the per-test `db` fixture wraps everything in one
savepoint that never commits, so it can't reproduce a cross-connection race. This test
opens its own real, committing `Session`s against the session-scoped `engine` fixture's
connection pool instead.

Regression target: the original start_or_resume implementation queried for an existing
in_progress Assessment, then INSERTed if none was found. Two concurrent POST
/assessments for the SAME startup+founder both pass the "no in_progress assessment yet"
check, both INSERT -- and the loser trips the partial unique index
uq_assessments_startup_in_progress as an uncaught IntegrityError, surfacing as a 500 to
the client. The intended behavior is RESUME: the loser should transparently return the
winner's in_progress assessment, exactly like a caller that arrived after the winner had
already committed. This test fails against the naive read-then-insert version
(IntegrityError raised in one of the two threads / two Assessment rows) and passes
against the insert-or-get pattern (SAVEPOINT around the INSERT, catch the unique
violation, re-select the committed winner's row).
"""

import threading
import uuid

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models.assessment import Assessment
from app.db.models.startup import Startup
from app.db.models.user import User
from app.services.assessment.service import start_or_resume
from tests.factories import create_startup, create_user


def test_concurrent_start_of_same_startup_only_one_assessment(engine: Engine):
    setup = Session(bind=engine)
    startup_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    try:
        user = create_user(setup, email=f"race-{uuid.uuid4().hex[:8]}@example.com")
        startup = create_startup(setup, owner=user)
        setup.commit()
        startup_id = startup.id
        user_id = user.id

        barrier = threading.Barrier(2)
        results: list[tuple[uuid.UUID | None, BaseException | None]] = []
        results_lock = threading.Lock()

        def attempt() -> None:
            session = Session(bind=engine)
            outcome: tuple[uuid.UUID | None, BaseException | None]
            try:
                s = session.query(Startup).filter(Startup.id == startup_id).one()
                u = session.query(User).filter(User.id == user_id).one()
                barrier.wait(timeout=5)
                try:
                    result = start_or_resume(session, s, u)
                except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
                    session.rollback()
                    outcome = (None, exc)
                else:
                    session.commit()
                    outcome = (result.id, None)
            finally:
                session.close()
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(results) == 2, "both threads must finish"
        assert all(exc is None for _, exc in results), f"neither call should raise: {results}"

        r0, r1 = results[0][0], results[1][0]
        assert r0 is not None and r1 is not None
        assert r0 == r1, "both callers must resume the SAME assessment"

        verify = Session(bind=engine)
        try:
            assert (
                verify.query(Assessment).filter(Assessment.startup_id == startup_id).count() == 1
            ), "exactly one Assessment row, not one per racer"
        finally:
            verify.close()
    finally:
        cleanup = Session(bind=engine)
        try:
            if startup_id is not None:
                # Cascades: startups -> startup_profiles, assessments -> assessment_answers
                # and assessment_results.
                cleanup.query(Startup).filter(Startup.id == startup_id).delete()
            if user_id is not None:
                cleanup.query(User).filter(User.id == user_id).delete()
            cleanup.commit()
        finally:
            cleanup.close()
        setup.close()
