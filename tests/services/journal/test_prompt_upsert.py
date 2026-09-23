from datetime import date

from app.db.models.enums import EnrichmentStatus
from app.db.models.job import Job
from app.db.models.journal import JournalPrompt
from app.services.journal.service import JournalService
from tests.factories import create_startup, create_user


def _jobs(db, startup_id):
    return db.query(Job).filter_by(type="ai.journal.prompt", startup_id=startup_id).all()


def test_first_call_creates_static_generating_row_and_enqueues(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = JournalService.get_or_create_today_prompt(db, startup_id=s.id, founder_id=u.id)
    assert row.status == EnrichmentStatus.generating
    assert row.prompt == JournalService.get_prompt(today=date.today())
    assert len(_jobs(db, s.id)) == 1


def test_second_call_returns_existing_no_duplicate(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    JournalService.get_or_create_today_prompt(db, startup_id=s.id, founder_id=u.id)
    JournalService.get_or_create_today_prompt(db, startup_id=s.id, founder_id=u.id)
    assert db.query(JournalPrompt).filter_by(startup_id=s.id, founder_id=u.id).count() == 1
    assert len(_jobs(db, s.id)) == 1
