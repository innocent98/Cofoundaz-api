from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.dashboard import DailyBriefing
from app.db.models.enums import BriefingStatus
from tests.factories import create_startup, create_user


def _briefing(db, startup, **kw):
    row = DailyBriefing(
        startup_id=startup.id,
        briefing_date=kw.get("briefing_date", date.today()),
        status=kw.get("status", BriefingStatus.generating),
        briefing=kw.get("briefing", "b"),
        risks=kw.get("risks", "r"),
        opportunities=kw.get("opportunities", "o"),
    )
    db.add(row)
    db.flush()
    return row


def test_daily_briefing_round_trips(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = _briefing(db, s, status=BriefingStatus.ready, briefing="hello")
    got = db.query(DailyBriefing).filter_by(id=row.id).one()
    assert got.status == BriefingStatus.ready
    assert got.briefing == "hello"


def test_daily_briefing_unique_per_startup_day(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _briefing(db, s)
    with pytest.raises(IntegrityError):
        _briefing(db, s)  # same (startup_id, today) violates uq_briefing_startup_date
