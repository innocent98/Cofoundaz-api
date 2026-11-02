import datetime as dt

import pytest

from app.core.errors import AppError, NotFound
from app.db.models.enums import InvoiceStatus, InvoiceTerms
from app.db.models.invoice import Invoice
from app.db.models.job import Job
from app.schemas.invoice import InvoiceCreate, InvoiceUpdate, LineItem
from app.services.finance import invoices as svc
from tests.factories import create_startup, create_user

TODAY = dt.date(2026, 9, 29)


def _startup(db):
    return create_startup(db, owner=create_user(db))


def _draft(db, startup, *, terms="net_30") -> Invoice:
    return svc.create_invoice(
        db,
        startup_id=startup.id,
        data=InvoiceCreate(
            client_name="Acme Ltd",
            client_email="billing@acme-client.com",
            line_items=[LineItem(description="Design", quantity=2, unit_price_minor=50000)],
            tax_percent=10,
            terms=terms,
        ),
    )


def test_edit_draft_recomputes_totals(db):
    s = _startup(db)
    inv = _draft(db, s)
    assert inv.total_minor == 110000
    updated = svc.update_invoice(
        db,
        startup_id=s.id,
        invoice_id=inv.id,
        data=InvoiceUpdate(
            line_items=[LineItem(description="Build", quantity=1, unit_price_minor=200000)]
        ),
    )
    assert updated.subtotal_minor == 200000
    assert updated.tax_minor == 20000
    assert updated.total_minor == 220000


def test_edit_draft_tax_only_recomputes_from_existing_lines(db):
    s = _startup(db)
    inv = _draft(db, s)
    updated = svc.update_invoice(
        db, startup_id=s.id, invoice_id=inv.id, data=InvoiceUpdate(tax_percent=5)
    )
    assert updated.subtotal_minor == 100000
    assert updated.tax_minor == 5000
    assert updated.total_minor == 105000


def test_edit_draft_client_fields_leave_totals_alone(db):
    s = _startup(db)
    inv = _draft(db, s)
    updated = svc.update_invoice(
        db,
        startup_id=s.id,
        invoice_id=inv.id,
        data=InvoiceUpdate(client_name="Beta Inc", terms="due_on_receipt"),
    )
    assert updated.client_name == "Beta Inc"
    assert updated.terms == InvoiceTerms.due_on_receipt
    assert updated.total_minor == 110000


@pytest.mark.parametrize("status", [InvoiceStatus.sent, InvoiceStatus.paid])
def test_edit_non_draft_rejected(db, status):
    s = _startup(db)
    inv = _draft(db, s)
    inv.status = status
    db.flush()
    with pytest.raises(AppError) as exc:
        svc.update_invoice(
            db, startup_id=s.id, invoice_id=inv.id, data=InvoiceUpdate(client_name="Beta Inc")
        )
    assert exc.value.http_status == 422
    assert inv.client_name == "Acme Ltd"


@pytest.mark.parametrize("status", [InvoiceStatus.draft, InvoiceStatus.sent, InvoiceStatus.paid])
def test_auto_remind_toggle_allowed_in_any_status(db, status):
    s = _startup(db)
    inv = _draft(db, s)
    inv.status = status
    db.flush()
    initial = inv.auto_remind
    updated = svc.update_invoice(
        db, startup_id=s.id, invoice_id=inv.id, data=InvoiceUpdate(auto_remind=not initial)
    )
    assert updated.auto_remind is (not initial)


def test_delete_draft_ok(db):
    s = _startup(db)
    inv = _draft(db, s)
    invoice_id = inv.id
    svc.delete_invoice(db, startup_id=s.id, invoice_id=invoice_id)
    with pytest.raises(NotFound):
        svc.get_invoice(db, startup_id=s.id, invoice_id=invoice_id)


@pytest.mark.parametrize("status", [InvoiceStatus.sent, InvoiceStatus.paid])
def test_delete_non_draft_rejected(db, status):
    s = _startup(db)
    inv = _draft(db, s)
    inv.status = status
    db.flush()
    with pytest.raises(AppError) as exc:
        svc.delete_invoice(db, startup_id=s.id, invoice_id=inv.id)
    assert exc.value.http_status == 422
    still_there = svc.get_invoice(db, startup_id=s.id, invoice_id=inv.id)
    assert still_there.id == inv.id


@pytest.mark.parametrize(("terms", "days"), [("net_30", 30), ("net_15", 15), ("due_on_receipt", 0)])
def test_send_sets_dates_status_and_enqueues(db, terms, days):
    s = _startup(db)
    inv = _draft(db, s, terms=terms)
    sent = svc.send_invoice(db, startup_id=s.id, invoice_id=inv.id, today=TODAY)
    assert sent.status == InvoiceStatus.sent
    assert sent.issued_on == TODAY
    assert sent.due_on == TODAY + dt.timedelta(days=days)
    if days == 0:
        assert sent.due_on == sent.issued_on
    jobs = db.query(Job).filter_by(type="email.invoice_sent", startup_id=s.id).all()
    assert len(jobs) == 1
    assert jobs[0].payload == {"invoice_id": str(inv.id)}


def test_send_defaults_issued_on_to_today(db):
    s = _startup(db)
    inv = _draft(db, s)
    sent = svc.send_invoice(db, startup_id=s.id, invoice_id=inv.id)
    assert sent.issued_on == dt.datetime.now(dt.UTC).date()


@pytest.mark.parametrize("status", [InvoiceStatus.sent, InvoiceStatus.paid])
def test_send_non_draft_rejected_without_mutation_or_job(db, status):
    s = _startup(db)
    inv = _draft(db, s)
    inv.status = status
    db.flush()
    with pytest.raises(AppError) as exc:
        svc.send_invoice(db, startup_id=s.id, invoice_id=inv.id, today=TODAY)
    assert exc.value.http_status == 422
    assert inv.issued_on is None
    assert inv.due_on is None
    assert db.query(Job).filter_by(type="email.invoice_sent", startup_id=s.id).count() == 0
