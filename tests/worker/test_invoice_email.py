import sys
import uuid
from datetime import date

from app.db.models.enums import InvoiceStatus, JobStatus
from app.db.models.invoice import Invoice
from app.db.models.job import Job
from app.platform import email as email_mod
from app.worker.handlers import invoice_email as handler
from tests.factories import create_startup, create_user


def _invoice(db, startup_id, **kw) -> Invoice:
    fields = {
        "startup_id": startup_id,
        "number": "INV-0042",
        "client_name": "Globex Ltd",
        "client_email": "billing@globex.test",
        "line_items": [
            {"description": "Design sprint", "quantity": 2, "unit_price_minor": 50_000},
            {"description": "Hosting setup", "quantity": 1, "unit_price_minor": 25_000},
        ],
        "subtotal_minor": 125_000,
        "tax_minor": 9_375,
        "total_minor": 134_375,
        "currency": "NGN",
        "status": InvoiceStatus.sent,
        "due_on": date(2026, 11, 1),
    }
    fields.update(kw)
    inv = Invoice(**fields)
    db.add(inv)
    db.flush()
    return inv


def _job(invoice_id) -> Job:
    return Job(
        type="email.invoice_sent",
        payload={"invoice_id": str(invoice_id)},
        status=JobStatus.running,
    )


def _sender(monkeypatch) -> email_mod.ConsoleEmailSender:
    sender = email_mod.ConsoleEmailSender()
    monkeypatch.setattr(handler, "get_email_sender", lambda: sender)
    return sender


def test_sends_one_email_with_invoice_details(db, monkeypatch):
    sender = _sender(monkeypatch)
    u = create_user(db)
    s = create_startup(db, owner=u, name="Initech")
    inv = _invoice(db, s.id)

    handler.handle_invoice_email(db, _job(inv.id))

    assert len(sender.sent) == 1
    msg = sender.sent[0]
    assert msg.to == "billing@globex.test"
    assert "INV-0042" in msg.subject
    assert "Initech" in msg.subject
    assert "134375" in msg.html
    assert "Design sprint" in msg.html
    assert "Hosting setup" in msg.html
    assert "100000" in msg.html  # 2 x 50_000 line amount
    assert "2026-11-01" in msg.html


def test_html_escapes_user_controlled_fields(db, monkeypatch):
    sender = _sender(monkeypatch)
    u = create_user(db)
    s = create_startup(db, owner=u)
    inv = _invoice(
        db,
        s.id,
        client_name="<script>alert(1)</script> & Co",
        line_items=[
            {"description": "<script>x</script> & <b>y</b>", "quantity": 1, "unit_price_minor": 5}
        ],
    )

    handler.handle_invoice_email(db, _job(inv.id))

    msg = sender.sent[0]
    assert "<script>" not in msg.html
    assert "<b>y</b>" not in msg.html
    assert "&lt;script&gt;x&lt;/script&gt; &amp; &lt;b&gt;y&lt;/b&gt;" in msg.html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; Co" in msg.html


def test_subject_strips_crlf_from_startup_name(db, monkeypatch):
    sender = _sender(monkeypatch)
    u = create_user(db)
    s = create_startup(db, owner=u, name="Evil\r\nBcc: x@y.test")
    inv = _invoice(db, s.id)

    handler.handle_invoice_email(db, _job(inv.id))

    subject = sender.sent[0].subject
    assert "\r" not in subject
    assert "\n" not in subject


def test_subject_falls_back_when_startup_name_is_none(db, monkeypatch):
    sender = _sender(monkeypatch)
    u = create_user(db)
    s = create_startup(db, owner=u)
    s.name = None
    db.flush()
    inv = _invoice(db, s.id)

    handler.handle_invoice_email(db, _job(inv.id))

    assert sender.sent[0].subject == "Invoice INV-0042 from Cofoundaz"


def test_missing_invoice_is_noop(db, monkeypatch):
    sender = _sender(monkeypatch)

    handler.handle_invoice_email(db, _job(uuid.uuid4()))

    assert sender.sent == []


def test_empty_client_email_is_noop(db, monkeypatch):
    sender = _sender(monkeypatch)
    u = create_user(db)
    s = create_startup(db, owner=u)
    inv = _invoice(db, s.id, client_email="")

    handler.handle_invoice_email(db, _job(inv.id))

    assert sender.sent == []


def test_register_wires_invoice_email_handler():
    from app.worker import runner
    from app.worker.__main__ import register

    sys.modules.pop("app.worker.handlers.invoice_email", None)
    runner.JOB_HANDLERS.clear()
    register()
    assert "email.invoice_sent" in runner.JOB_HANDLERS
