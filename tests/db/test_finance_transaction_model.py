from datetime import date

from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from tests.factories import create_startup, create_user


def test_direction_source_values():
    assert TransactionDirection.inflow.value == "in"
    assert TransactionDirection.outflow.value == "out"
    assert {s.value for s in TransactionSource} == {"manual", "bank", "accounting", "stripe"}


def test_transaction_persists(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = Transaction(
        startup_id=s.id,
        date=date(2026, 3, 10),
        description="Stripe payout",
        category=None,
        amount_minor=45000000,
        currency="NGN",
        direction=TransactionDirection.inflow,
        source=TransactionSource.manual,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.amount_minor == 45000000 and row.category is None
    assert row.direction == TransactionDirection.inflow and row.source == TransactionSource.manual
