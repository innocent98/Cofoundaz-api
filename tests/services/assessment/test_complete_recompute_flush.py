"""Regression: complete_assessment must materialize the Health Score inline.

`complete_assessment` adds the `AssessmentResult` and then, in the same transaction,
calls `recompute_health_score`, whose first step (`latest_completed_result`) is a
`SELECT` joining Assessment -> AssessmentResult. Production `SessionLocal` is
`autoflush=False` (app/db/session.py), so without an explicit flush that SELECT never
sees the just-added-but-unflushed AssessmentResult: the recompute bails and NO HealthScore
row, HealthRecommendation rows, or `ai.health.recommendations` job are produced by the
`/complete` request itself (it was only masked by `get_overview`'s lazy-on-read recompute
on a *later* GET /health-score request).

The per-test `db` fixture defaults to `autoflush=True`, which silently flushes before that
SELECT and hides the bug -- so this regression MUST use its own `autoflush=False` session
(like production) or it would pass with or without the fix. It opens a non-committing
`autoflush=False` Session on the session-scoped `engine` and rolls back at the end for
isolation.
"""

import uuid

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models.assessment import Assessment
from app.db.models.enums import AssessmentStatus, AssessmentType, RecommendationStatus
from app.db.models.health_score import HealthRecommendation, HealthScore
from app.db.models.job import Job
from app.services.assessment.bank import ASSESSMENT_BANK
from app.services.assessment.service import complete_assessment
from tests.factories import create_answer, create_startup, create_user

# Weak answers -> low dimension scores -> pending recommendations are generated.
_WEAK_ANSWERS = [
    ("product_stage", "idea"),
    ("market_clarity", 3),
    ("market_research", "none"),
    ("has_revenue", "no"),
    ("runway_confidence", 3),
    ("incorporated", "no"),
    ("team_size", "solo"),
    ("team_confidence", 3),
]


def test_complete_assessment_materializes_health_inline(engine: Engine):
    """Immediately after complete_assessment returns -- no intervening GET, no manual
    flush -- the HealthScore, pending HealthRecommendation rows, and the
    ai.health.recommendations job must all exist. Reproduces the production
    autoflush=False condition; fails before the inline db.flush() fix, passes after."""
    session = Session(bind=engine, autoflush=False)
    try:
        user = create_user(session, email=f"flush-{uuid.uuid4().hex[:8]}@example.com")
        startup = create_startup(session, owner=user)
        assessment = Assessment(
            startup_id=startup.id,
            created_by=user.id,
            type=AssessmentType.initial,
            status=AssessmentStatus.in_progress,
            bank_version=ASSESSMENT_BANK.version,
        )
        session.add(assessment)
        session.flush()
        for key, value in _WEAK_ANSWERS:
            create_answer(session, assessment, question_key=key, value=value)
        session.flush()

        complete_assessment(session, assessment, startup)

        assert (
            session.query(HealthScore).filter_by(startup_id=startup.id).count() == 1
        ), "expected a HealthScore row from /complete itself, not only from a later GET"
        pending = (
            session.query(HealthRecommendation)
            .filter_by(startup_id=startup.id, status=RecommendationStatus.pending)
            .count()
        )
        assert pending > 0, "expected pending HealthRecommendation rows from /complete itself"
        assert (
            session.query(Job).filter(Job.type == "ai.health.recommendations").count() == 1
        ), "expected an ai.health.recommendations job enqueued from /complete itself"
    finally:
        session.rollback()
        session.close()
