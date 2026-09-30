import datetime as dt
import uuid

import pytest

from app.core.errors import NotFound
from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.expense import Expense
from app.db.models.finance import Transaction
from app.schemas.expense import ExpenseCreate, ExpenseUpdate
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


def test_update_syncs_same_transaction(db):
    u, s = _startup(db)
    exp = svc.create_expense(db, startup_id=s.id, created_by=u.id, data=_data())
    original_txn_id = exp.transaction_id
    patch = ExpenseUpdate(
        amount_minor=400_000,
        expense_date=dt.date(2026, 1, 2),
        category="Ops",
        vendor="GCP",
        currency="USD",
    )
    updated = svc.update_expense(db, startup_id=s.id, expense_id=exp.id, data=patch)
    assert updated.transaction_id == original_txn_id
    rows = db.query(Transaction).filter_by(startup_id=s.id).all()
    assert len(rows) == 1
    txn = rows[0]
    assert txn.id == original_txn_id
    assert txn.amount_minor == 400_000
    assert txn.date == dt.date(2026, 1, 2)
    assert txn.category == "Ops"
    assert txn.currency == "USD"
    assert txn.description == "Expense: GCP"
    assert txn.source == TransactionSource.expense
    assert txn.direction == TransactionDirection.outflow


def test_update_non_ledger_fields_leaves_transaction_untouched(db):
    u, s = _startup(db)
    exp = svc.create_expense(db, startup_id=s.id, created_by=u.id, data=_data())
    txn = db.get(Transaction, exp.transaction_id)
    before = (txn.amount_minor, txn.date, txn.category, txn.description)
    svc.update_expense(
        db,
        startup_id=s.id,
        expense_id=exp.id,
        data=ExpenseUpdate(notes="hi", recurring=True),
    )
    assert exp.notes == "hi" and exp.recurring is True
    assert (txn.amount_minor, txn.date, txn.category, txn.description) == before


def test_update_tolerates_missing_linked_transaction(db):
    u, s = _startup(db)
    exp = svc.create_expense(db, startup_id=s.id, created_by=u.id, data=_data())
    exp.transaction_id = None
    db.flush()
    updated = svc.update_expense(
        db, startup_id=s.id, expense_id=exp.id, data=ExpenseUpdate(amount_minor=1)
    )
    assert updated.amount_minor == 1


def test_update_adjusts_cash_on_hand(db):
    u, s = _startup(db)
    exp = svc.create_expense(db, startup_id=s.id, created_by=u.id, data=_data(amount_minor=250_000))
    assert _cash(db, s) == -250_000
    svc.update_expense(
        db, startup_id=s.id, expense_id=exp.id, data=ExpenseUpdate(amount_minor=100_000)
    )
    assert _cash(db, s) == -100_000


def test_delete_reverses_the_outflow(db):
    u, s = _startup(db)
    before = _cash(db, s)
    exp = svc.create_expense(db, startup_id=s.id, created_by=u.id, data=_data())
    txn_id = exp.transaction_id
    assert _cash(db, s) == before - 250_000
    svc.delete_expense(db, startup_id=s.id, expense_id=exp.id)
    assert db.query(Transaction).filter_by(id=txn_id).one_or_none() is None
    assert db.query(Expense).filter_by(startup_id=s.id).count() == 0
    assert _cash(db, s) == before


def test_delete_unknown_expense_404(db):
    u, s = _startup(db)
    with pytest.raises(NotFound):
        svc.delete_expense(db, startup_id=s.id, expense_id=uuid.uuid4())


def test_update_and_delete_are_tenant_scoped(db):
    u, s = _startup(db)
    _u2, other = _startup(db)
    exp = svc.create_expense(db, startup_id=s.id, created_by=u.id, data=_data())
    with pytest.raises(NotFound):
        svc.update_expense(
            db, startup_id=other.id, expense_id=exp.id, data=ExpenseUpdate(amount_minor=1)
        )
    with pytest.raises(NotFound):
        svc.delete_expense(db, startup_id=other.id, expense_id=exp.id)


def test_update_and_delete_work_with_autoflush_off(db):
    u, s = _startup(db)
    exp = svc.create_expense(db, startup_id=s.id, created_by=u.id, data=_data())
    with db.no_autoflush:
        svc.update_expense(
            db, startup_id=s.id, expense_id=exp.id, data=ExpenseUpdate(amount_minor=5)
        )
        svc.delete_expense(db, startup_id=s.id, expense_id=exp.id)
    assert db.query(Transaction).filter_by(startup_id=s.id).count() == 0
