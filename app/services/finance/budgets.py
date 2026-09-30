import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.budget import Budget
from app.schemas.budget import BudgetCreate, BudgetResponse, BudgetUpdate
from app.services.finance.errors import _validation
from app.services.finance.expenses import expense_category_totals


def create_budget(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID | None, data: BudgetCreate
) -> Budget:
    # add() INSIDE begin_nested: a duplicate (category, month) rolls back only the savepoint, so
    # the outer session stays usable and the caller gets a 422 instead of a poisoned-session 500.
    try:
        with db.begin_nested():
            budget = Budget(startup_id=startup_id, created_by=created_by, **data.model_dump())
            db.add(budget)
    except IntegrityError:
        raise _validation(
            "category", "A budget for this category and month already exists."
        ) from None
    return budget


def get_budget(db: Session, *, startup_id: uuid.UUID, budget_id: uuid.UUID) -> Budget:
    budget = db.query(Budget).filter_by(id=budget_id, startup_id=startup_id).one_or_none()
    if budget is None:
        raise NotFound()
    return budget


def update_budget(
    db: Session, *, startup_id: uuid.UUID, budget_id: uuid.UUID, data: BudgetUpdate
) -> Budget:
    budget = get_budget(db, startup_id=startup_id, budget_id=budget_id)
    for name, value in data.model_dump(exclude_unset=True).items():
        setattr(budget, name, value)
    db.flush()
    return budget


def delete_budget(db: Session, *, startup_id: uuid.UUID, budget_id: uuid.UUID) -> None:
    budget = get_budget(db, startup_id=startup_id, budget_id=budget_id)
    db.delete(budget)
    db.flush()


def spent_for(db: Session, *, startup_id: uuid.UUID, budget: Budget) -> int:
    """Actual spend for one budget: its category's expense total in its month."""
    totals = expense_category_totals(db, startup_id=startup_id, month=budget.period_month)
    return totals.get(budget.category, 0)


def list_budgets(db: Session, *, startup_id: uuid.UUID, month: str) -> list[tuple[Budget, int]]:
    """The month's budgets paired with actuals; the expense totals are queried once for all."""
    budgets = (
        db.query(Budget)
        .filter_by(startup_id=startup_id, period_month=month)
        .order_by(Budget.category)
        .all()
    )
    totals = expense_category_totals(db, startup_id=startup_id, month=month)
    return [(b, totals.get(b.category, 0)) for b in budgets]


def serialize_budget(budget: Budget, *, spent_minor: int) -> BudgetResponse:
    limit = budget.limit_minor
    return BudgetResponse(
        id=budget.id,
        category=budget.category,
        period_month=budget.period_month,
        limit_minor=limit,
        currency=budget.currency,
        notes=budget.notes,
        spent_minor=spent_minor,
        variance_minor=spent_minor - limit,
        over_budget=spent_minor > limit,
        percent_used=round(100 * spent_minor / limit, 1) if limit > 0 else None,
        created_at=budget.created_at,
        updated_at=budget.updated_at,
    )


def _prev_month(period_month: str) -> str:
    """The calendar month before "YYYY-MM", rolling the year back across January."""
    year, month = int(period_month[:4]), int(period_month[5:7])
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def _existing_categories(db: Session, *, startup_id: uuid.UUID, period_month: str) -> set[str]:
    rows = db.query(Budget.category).filter_by(startup_id=startup_id, period_month=period_month)
    return {category for (category,) in rows}


def _seed_missing(
    db: Session,
    *,
    startup_id: uuid.UUID,
    created_by: uuid.UUID | None,
    period_month: str,
    candidates: list[tuple[str, int, str]],
) -> list[Budget]:
    """Insert (category, limit, currency) candidates for the month, skipping categories that
    already have a budget. Never overwrites."""
    existing = _existing_categories(db, startup_id=startup_id, period_month=period_month)
    created: list[Budget] = []
    for category, limit_minor, currency in sorted(candidates):
        if category in existing:
            continue
        # add() INSIDE begin_nested: if a concurrent request inserted the same (category, month)
        # after our read, only the savepoint rolls back and we skip it instead of a 500.
        try:
            with db.begin_nested():
                budget = Budget(
                    startup_id=startup_id,
                    created_by=created_by,
                    category=category,
                    period_month=period_month,
                    limit_minor=limit_minor,
                    currency=currency,
                )
                db.add(budget)
        except IntegrityError:
            continue
        created.append(budget)
    db.flush()
    return created


def draft_from_actuals(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID | None, period_month: str
) -> list[Budget]:
    """Seed the month with one budget per category that had spend last month (limit = that spend)."""
    totals = expense_category_totals(db, startup_id=startup_id, month=_prev_month(period_month))
    candidates = [(cat, total, "NGN") for cat, total in totals.items() if total > 0]
    return _seed_missing(
        db,
        startup_id=startup_id,
        created_by=created_by,
        period_month=period_month,
        candidates=candidates,
    )


def copy_last_month(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID | None, period_month: str
) -> list[Budget]:
    """Copy last month's budgets (category, limit, currency) into the month."""
    prev_rows = (
        db.query(Budget)
        .filter_by(startup_id=startup_id, period_month=_prev_month(period_month))
        .all()
    )
    candidates = [(b.category, b.limit_minor, b.currency) for b in prev_rows]
    return _seed_missing(
        db,
        startup_id=startup_id,
        created_by=created_by,
        period_month=period_month,
        candidates=candidates,
    )
