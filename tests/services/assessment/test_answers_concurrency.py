"""Concurrency regression test for the submit_answer upsert TOCTOU race.

Same rationale as tests/services/auth/test_sessions_concurrency.py: the per-test `db`
fixture wraps everything in one savepoint that never commits, so it can't reproduce a
cross-connection race. This test opens its own real, committing `Session`s against the
session-scoped `engine` fixture's connection pool instead.

Regression target: the original submit_answer implementation queried for an existing
AssessmentAnswer row, then INSERTed if none was found. Two requests racing to answer
the SAME current question both pass the "is this the current question" gate, both see
no existing row, both INSERT -- and the loser trips the (assessment_id, question_key)
unique constraint as an uncaught IntegrityError, surfacing as a 500 to the client. This
test fails against that old code (IntegrityError raised in one of the two threads) and
passes against the ON CONFLICT DO UPDATE upsert that replaced it.
"""

import threading
import uuid

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models.assessment import Assessment, AssessmentAnswer
from app.db.models.enums import AssessmentStatus, AssessmentType
from app.db.models.startup import Startup
from app.db.models.user import User
from app.services.assessment.bank import ASSESSMENT_BANK
from app.services.assessment.service import submit_answer
from tests.factories import create_startup, create_user


def test_concurrent_submit_of_same_current_question_only_one_row(engine: Engine):
    setup = Session(bind=engine)
    startup_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    try:
        user = create_user(setup, email=f"race-{uuid.uuid4().hex[:8]}@example.com")
        startup = create_startup(setup, owner=user)
        assessment = Assessment(
            startup_id=startup.id,
            created_by=user.id,
            type=AssessmentType.initial,
            status=AssessmentStatus.in_progress,
            bank_version=ASSESSMENT_BANK.version,
        )
        setup.add(assessment)
        setup.commit()
        startup_id = startup.id
        user_id = user.id
        assessment_id = assessment.id

        barrier = threading.Barrier(2)
        results: list[tuple[bool, BaseException | None]] = []
        results_lock = threading.Lock()

        def attempt(value: str) -> None:
            session = Session(bind=engine)
            outcome: tuple[bool, BaseException | None]
            try:
                a = session.query(Assessment).filter(Assessment.id == assessment_id).one()
                s = session.query(Startup).filter(Startup.id == startup_id).one()
                barrier.wait(timeout=5)
                try:
                    submit_answer(session, a, s, "product_stage", value)
                except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
                    session.rollback()
                    outcome = (False, exc)
                else:
                    session.commit()
                    outcome = (True, None)
            finally:
                session.close()
            with results_lock:
                results.append(outcome)

        threads = [
            threading.Thread(target=attempt, args=("mvp",)),
            threading.Thread(target=attempt, args=("prototype",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(results) == 2, "both threads must finish"
        assert all(ok for ok, _ in results), f"neither submit should raise: {results}"

        verify = Session(bind=engine)
        try:
            rows = (
                verify.query(AssessmentAnswer)
                .filter(
                    AssessmentAnswer.assessment_id == assessment_id,
                    AssessmentAnswer.question_key == "product_stage",
                )
                .all()
            )
            assert len(rows) == 1, f"expected exactly one row for the race, got {len(rows)}"
            assert rows[0].value_json in ("mvp", "prototype")
        finally:
            verify.close()
    finally:
        cleanup = Session(bind=engine)
        try:
            if startup_id is not None:
                # Cascades: startups -> startup_profiles, assessments -> assessment_answers.
                cleanup.query(Startup).filter(Startup.id == startup_id).delete()
            if user_id is not None:
                cleanup.query(User).filter(User.id == user_id).delete()
            cleanup.commit()
        finally:
            cleanup.close()
        setup.close()
