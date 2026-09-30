import datetime as dt

import pytest

from app.db.models.budget import Budget
from app.schemas.budget import BudgetCreate
from app.schemas.expense import ExpenseCreate
from app.services.finance import budgets as budget_svc
from app.services.finance import expenses as expense_svc
from tests.factories import create_startup, create_user


def _startup(db):
    user = create_user(db)
    return user, create_startup(db, owner=user)


def _exp(db, u, s, *, amount, category, date):
    return expense_svc.create_expense(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=ExpenseCreate(vendor="V", category=category, expense_date=date, amount_minor=amount),
    )


def _budget(db, u, s, *, category, month, limit, currency="NGN"):
    return budget_svc.create_budget(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=BudgetCreate(
            category=category, period_month=month, limit_minor=limit, currency=currency
        ),
    )


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("2026-03", "2026-02"),
        ("2026-01", "2025-12"),
        ("2026-12", "2026-11"),
        ("2000-01", "1999-12"),
    ],
)
def test_prev_month(given, expected):
    result = budget_svc._prev_month(given)
    assert result == expected


def test_draft_seeds_per_category_from_prev_month_actuals(db):
    u, s = _startup(db)
    _exp(db, u, s, amount=100, category="Infra", date=dt.date(2026, 2, 1))
    _exp(db, u, s, amount=50, category="Infra", date=dt.date(2026, 2, 28))
    _exp(db, u, s, amount=70, category="Ops", date=dt.date(2026, 2, 15))
    _exp(db, u, s, amount=999, category="Ops", date=dt.date(2026, 3, 1))  # target month: ignored
    _exp(db, u, s, amount=888, category="Legal", date=dt.date(2026, 1, 31))  # two months back
    created = budget_svc.draft_from_actuals(
        db, startup_id=s.id, created_by=u.id, period_month="2026-03"
    )
    assert [(b.category, b.limit_minor, b.currency) for b in created] == [
        ("Infra", 150, "NGN"),
        ("Ops", 70, "NGN"),
    ]
    assert all(b.period_month == "2026-03" and b.created_by == u.id for b in created)
    assert db.query(Budget).filter_by(startup_id=s.id).count() == 2


def test_draft_skips_zero_spend_categories(db):
    u, s = _startup(db)
    _exp(db, u, s, amount=0, category="Free", date=dt.date(2026, 2, 5))
    _exp(db, u, s, amount=10, category="Paid", date=dt.date(2026, 2, 5))
    created = budget_svc.draft_from_actuals(
        db, startup_id=s.id, created_by=u.id, period_month="2026-03"
    )
    assert [b.category for b in created] == ["Paid"]


def test_draft_skips_categories_already_budgeted_and_keeps_their_limit(db):
    u, s = _startup(db)
    _exp(db, u, s, amount=100, category="Infra", date=dt.date(2026, 2, 5))
    _exp(db, u, s, amount=70, category="Ops", date=dt.date(2026, 2, 5))
    existing = _budget(db, u, s, category="Infra", month="2026-03", limit=5)
    created = budget_svc.draft_from_actuals(
        db, startup_id=s.id, created_by=u.id, period_month="2026-03"
    )
    assert [b.category for b in created] == ["Ops"]
    db.refresh(existing)
    assert existing.limit_minor == 5


def test_draft_with_no_prev_month_expenses_creates_nothing(db):
    u, s = _startup(db)
    _exp(db, u, s, amount=100, category="Infra", date=dt.date(2026, 3, 5))
    created = budget_svc.draft_from_actuals(
        db, startup_id=s.id, created_by=u.id, period_month="2026-03"
    )
    assert created == []
    assert db.query(Budget).filter_by(startup_id=s.id).count() == 0


def test_draft_january_reads_previous_decembers_expenses(db):
    u, s = _startup(db)
    _exp(db, u, s, amount=123, category="Infra", date=dt.date(2025, 12, 20))
    created = budget_svc.draft_from_actuals(
        db, startup_id=s.id, created_by=u.id, period_month="2026-01"
    )
    assert [(b.category, b.limit_minor, b.period_month) for b in created] == [
        ("Infra", 123, "2026-01")
    ]


def test_draft_is_tenant_scoped(db):
    u, s = _startup(db)
    u2, s2 = _startup(db)
    _exp(db, u2, s2, amount=500, category="Other", date=dt.date(2026, 2, 5))
    created = budget_svc.draft_from_actuals(
        db, startup_id=s.id, created_by=u.id, period_month="2026-03"
    )
    assert created == []


def test_draft_twice_is_idempotent(db):
    u, s = _startup(db)
    _exp(db, u, s, amount=100, category="Infra", date=dt.date(2026, 2, 5))
    first = budget_svc.draft_from_actuals(
        db, startup_id=s.id, created_by=u.id, period_month="2026-03"
    )
    second = budget_svc.draft_from_actuals(
        db, startup_id=s.id, created_by=u.id, period_month="2026-03"
    )
    assert len(first) == 1
    assert second == []


def test_copy_last_month_copies_category_limit_currency(db):
    u, s = _startup(db)
    _budget(db, u, s, category="Infra", month="2026-02", limit=100, currency="USD")
    _budget(db, u, s, category="Ops", month="2026-02", limit=70)
    _budget(db, u, s, category="Legal", month="2026-01", limit=9)  # not last month
    created = budget_svc.copy_last_month(
        db, startup_id=s.id, created_by=u.id, period_month="2026-03"
    )
    assert [(b.category, b.limit_minor, b.currency) for b in created] == [
        ("Infra", 100, "USD"),
        ("Ops", 70, "NGN"),
    ]
    assert all(b.period_month == "2026-03" for b in created)


def test_copy_skips_categories_already_budgeted(db):
    u, s = _startup(db)
    _budget(db, u, s, category="Infra", month="2026-02", limit=100)
    _budget(db, u, s, category="Ops", month="2026-02", limit=70)
    existing = _budget(db, u, s, category="Infra", month="2026-03", limit=5)
    created = budget_svc.copy_last_month(
        db, startup_id=s.id, created_by=u.id, period_month="2026-03"
    )
    assert [b.category for b in created] == ["Ops"]
    db.refresh(existing)
    assert existing.limit_minor == 5


def test_copy_with_no_prev_budgets_creates_nothing(db):
    u, s = _startup(db)
    created = budget_svc.copy_last_month(
        db, startup_id=s.id, created_by=u.id, period_month="2026-03"
    )
    assert created == []


def test_copy_january_reads_previous_december(db):
    u, s = _startup(db)
    _budget(db, u, s, category="Infra", month="2025-12", limit=42)
    created = budget_svc.copy_last_month(
        db, startup_id=s.id, created_by=u.id, period_month="2026-01"
    )
    assert [(b.category, b.limit_minor, b.period_month) for b in created] == [
        ("Infra", 42, "2026-01")
    ]


def test_copy_is_tenant_scoped(db):
    u, s = _startup(db)
    u2, s2 = _startup(db)
    _budget(db, u2, s2, category="Other", month="2026-02", limit=1)
    created = budget_svc.copy_last_month(
        db, startup_id=s.id, created_by=u.id, period_month="2026-03"
    )
    assert created == []
