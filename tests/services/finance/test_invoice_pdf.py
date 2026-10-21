from datetime import UTC, date, datetime

from app.db.models.enums import InvoiceStatus
from app.db.models.invoice import Invoice
from app.services.finance.invoice_pdf import render_invoice_pdf


def _inv(**kw) -> Invoice:
    fields = {
        "number": "INV-2026-007",
        "client_name": "Globex Ltd",
        "client_email": "billing@globex.test",
        "line_items": [
            {"description": "Design sprint", "quantity": 2, "unit_price_minor": 50_000},
            {"description": "Hosting setup", "quantity": 1, "unit_price_minor": 25_000},
        ],
        "subtotal_minor": 125_000,
        "tax_percent": 7.5,
        "tax_minor": 9_375,
        "total_minor": 134_375,
        "currency": "NGN",
        "status": InvoiceStatus.sent,
        "issued_on": date(2026, 10, 1),
        "due_on": date(2026, 10, 31),
    }
    fields.update(kw)
    return Invoice(**fields)


def test_pdf_structure_and_content():
    data = render_invoice_pdf(_inv(), "Initech")
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    assert b"INV-2026-007" in data
    assert b"Globex Ltd" in data
    assert b"Initech" in data
    assert b"1,343.75" in data
    assert b"Design sprint" in data
    assert b"PAID" not in data


def test_paid_stamp_only_when_paid():
    paid = _inv(status=InvoiceStatus.paid, paid_at=datetime(2026, 10, 5, tzinfo=UTC))
    assert b"PAID" in render_invoice_pdf(paid, "Initech")


def test_hostile_and_non_latin1_text_is_escaped():
    data = render_invoice_pdf(_inv(client_name="A ( ) \\ ₦ é Co"), "Init(ech")
    assert b"\\(" in data
    assert b"\\)" in data
    assert b"\\\\" in data
    assert b"?" in data
    assert "₦".encode() not in data
    assert data.rstrip().endswith(b"%%EOF")


def test_many_lines_paginate():
    items = [
        {"description": f"Item {i}", "quantity": 1, "unit_price_minor": 100} for i in range(70)
    ]
    data = render_invoice_pdf(_inv(line_items=items), "Initech")
    assert data.count(b"/Type /Page\n") >= 3
    assert b"Item 69" in data
