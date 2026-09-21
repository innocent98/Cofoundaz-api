from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.llm_usage import LlmUsageDaily
from tests.factories import create_startup, create_user


def _row(db, s, **kw):
    r = LlmUsageDaily(
        startup_id=s.id,
        usage_date=kw.get("usage_date", date.today()),
        tokens_used=kw.get("tokens_used", 0),
    )
    db.add(r)
    db.flush()
    return r


def test_round_trip_and_default(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    r = _row(db, s, tokens_used=123)
    got = db.query(LlmUsageDaily).filter_by(id=r.id).one()
    assert got.tokens_used == 123


def test_unique_per_startup_day(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _row(db, s)
    with pytest.raises(IntegrityError):
        _row(db, s)  # same (startup, today)
