import datetime as dt

from app.schemas.budget import BudgetCreate, BudgetUpdate
from app.schemas.expense import ExpenseCreate
from app.services.finance import budgets as budget_svc
from app.services.finance import expenses as expense_svc
from tests.factories import create_startup, create_user


def _startup(db):
    user = create_user(db)
    return user, create_startup(db, owner=user)


def _exp(db, u, s, *, amount, category="Infra", date=dt.date(2026, 3, 10)):
    return expense_svc.create_expense(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=ExpenseCreate(vendor="V", category=category, expense_date=date, amount_minor=amount),
    )


def test_category_totals_month_bounds_are_inclusive_both_ends(db):
    u, s = _startup(db)
    _exp(db, u, s, amount=1, date=dt.date(2026, 2, 28))
    _exp(db, u, s, amount=10, date=dt.date(2026, 3, 1))
    _exp(db, u, s, amount=100, date=dt.date(2026, 3, 31))
    _exp(db, u, s, amount=1000, date=dt.date(2026, 4, 1))
    totals = expense_svc.expense_category_totals(db, startup_id=s.id, month="2026-03")
    assert totals == {"Infra": 110}


def test_category_totals_is_tenant_scoped(db):
    u, s = _startup(db)
    u2, s2 = _startup(db)
    _exp(db, u, s, amount=5)
    _exp(db, u2, s2, amount=99)
    totals = expense_svc.expense_category_totals(db, startup_id=s.id, month="2026-03")
    assert totals == {"Infra": 5}


def test_summary_and_budget_spent_share_one_source(db):
    u, s = _startup(db)
    _exp(db, u, s, amount=30, category="Ops")
    _exp(db, u, s, amount=70, category="Ops")
    _exp(db, u, s, amount=50, category="Infra")
    budget_svc.create_budget(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=BudgetCreate(category="Ops", period_month="2026-03", limit_minor=90),
    )
    summary = expense_svc.category_summary(db, startup_id=s.id, month="2026-03")
    summary_rows = {r["category"]: r["total_minor"] for r in summary["rows"]}
    listed = budget_svc.list_budgets(db, startup_id=s.id, month="2026-03")
    assert len(listed) == 1
    budget, spent = listed[0]
    assert spent == summary_rows[budget.category] == 100
    assert summary["total_minor"] == 150


def test_create_then_serialize_reflects_existing_expenses(db):
    u, s = _startup(db)
    _exp(db, u, s, amount=42)
    budget = budget_svc.create_budget(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=BudgetCreate(category="Infra", period_month="2026-03", limit_minor=10),
    )
    spent = budget_svc.spent_for(db, startup_id=s.id, budget=budget)
    resp = budget_svc.serialize_budget(budget, spent_minor=spent)
    assert resp.spent_minor == 42
    assert resp.over_budget is True


def test_update_only_touches_set_fields(db):
    u, s = _startup(db)
    budget = budget_svc.create_budget(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=BudgetCreate(category="Ops", period_month="2026-03", limit_minor=10, notes="n"),
    )
    updated = budget_svc.update_budget(
        db, startup_id=s.id, budget_id=budget.id, data=BudgetUpdate(limit_minor=20)
    )
    assert updated.limit_minor == 20
    assert updated.notes == "n"
