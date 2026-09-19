"""Concurrency regression test for the complete_assessment atomic-claim TOCTOU race.

Same rationale as tests/services/assessment/test_answers_concurrency.py and
tests/services/auth/test_sessions_concurrency.py: the per-test `db` fixture wraps
everything in one savepoint that never commits, so it can't reproduce a cross-connection
race. This test opens its own real, committing `Session`s against the session-scoped
`engine` fixture's connection pool instead.

Regression target: complete_assessment claims the in_progress -> completed transition
with an UPDATE ... WHERE status = 'in_progress' conditioned on the very predicate it's
about to flip -- the same pattern as rotate_refresh (app/services/auth/sessions.py). Two
concurrent completions of the same fully-answered assessment must score, write the
AssessmentResult, recompute the Health Score, and enqueue the roadmap.replan job EXACTLY
ONCE. A plain read-then-write ("if assessment.status == in_progress: ...") would let both
callers pass the check before either commits, double-scoring and double-enqueuing/
double-recomputing. This test fails against that naive version (two AssessmentResult
rows / two roadmap.replan jobs / two HealthScoreHistory rows) and passes against the
atomic claim.

As of Module 06 the Health Score is recomputed inline within complete_assessment
instead of via a healthscore.recalculate job (see docs/sop/2026-08-19-health-score.md),
so this test also asserts exactly one HealthScore/HealthScoreHistory row rather than a
healthscore.recalculate job.
"""

import threading
import uuid

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models.assessment import Assessment, AssessmentResult
from app.db.models.enums import AssessmentStatus, AssessmentType
from app.db.models.health_score import HealthScore, HealthScoreHistory
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.events import event_bus
from app.services.assessment.bank import ASSESSMENT_BANK
from app.services.assessment.service import complete_assessment
from tests.factories import create_answer, create_startup, create_user

# Chosen to skip every conditional follow-up question, same set used by
# tests/api/assessment/test_answers.py::test_answer_after_bank_exhausted_422, so the
# assessment is fully (and minimally) answered.
_MINIMAL_ANSWERS = [
    ("product_stage", "idea"),
    ("market_clarity", 3),
    ("market_research", "none"),
    ("has_revenue", "no"),
    ("runway_confidence", 3),
    ("incorporated", "no"),
    ("team_size", "solo"),
    ("team_confidence", 3),
]


def test_concurrent_complete_of_same_assessment_only_one_scores(engine: Engine):
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
        setup.flush()
        for key, value in _MINIMAL_ANSWERS:
            create_answer(setup, assessment, question_key=key, value=value)
        setup.commit()
        startup_id = startup.id
        user_id = user.id
        assessment_id = assessment.id

        barrier = threading.Barrier(2)
        results: list[tuple[dict | None, bool | None, BaseException | None]] = []
        results_lock = threading.Lock()

        def attempt() -> None:
            session = Session(bind=engine)
            outcome: tuple[dict | None, bool | None, BaseException | None]
            try:
                a = session.query(Assessment).filter(Assessment.id == assessment_id).one()
                s = session.query(Startup).filter(Startup.id == startup_id).one()
                barrier.wait(timeout=5)
                try:
                    result, claimed = complete_assessment(session, a, s)
                except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
                    session.rollback()
                    outcome = (None, None, exc)
                else:
                    session.commit()
                    outcome = (result, claimed, None)
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
        assert all(exc is None for _, _, exc in results), f"neither call should raise: {results}"

        r0, r1 = results[0][0], results[1][0]
        assert r0 == r1, "both callers must observe the identical scored result"

        claimed0, claimed1 = results[0][1], results[1][1]
        assert sorted([claimed0, claimed1]) == [
            False,
            True,
        ], f"exactly one racer must claim the transition, got {[claimed0, claimed1]}"

        verify = Session(bind=engine)
        try:
            assert (
                verify.query(AssessmentResult).filter_by(assessment_id=assessment_id).count() == 1
            ), "exactly one AssessmentResult row, not one per racer"

            jobs = verify.query(Job).filter(Job.startup_id == startup_id).all()
            types = sorted(j.type for j in jobs)
            assert types == [
                "ai.assessment.narrative",
                "ai.health.recommendations",
                "roadmap.replan",
            ], f"expected exactly one of each completion job (not one per racer), got {types}"

            assert (
                verify.query(HealthScore).filter_by(startup_id=startup_id).count() == 1
            ), "exactly one HealthScore row, not one per racer"
            assert (
                verify.query(HealthScoreHistory).filter_by(startup_id=startup_id).count() == 1
            ), "exactly one HealthScoreHistory row (one recompute), not one per racer"

            completed_events = [
                e
                for e in event_bus.published
                if e[0] == "assessment.completed"
                and e[1].get("assessment_id") == str(assessment_id)
            ]
            assert (
                len(completed_events) == 1
            ), f"expected exactly one assessment.completed event, got {len(completed_events)}"
        finally:
            verify.close()
    finally:
        cleanup = Session(bind=engine)
        try:
            if startup_id is not None:
                cleanup.query(Job).filter(Job.startup_id == startup_id).delete()
                # Cascades: startups -> startup_profiles, assessments -> assessment_answers
                # and assessment_results.
                cleanup.query(Startup).filter(Startup.id == startup_id).delete()
            if user_id is not None:
                cleanup.query(User).filter(User.id == user_id).delete()
            cleanup.commit()
        finally:
            cleanup.close()
        setup.close()
