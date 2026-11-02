import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.budget import Budget
from tests.factories import create_startup, create_user


def _budget(startup_id, category="Infrastructure", period_month="2026-09"):
    return Budget(
        startup_id=startup_id,
        category=category,
        period_month=period_month,
        limit_minor=500_000,
    )


def test_budget_persists_with_defaults(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = _budget(s.id)
    db.add(row)
    db.flush()
    got = db.get(Budget, row.id)
    assert got is not None
    assert got.currency == "NGN"
    assert got.notes is None
    assert got.created_by is None
    assert got.limit_minor == 500_000
    assert got.period_month == "2026-09"
    assert got.created_at is not None


def test_budget_unique_per_startup_category_month(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.add(_budget(s.id))
    db.flush()
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(_budget(s.id))
    # the SAVEPOINT rolled back only the failed insert; the session is still usable
    remaining = db.query(Budget).filter_by(startup_id=s.id).count()
    assert remaining == 1


def test_budget_same_category_different_month_allowed(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.add(_budget(s.id, period_month="2026-09"))
    db.add(_budget(s.id, period_month="2026-10"))
    db.flush()
    count = db.query(Budget).filter_by(startup_id=s.id, category="Infrastructure").count()
    assert count == 2
