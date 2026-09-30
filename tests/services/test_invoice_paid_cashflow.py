import datetime as dt
import uuid

import pytest
from sqlalchemy import event

from app.core.errors import AppError, NotFound
from app.db.models.enums import InvoiceStatus, TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from app.schemas.invoice import InvoiceCreate, LineItem
from app.services.finance import invoices as svc
from app.services.finance.cashflow import cash_flow_summary
from tests.factories import create_startup, create_user


def _startup(db):
    return create_startup(db, owner=create_user(db))


def _sent(db, startup):
    inv = svc.create_invoice(
        db,
        startup_id=startup.id,
        data=InvoiceCreate(
            client_name="Acme Ltd",
            client_email="billing@acme-client.com",
            line_items=[LineItem(description="Design", quantity=2, unit_price_minor=50000)],
            tax_percent=10,
            terms="net_30",
        ),
    )
    return svc.send_invoice(db, startup_id=startup.id, invoice_id=inv.id)


def _txns(db, startup):
    return db.query(Transaction).filter_by(startup_id=startup.id).all()


def _cash(db, startup) -> int:
    return cash_flow_summary(db, startup_id=startup.id)["cash_on_hand"]


def test_mark_paid_creates_exactly_one_inflow(db):
    s = _startup(db)
    inv = _sent(db, s)
    paid = svc.mark_paid(db, startup_id=s.id, invoice_id=inv.id)
    assert paid.status == InvoiceStatus.paid
    assert paid.paid_at is not None
    rows = _txns(db, s)
    assert len(rows) == 1
    txn = rows[0]
    assert txn.direction == TransactionDirection.inflow
    assert txn.amount_minor == paid.total_minor == 110000
    assert txn.source == TransactionSource.invoice
    assert txn.category == "Revenue"
    assert txn.currency == paid.currency
    assert paid.number in txn.description
    assert paid.transaction_id == txn.id


def test_mark_paid_is_idempotent_and_cash_rises_once(db):
    s = _startup(db)
    inv = _sent(db, s)
    before = _cash(db, s)
    first = svc.mark_paid(db, startup_id=s.id, invoice_id=inv.id)
    first_txn_id = first.transaction_id
    first_paid_at = first.paid_at
    second = svc.mark_paid(db, startup_id=s.id, invoice_id=inv.id)
    assert second.transaction_id == first_txn_id
    assert second.paid_at == first_paid_at
    assert len(_txns(db, s)) == 1
    assert _cash(db, s) - before == inv.total_minor


def test_mark_paid_guard_on_transaction_id_even_if_status_not_paid(db):
    s = _startup(db)
    inv = _sent(db, s)
    paid = svc.mark_paid(db, startup_id=s.id, invoice_id=inv.id)
    paid.status = InvoiceStatus.sent  # simulate drift: linked txn exists but status regressed
    db.flush()
    again = svc.mark_paid(db, startup_id=s.id, invoice_id=inv.id)
    assert again.transaction_id == paid.transaction_id
    assert len(_txns(db, s)) == 1


def test_mark_paid_from_draft_rejected(db):
    s = _startup(db)
    draft = svc.create_invoice(
        db,
        startup_id=s.id,
        data=InvoiceCreate(
            client_name="Acme Ltd",
            client_email="billing@acme-client.com",
            line_items=[LineItem(description="Design", quantity=1, unit_price_minor=100)],
            tax_percent=0,
            terms="net_30",
        ),
    )
    with pytest.raises(AppError) as exc:
        svc.mark_paid(db, startup_id=s.id, invoice_id=draft.id)
    assert exc.value.http_status == 422
    assert draft.status == InvoiceStatus.draft
    assert draft.transaction_id is None
    assert _txns(db, s) == []


def test_mark_unpaid_reverses_inflow(db):
    s = _startup(db)
    inv = _sent(db, s)
    before = _cash(db, s)
    paid = svc.mark_paid(db, startup_id=s.id, invoice_id=inv.id)
    txn_id = paid.transaction_id
    assert _cash(db, s) == before + inv.total_minor
    unpaid = svc.mark_unpaid(db, startup_id=s.id, invoice_id=inv.id)
    assert unpaid.status == InvoiceStatus.sent
    assert unpaid.transaction_id is None
    assert unpaid.paid_at is None
    assert db.query(Transaction).filter_by(id=txn_id).one_or_none() is None
    assert _txns(db, s) == []
    assert _cash(db, s) == before


def test_mark_paid_again_after_unpaid_creates_fresh_single_inflow(db):
    s = _startup(db)
    inv = _sent(db, s)
    svc.mark_paid(db, startup_id=s.id, invoice_id=inv.id)
    svc.mark_unpaid(db, startup_id=s.id, invoice_id=inv.id)
    repaid = svc.mark_paid(db, startup_id=s.id, invoice_id=inv.id)
    assert repaid.status == InvoiceStatus.paid
    assert len(_txns(db, s)) == 1


@pytest.mark.parametrize("status", [InvoiceStatus.draft, InvoiceStatus.sent])
def test_mark_unpaid_on_non_paid_rejected(db, status):
    s = _startup(db)
    inv = _sent(db, s)
    inv.status = status
    db.flush()
    with pytest.raises(AppError) as exc:
        svc.mark_unpaid(db, startup_id=s.id, invoice_id=inv.id)
    assert exc.value.http_status == 422
    assert inv.status == status


def test_mark_paid_and_unpaid_cross_tenant_not_found(db):
    s = _startup(db)
    other = _startup(db)
    inv = _sent(db, s)
    with pytest.raises(NotFound):
        svc.mark_paid(db, startup_id=other.id, invoice_id=inv.id)
    with pytest.raises(NotFound):
        svc.mark_unpaid(db, startup_id=other.id, invoice_id=inv.id)
    with pytest.raises(NotFound):
        svc.mark_paid(db, startup_id=s.id, invoice_id=uuid.uuid4())
    assert _txns(db, s) == []


def test_mark_paid_dates_inflow_on_paid_day(db):
    s = _startup(db)
    inv = _sent(db, s)
    when = dt.datetime.now(dt.UTC)
    svc.mark_paid(db, startup_id=s.id, invoice_id=inv.id, now=when)
    assert _txns(db, s)[0].date == when.date()


@pytest.mark.parametrize("op", ["mark_paid", "mark_unpaid"])
def test_money_paths_take_a_row_lock(db, op):
    """The race guard: mark-paid/unpaid must load the invoice FOR UPDATE."""
    s = _startup(db)
    inv = _sent(db, s)
    if op == "mark_unpaid":
        svc.mark_paid(db, startup_id=s.id, invoice_id=inv.id)
    statements: list[str] = []

    def _capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        getattr(svc, op)(db, startup_id=s.id, invoice_id=inv.id)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    locked = [q for q in statements if "FROM invoices" in q and "FOR UPDATE" in q]
    assert locked, statements


def test_mark_unpaid_tolerates_missing_linked_transaction(db):
    s = _startup(db)
    inv = _sent(db, s)
    paid = svc.mark_paid(db, startup_id=s.id, invoice_id=inv.id)
    txn = db.get(Transaction, paid.transaction_id)
    db.delete(txn)
    db.flush()
    unpaid = svc.mark_unpaid(db, startup_id=s.id, invoice_id=inv.id)
    assert unpaid.status == InvoiceStatus.sent
    assert unpaid.transaction_id is None
    assert unpaid.paid_at is None
