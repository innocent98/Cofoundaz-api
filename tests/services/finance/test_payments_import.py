from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.db.models.enums import InvoiceStatus, TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from app.services.finance.payments import importer
from tests.services.finance.payments_helpers import (
    charge,
    fee,
    make_connection,
    make_invoice,
    make_workspace,
    refund,
)


@pytest.fixture(params=["autoflush_on", "autoflush_off"])
def session(request, engine) -> Iterator[Session]:
    """The shared rollback-wrapped session, plus an autoflush=False variant (prod SessionLocal)."""
    connection = engine.connect()
    trans = connection.begin()
    s = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=request.param == "autoflush_on",
    )
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        connection.close()


def _rows(db, conn):
    return {t.external_ref: t for t in db.query(Transaction).filter_by(connection_id=conn.id).all()}


def test_first_import_creates_charge_and_fee_rows_with_exact_kobo(session):
    _u, s = make_workspace(session)
    conn = make_connection(session, s)
    res = importer.import_items(
        session, connection=conn, items=[charge("r1", 500000), fee("r1", 7500)]
    )
    assert (res.imported, res.fees, res.refunds, res.duplicates) == (1, 1, 0, 0)
    rows = _rows(session, conn)
    c, f = rows["r1"], rows["r1:fee"]
    assert (c.amount_minor, c.direction, c.category) == (
        500000,
        TransactionDirection.inflow,
        "Revenue",
    )
    assert c.source == TransactionSource.paystack and c.currency == "NGN"
    assert c.date.isoformat() == "2026-10-01"
    assert (f.amount_minor, f.direction, f.category) == (
        7500,
        TransactionDirection.outflow,
        "Payment fees",
    )


def test_rerun_is_idempotent(session):
    _u, s = make_workspace(session)
    conn = make_connection(session, s, provider="flutterwave")
    items = [charge("r1", 500000), fee("r1", 7500), refund("9", 1000, parent="r1")]
    importer.import_items(session, connection=conn, items=items)
    res = importer.import_items(session, connection=conn, items=items)
    assert (res.imported, res.fees, res.refunds) == (0, 0, 0)
    assert res.duplicates == 3
    assert session.query(Transaction).filter_by(connection_id=conn.id).count() == 3
    assert all(t.source == TransactionSource.flutterwave for t in _rows(session, conn).values())


def test_duplicate_inside_one_batch_does_not_poison_session(session):
    _u, s = make_workspace(session)
    conn = make_connection(session, s)
    res = importer.import_items(
        session, connection=conn, items=[charge("r1", 100), charge("r1", 100), charge("r2", 200)]
    )
    assert (res.imported, res.duplicates) == (2, 1)
    assert session.query(Transaction).filter_by(connection_id=conn.id).count() == 2


def test_non_ngn_skipped_and_counted(session):
    _u, s = make_workspace(session)
    conn = make_connection(session, s)
    res = importer.import_items(
        session,
        connection=conn,
        items=[charge("usd", 5000, currency="USD"), fee("usd", 50, currency="USD")],
    )
    assert res.skipped_currency == 2 and res.imported == 0
    assert session.query(Transaction).filter_by(connection_id=conn.id).count() == 0


def test_int32_overflow_and_nonpositive_skipped_without_error(session):
    _u, s = make_workspace(session)
    conn = make_connection(session, s)
    res = importer.import_items(
        session,
        connection=conn,
        items=[
            charge("big", 2_147_483_648),
            charge("edge", 2_147_483_647),
            charge("zero", 0),
            charge("neg", -5),
        ],
    )
    assert res.skipped_oversize == 3 and res.imported == 1
    assert list(_rows(session, conn)) == ["edge"]


def test_refund_for_unknown_parent_and_partial_refund_each_make_one_out_row(session):
    _u, s = make_workspace(session)
    conn = make_connection(session, s)
    res = importer.import_items(
        session,
        connection=conn,
        items=[
            charge("r1", 500000),
            refund("700", 200000, parent="r1"),  # partial
            refund("701", 300000, parent="never_imported"),
            refund("702", 100, parent=None),
        ],
    )
    assert res.refunds == 3
    rows = _rows(session, conn)
    for rid in ("700", "701", "702"):
        assert rows[rid].direction == TransactionDirection.outflow
        assert rows[rid].category == "Refunds"
    assert rows["700"].amount_minor == 200000


def test_charge_matching_invoice_pay_reference_marks_paid_not_inflow(session):
    _u, s = make_workspace(session)
    conn = make_connection(session, s)
    inv = make_invoice(session, s, reference="inv_ref_1", total=500000)
    res = importer.import_items(
        session,
        connection=conn,
        items=[charge("inv_ref_1", 500000), fee("inv_ref_1", 7500), charge("other", 100)],
    )
    session.refresh(inv)
    assert inv.status == InvoiceStatus.paid and inv.paid_via == "paystack"
    assert res.invoice_matched == 1 and res.imported == 1 and res.fees == 1
    inflows = (
        session.query(Transaction)
        .filter_by(startup_id=s.id, direction=TransactionDirection.inflow)
        .all()
    )
    assert sorted(t.source.value for t in inflows) == ["invoice", "paystack"]
    assert "inv_ref_1" not in _rows(session, conn)

    again = importer.import_items(session, connection=conn, items=[charge("inv_ref_1", 500000)])
    assert again.invoice_matched == 0 and again.duplicates == 1
    assert (
        session.query(Transaction)
        .filter_by(startup_id=s.id, direction=TransactionDirection.inflow)
        .count()
        == 2
    )


def test_invoice_reference_of_another_workspace_is_not_matched(session):
    _u, s = make_workspace(session)
    _u2, other = make_workspace(session)
    conn = make_connection(session, s)
    inv = make_invoice(session, other, reference="shared_ref")
    res = importer.import_items(session, connection=conn, items=[charge("shared_ref", 100)])
    session.refresh(inv)
    assert inv.status == InvoiceStatus.sent
    assert res.invoice_matched == 0 and res.imported == 1


def test_underpayment_does_not_settle_the_invoice(session):
    """A charge below the invoice total is real money but must not mark the invoice paid."""
    _u, s = make_workspace(session)
    conn = make_connection(session, s)
    inv = make_invoice(session, s, reference="inv_ref_1", total=500000)
    res = importer.import_items(session, connection=conn, items=[charge("inv_ref_1", 100000)])
    session.refresh(inv)
    assert inv.status == InvoiceStatus.sent and inv.paid_via is None
    assert res.invoice_matched == 0 and res.imported == 1
    assert _rows(session, conn)["inv_ref_1"].amount_minor == 100000
