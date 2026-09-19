from datetime import date

from app.db.models.assessment import AssessmentResult
from app.db.models.dashboard import DailyBriefing
from app.db.models.enums import AssessmentStatus, BriefingStatus
from app.db.models.job import Job
from app.services.dashboard.service import get_or_generate_briefing, get_summary
from tests.factories import create_assessment, create_startup, create_user


def _complete_assessment(db, startup):
    a = create_assessment(db, startup, status=AssessmentStatus.completed)
    db.add(
        AssessmentResult(
            assessment_id=a.id,
            dimension_scores={"product": 30, "market": 30, "money": 30, "legal": 30, "team": 30},
            overall_provisional=30,
            narrative="n",
        )
    )
    db.flush()


def _jobs(db):
    return db.query(Job).filter(Job.type == "ai.dashboard.briefing").count()


def test_no_briefing_without_assessment(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    assert get_or_generate_briefing(db, s) is None
    assert db.query(DailyBriefing).filter_by(startup_id=s.id).count() == 0
    assert _jobs(db) == 0


def test_generates_and_enqueues_once_with_assessment(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _complete_assessment(db, s)
    row = get_or_generate_briefing(db, s)
    assert row is not None and row.status == BriefingStatus.generating
    assert (
        db.query(DailyBriefing).filter_by(startup_id=s.id, briefing_date=date.today()).count() == 1
    )
    assert _jobs(db) == 1
    # second same-day call returns the same row, no new job
    again = get_or_generate_briefing(db, s)
    assert again.id == row.id
    assert _jobs(db) == 1


def test_get_summary_blocks_reflect_state(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    # no assessment -> static empty block
    summary = get_summary(db, s, u)
    assert summary["briefing"]["status"] == "empty"
    # with assessment -> generating block
    _complete_assessment(db, s)
    summary = get_summary(db, s, u)
    assert summary["briefing"]["status"] == "generating"
    assert summary["risks"]["status"] == "generating"
    assert summary["opportunities"]["status"] == "generating"
    # flip to ready -> ready block with the stored text
    row = db.query(DailyBriefing).filter_by(startup_id=s.id, briefing_date=date.today()).one()
    row.briefing, row.risks, row.opportunities = "B", "R", "O"
    row.status = BriefingStatus.ready
    db.flush()
    summary = get_summary(db, s, u)
    assert summary["briefing"] == {"status": "ready", "message": "B"}
    assert summary["opportunities"] == {"status": "ready", "message": "O"}
