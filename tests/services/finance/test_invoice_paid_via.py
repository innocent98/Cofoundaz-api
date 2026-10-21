from datetime import UTC, datetime

from app.db.models.enums import InvoiceStatus
from app.db.models.finance import Transaction
from app.services.finance import invoices as inv_svc
from tests.services.finance.payments_helpers import make_invoice, make_workspace


def test_mark_paid_via_provider_pays_once_and_records_provider(db):
    _u, s = make_workspace(db)
    inv = make_invoice(db, s)
    when = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    out = inv_svc.mark_paid_via_provider(
        db, startup_id=s.id, invoice_id=inv.id, provider="paystack", now=when
    )
    assert out is not None
    assert out.status == InvoiceStatus.paid
    assert out.paid_via == "paystack"
    assert out.paid_at == when
    assert db.query(Transaction).filter_by(startup_id=s.id).count() == 1

    again = inv_svc.mark_paid_via_provider(
        db, startup_id=s.id, invoice_id=inv.id, provider="flutterwave", now=when
    )
    assert again is not None and again.paid_via == "paystack"
    assert db.query(Transaction).filter_by(startup_id=s.id).count() == 1


def test_mark_paid_via_provider_returns_none_when_not_payable(db):
    _u, s = make_workspace(db)
    draft = make_invoice(db, s, status=InvoiceStatus.draft)
    assert (
        inv_svc.mark_paid_via_provider(
            db, startup_id=s.id, invoice_id=draft.id, provider="paystack"
        )
        is None
    )
    assert draft.status == InvoiceStatus.draft and draft.paid_via is None


def test_mark_paid_via_provider_is_scoped_to_startup(db):
    _u, s = make_workspace(db)
    _u2, other = make_workspace(db)
    inv = make_invoice(db, s)
    assert (
        inv_svc.mark_paid_via_provider(
            db, startup_id=other.id, invoice_id=inv.id, provider="paystack"
        )
        is None
    )
    assert inv.status == InvoiceStatus.sent
