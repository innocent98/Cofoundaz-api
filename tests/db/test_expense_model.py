from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.expense import Expense
from app.db.models.finance import Transaction
from tests.factories import create_startup, create_user


def _expense(startup_id, transaction_id=None):
    return Expense(
        startup_id=startup_id,
        vendor="AWS",
        category="Infrastructure",
        expense_date=date(2026, 9, 1),
        amount_minor=250_000,
        transaction_id=transaction_id,
    )


def _transaction(startup_id):
    return Transaction(
        startup_id=startup_id,
        date=date(2026, 9, 1),
        description="AWS",
        amount_minor=250_000,
        direction=TransactionDirection.outflow,
        source=TransactionSource.expense,
    )


def test_expense_persists_with_defaults(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = _expense(s.id)
    db.add(row)
    db.flush()
    got = db.get(Expense, row.id)
    assert got is not None
    assert got.recurring is False
    assert got.currency == "NGN"
    assert got.receipt_url is None
    assert got.receipt_key is None
    assert got.notes is None
    assert got.created_by is None
    assert got.transaction_id is None
    assert got.created_at is not None


def test_expense_transaction_id_unique(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    txn = _transaction(s.id)
    db.add(txn)
    db.flush()
    db.add(_expense(s.id, transaction_id=txn.id))
    db.flush()
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(_expense(s.id, transaction_id=txn.id))
    # the SAVEPOINT rolled back only the failed insert; the session is still usable
    remaining = db.query(Expense).filter_by(startup_id=s.id).count()
    assert remaining == 1


def test_expense_transaction_delete_sets_null(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    txn = _transaction(s.id)
    db.add(txn)
    db.flush()
    row = _expense(s.id, transaction_id=txn.id)
    db.add(row)
    db.flush()
    db.execute(text("DELETE FROM transactions WHERE id = :id"), {"id": txn.id})
    db.expire(row)
    assert row.transaction_id is None


def test_transaction_source_has_expense():
    assert TransactionSource.expense == "expense"
