import datetime as dt

from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from app.schemas.expense import ExpenseCreate
from app.services.finance import expenses as svc
from app.services.finance.cashflow import cash_flow_summary
from tests.factories import create_startup, create_user


def _startup(db):
    user = create_user(db)
    return user, create_startup(db, owner=user)


def _data(**over) -> ExpenseCreate:
    body = {
        "vendor": "AWS",
        "category": "Infrastructure",
        "expense_date": dt.datetime.now(dt.UTC).date(),
        "amount_minor": 250_000,
    }
    body.update(over)
    return ExpenseCreate(**body)


def _cash(db, startup) -> int:
    return cash_flow_summary(db, startup_id=startup.id)["cash_on_hand"]


def test_create_posts_exactly_one_linked_outflow(db):
    u, s = _startup(db)
    exp = svc.create_expense(db, startup_id=s.id, created_by=u.id, data=_data())
    rows = db.query(Transaction).filter_by(startup_id=s.id).all()
    assert len(rows) == 1
    txn = rows[0]
    assert txn.direction == TransactionDirection.outflow
    assert txn.source == TransactionSource.expense
    assert txn.amount_minor == exp.amount_minor == 250_000
    assert txn.date == exp.expense_date
    assert txn.category == "Infrastructure"
    assert txn.currency == exp.currency
    assert txn.description == "Expense: AWS"
    assert exp.transaction_id == txn.id


def test_expense_reduces_cash_on_hand_and_shows_in_month_series(db):
    u, s = _startup(db)
    before = _cash(db, s)
    svc.create_expense(db, startup_id=s.id, created_by=u.id, data=_data(amount_minor=250_000))
    summary = cash_flow_summary(db, startup_id=s.id)
    assert summary["cash_on_hand"] == before - 250_000
    assert summary["by_month"][-1]["outflow"] == 250_000
    assert summary["by_month"][-1]["net"] == -250_000


def test_two_expenses_two_outflows(db):
    u, s = _startup(db)
    svc.create_expense(db, startup_id=s.id, created_by=u.id, data=_data(amount_minor=100))
    svc.create_expense(db, startup_id=s.id, created_by=u.id, data=_data(amount_minor=200))
    assert db.query(Transaction).filter_by(startup_id=s.id).count() == 2
    assert _cash(db, s) == -300


def test_create_works_with_autoflush_off(db):
    # Prod SessionLocal is autoflush=False; create must flush explicitly and not rely on autoflush.
    u, s = _startup(db)
    db.flush()
    with db.no_autoflush:
        exp = svc.create_expense(db, startup_id=s.id, created_by=u.id, data=_data())
        assert exp.transaction_id is not None
        assert db.query(Transaction).filter_by(startup_id=s.id).count() == 1


def test_serialize_has_receipt_flag(db):
    u, s = _startup(db)
    exp = svc.create_expense(db, startup_id=s.id, created_by=u.id, data=_data())
    assert svc.serialize_expense(exp).has_receipt is False
    exp.receipt_url = "https://example.com/r.pdf"
    assert svc.serialize_expense(exp).has_receipt is True


def test_summary_guards_zero_total(db):
    u, s = _startup(db)
    svc.create_expense(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=_data(amount_minor=0, expense_date=dt.date(2026, 2, 1)),
    )
    out = svc.category_summary(db, startup_id=s.id, month="2026-02")
    assert out["rows"] == [] and out["total_minor"] == 0
