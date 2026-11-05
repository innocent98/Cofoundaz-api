from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.db.models.enums import MembershipRole
from app.db.models.job import Job
from app.db.models.readiness import MockSession
from app.services.readiness import qa
from app.worker import scheduler
from app.worker.handlers.scheduled import handle_qa_retention
from app.worker.scheduler import SCHED_QA_RETENTION, scheduler_tick
from tests.factories import create_membership, create_startup, create_user
from tests.qa_helpers import ScriptedLLM

NOW = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)


def test_due_item_is_one_global_job_per_local_day():
    [item] = scheduler._due_qa_retention(None, NOW)
    assert item.task_key == "readiness.qa_retention" and item.scope_key == "all"
    assert item.job_type == SCHED_QA_RETENTION == "scheduled.readiness.qa_retention"
    assert item.startup_id is None and item.payload == {}


def test_two_ticks_in_a_day_enqueue_once_and_the_next_day_again(db):
    scheduler_tick(db, now=NOW)
    scheduler_tick(db, now=NOW + timedelta(hours=3))
    assert db.query(Job).filter_by(type=SCHED_QA_RETENTION).count() == 1
    scheduler_tick(db, now=NOW + timedelta(days=1))
    assert db.query(Job).filter_by(type=SCHED_QA_RETENTION).count() == 2


def test_the_handler_clears_old_transcripts_and_keeps_scorecards(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    old = MockSession(
        startup_id=s.id,
        focus="general",
        intensity="friendly_angel",
        status="scored",
        transcript=[{"role": "founder", "text": "secret"}],
        context="numbers",
        turns=1,
        scorecard=qa.build_scorecard(
            ScriptedLLM().score,
            [
                {"role": "investor", "text": "What is your runway?"},
                {"role": "founder", "text": "secret"},
            ],
            [],
        ),
        rating="good",
        average=3.0,
        started_at=datetime.now(UTC) - timedelta(days=120),
    )
    db.add(old)
    db.flush()
    handle_qa_retention(db, SimpleNamespace(payload={}))
    assert old.transcript == [] and old.context == "" and old.purged_at is not None
    assert old.rating == "good" and old.scorecard["text_purged"] is True
    blob = repr(old.scorecard)
    assert "secret" not in blob and "What is your runway" not in blob and "Specific" not in blob
    assert old.scorecard["answers"] == [{"i": 1, "rating": 4}]
