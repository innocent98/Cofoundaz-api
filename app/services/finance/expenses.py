import calendar
import datetime as dt
import uuid
from collections import Counter
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.expense import Expense
from app.db.models.finance import Transaction
from app.schemas.expense import ExpenseCreate, ExpenseResponse
from app.services.finance.errors import _validation


def _month_range(month: str) -> tuple[dt.date, dt.date]:
    """Inclusive first/last day of a "YYYY-MM" calendar month."""
    try:
        year_s, month_s = month.split("-")
        first = dt.date(int(year_s), int(month_s), 1)
    except ValueError:
        raise _validation("month", "month must be in YYYY-MM format.") from None
    last = dt.date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
    return first, last


def _post_outflow(db: Session, exp: Expense) -> Transaction:
    # 1:1 with the expense: this row is what makes the spend reduce cash-on-hand / raise burn.
    txn = Transaction(
        startup_id=exp.startup_id,
        date=exp.expense_date,
        description=f"Expense: {exp.vendor}",
        category=exp.category,
        amount_minor=exp.amount_minor,
        currency=exp.currency,
        direction=TransactionDirection.outflow,
        source=TransactionSource.expense,
    )
    db.add(txn)
    db.flush()
    return txn


def create_expense(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID | None, data: ExpenseCreate
) -> Expense:
    exp = Expense(startup_id=startup_id, created_by=created_by, **data.model_dump())
    db.add(exp)
    db.flush()
    txn = _post_outflow(db, exp)
    exp.transaction_id = txn.id
    db.flush()
    return exp


def get_expense(db: Session, *, startup_id: uuid.UUID, expense_id: uuid.UUID) -> Expense:
    exp = db.query(Expense).filter_by(id=expense_id, startup_id=startup_id).one_or_none()
    if exp is None:
        raise NotFound()
    return exp


def list_expenses(
    db: Session,
    *,
    startup_id: uuid.UUID,
    category: str | None = None,
    month: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    recurring: bool | None = None,
) -> list[Expense]:
    q = db.query(Expense).filter_by(startup_id=startup_id)
    if category is not None:
        q = q.filter(Expense.category == category)
    if month is not None:
        first, last = _month_range(month)
        q = q.filter(Expense.expense_date >= first, Expense.expense_date <= last)
    if date_from is not None:
        q = q.filter(Expense.expense_date >= date_from)
    if date_to is not None:
        q = q.filter(Expense.expense_date <= date_to)
    if recurring is not None:
        q = q.filter(Expense.recurring == recurring)
    return q.order_by(Expense.expense_date.desc(), Expense.created_at.desc()).all()


def serialize_expense(exp: Expense) -> ExpenseResponse:
    return ExpenseResponse(
        id=exp.id,
        vendor=exp.vendor,
        category=exp.category,
        expense_date=exp.expense_date,
        amount_minor=exp.amount_minor,
        currency=exp.currency,
        recurring=exp.recurring,
        notes=exp.notes,
        has_receipt=exp.receipt_url is not None,
        receipt_url=exp.receipt_url,
        transaction_id=exp.transaction_id,
        created_by=exp.created_by,
        created_at=exp.created_at,
        updated_at=exp.updated_at,
    )


def category_summary(db: Session, *, startup_id: uuid.UUID, month: str) -> dict[str, Any]:
    first, last = _month_range(month)
    base = db.query(Expense).filter(
        Expense.startup_id == startup_id,
        Expense.expense_date >= first,
        Expense.expense_date <= last,
    )
    totals = (
        base.with_entities(Expense.category, func.sum(Expense.amount_minor))
        .group_by(Expense.category)
        .all()
    )
    grand_total = sum(int(t) for _, t in totals)
    currencies = Counter(c for (c,) in base.with_entities(Expense.currency).all())
    currency = currencies.most_common(1)[0][0] if currencies else "NGN"

    rows: list[dict[str, Any]] = []
    if grand_total > 0:  # guard: an all-zero month has no meaningful percentages
        rows = [
            {
                "category": cat,
                "total_minor": int(total),
                "percent": round(100 * int(total) / grand_total, 1),
            }
            for cat, total in sorted(totals, key=lambda r: (-int(r[1]), r[0]))
        ]
    return {"month": month, "currency": currency, "total_minor": grand_total, "rows": rows}
