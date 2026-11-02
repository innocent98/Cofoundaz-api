import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models.enums import InvoiceStatus, InvoiceTerms, TransactionSource
from app.db.models.invoice import Invoice
from tests.factories import create_startup, create_user


def _invoice(startup_id, number="INV-0001"):
    return Invoice(
        startup_id=startup_id,
        number=number,
        client_name="Acme Ltd",
        client_email="billing@acme.test",
    )


def test_invoice_persists_with_defaults(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = _invoice(s.id)
    db.add(row)
    db.flush()
    got = db.get(Invoice, row.id)
    assert got is not None
    assert got.status == InvoiceStatus.draft
    assert got.terms == InvoiceTerms.net_30
    assert got.auto_remind is False
    assert got.line_items == []
    assert got.subtotal_minor == 0
    assert got.tax_minor == 0
    assert got.total_minor == 0
    assert float(got.tax_percent) == 0
    assert got.currency == "NGN"
    assert got.issued_on is None
    assert got.due_on is None
    assert got.paid_at is None
    assert got.transaction_id is None
    assert got.created_at is not None


def test_invoice_number_unique_per_startup(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.add(_invoice(s.id))
    db.flush()
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(_invoice(s.id))
    # the SAVEPOINT rolled back only the failed insert; the session is still usable
    remaining = db.query(Invoice).filter_by(startup_id=s.id).count()
    assert remaining == 1


def test_invoice_number_reusable_across_startups(db):
    u = create_user(db)
    s1 = create_startup(db, owner=u)
    s2 = create_startup(db, owner=u)
    db.add(_invoice(s1.id))
    db.add(_invoice(s2.id))
    db.flush()
    assert db.query(Invoice).filter_by(number="INV-0001").count() == 2


def test_invoice_enums_stored_as_values(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = _invoice(s.id)
    db.add(row)
    db.flush()
    raw = db.execute(
        text("SELECT status, terms FROM invoices WHERE id = :id"), {"id": row.id}
    ).one()
    assert raw.status == "draft"
    assert raw.terms == "net_30"


def test_transaction_source_has_invoice():
    assert TransactionSource.invoice == "invoice"
