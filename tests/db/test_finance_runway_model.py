import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.finance_runway import FinanceRunwaySettings
from tests.factories import create_startup, create_user


def test_runway_settings_persist_with_defaults(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = FinanceRunwaySettings(startup_id=s.id)
    db.add(row)
    db.flush()
    got = db.get(FinanceRunwaySettings, row.id)
    assert got is not None
    assert got.mom_growth_percent == 0
    assert got.hiring_spend_minor == 0
    assert got.one_off_costs_minor == 0
    assert got.alert_is_low is False
    assert got.alert_last_fired_at is None
    assert got.created_at is not None


def test_runway_settings_startup_id_is_unique(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.add(FinanceRunwaySettings(startup_id=s.id))
    db.flush()
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(FinanceRunwaySettings(startup_id=s.id))
    # the SAVEPOINT rolled back only the failed insert; the session is still usable
    remaining = db.query(FinanceRunwaySettings).filter_by(startup_id=s.id).count()
    assert remaining == 1
