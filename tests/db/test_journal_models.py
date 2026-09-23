import uuid
from datetime import date

import pytest
import sqlalchemy

from app.db.models.journal import JournalEntry, MoodLog
from tests.factories import create_startup, create_user

TODAY = date.today()


def _entry(db, startup, founder, **kw):
    values = {
        "startup_id": startup.id,
        "founder_id": founder.id,
        "date": TODAY,
        "content_encrypted": "gAAAAA-pretend-token",
        "mood": 3,
        "stress": 5,
    }
    values.update(kw)
    entry = JournalEntry(**values)
    db.add(entry)
    db.flush()
    return entry


def test_entry_persists_with_its_columns(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    e = _entry(db, s, u)
    assert e.id is not None
    assert e.startup_id == s.id
    assert e.founder_id == u.id
    assert e.date == TODAY
    assert e.created_at is not None


def test_one_entry_per_founder_per_day(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _entry(db, s, u)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        _entry(db, s, u)


def test_two_founders_may_each_have_an_entry_the_same_day(db):
    u1 = create_user(db)
    u2 = create_user(db, email=f"second-{uuid.uuid4().hex[:8]}@example.com")
    s = create_startup(db, owner=u1)
    _entry(db, s, u1)
    _entry(db, s, u2)
    assert db.query(JournalEntry).filter_by(startup_id=s.id).count() == 2


def test_stress_below_one_is_rejected(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        _entry(db, s, u, stress=0)


def test_stress_above_ten_is_rejected(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        _entry(db, s, u, stress=11)


def test_deleting_the_startup_removes_its_entries(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _entry(db, s, u)
    db.delete(s)
    db.flush()
    assert db.query(JournalEntry).filter_by(startup_id=s.id).count() == 0


def test_one_mood_log_per_founder_per_day(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.add(MoodLog(startup_id=s.id, founder_id=u.id, date=TODAY, mood=3, stress=5))
    db.flush()
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db.add(MoodLog(startup_id=s.id, founder_id=u.id, date=TODAY, mood=4, stress=6))
        db.flush()


def test_mood_log_stress_range_is_enforced(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db.add(MoodLog(startup_id=s.id, founder_id=u.id, date=TODAY, mood=3, stress=99))
        db.flush()


def test_journal_prompt_persists(db):
    from app.db.models.enums import EnrichmentStatus
    from app.db.models.journal import JournalPrompt

    u = create_user(db)
    s = create_startup(db, owner=u)
    row = JournalPrompt(
        startup_id=s.id,
        founder_id=u.id,
        date=TODAY,
        prompt="What moved forward today?",
        status=EnrichmentStatus.generating,
    )
    db.add(row)
    db.flush()
    got = db.query(JournalPrompt).filter_by(startup_id=s.id, founder_id=u.id).one()
    assert got.status == EnrichmentStatus.generating
    assert got.prompt == "What moved forward today?"
