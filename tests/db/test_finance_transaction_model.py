from datetime import date

from app.db.models.enums import TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from tests.factories import create_startup, create_user


def test_direction_source_values():
    assert TransactionDirection.inflow.value == "in"
    assert TransactionDirection.outflow.value == "out"
    assert {s.value for s in TransactionSource} == {
        "manual",
        "bank",
        "accounting",
        "stripe",
        "invoice",
    }


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


def test_direction_stored_as_wire_value(db):
    """The DB column stores the enum *value* ('in'/'out'), not the member name."""
    from sqlalchemy import text

    from app.db.models.enums import TransactionSource
    from tests.factories import create_startup, create_user

    u = create_user(db)
    s = create_startup(db, owner=u)
    row = Transaction(
        startup_id=s.id,
        date=date(2026, 3, 1),
        description="x",
        amount_minor=1,
        currency="NGN",
        direction=TransactionDirection.outflow,
        source=TransactionSource.manual,
    )
    db.add(row)
    db.flush()
    stored = db.execute(
        text("SELECT direction FROM transactions WHERE id = :i"), {"i": str(row.id)}
    ).scalar_one()
    assert stored == "out"
