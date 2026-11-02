import uuid
from datetime import date

from app.db.models.enums import InvoiceStatus, JobStatus, MembershipRole
from app.db.models.invoice import Invoice
from app.db.models.job import Job
from app.db.models.user import UserProfile
from app.platform import email as email_mod
from app.worker.handlers import invoice_email as handler
from tests.factories import create_membership, create_startup, create_user


def _setup(db, *, startup_name="Initech", founder="Ada Obi", **inv_kw):
    u = create_user(db)
    u.profile = db.get(UserProfile, u.id) or u.profile
    u.profile.full_name = founder
    s = create_startup(db, owner=u, name=startup_name)
    create_membership(db, u, s, role=MembershipRole.founder)
    fields = {
        "startup_id": s.id,
        "number": "INV-2026-001",
        "client_name": "Chidi Okafor Ltd",
        "client_email": "billing@globex.test",
        "line_items": [{"description": "Work", "quantity": 1, "unit_price_minor": 150_000}],
        "subtotal_minor": 150_000,
        "total_minor": 150_000,
        "currency": "NGN",
        "status": InvoiceStatus.sent,
        "due_on": date(2026, 10, 10),
        "pay_url": "https://pay.example.test/abc",
    }
    fields.update(inv_kw)
    inv = Invoice(**fields)
    db.add(inv)
    db.flush()
    return s, inv


def _job(invoice_id) -> Job:
    return Job(
        type="email.invoice_reminder",
        payload={"invoice_id": str(invoice_id)},
        status=JobStatus.running,
    )


def _sender(monkeypatch):
    sender = email_mod.ConsoleEmailSender()
    monkeypatch.setattr(handler, "get_email_sender", lambda: sender)
    return sender


def test_reminder_copy_subject_and_pdf(db, monkeypatch):
    sender = _sender(monkeypatch)
    _s, inv = _setup(db)
    handler.handle_invoice_reminder(db, _job(inv.id))
    [msg] = sender.sent
    assert msg.to == "billing@globex.test"
    assert msg.subject == "Reminder: invoice INV-2026-001 from Initech"
    assert (
        "Hi Chidi, a friendly nudge that invoice INV-2026-001 for 1,500.00 NGN was due on "
        "2026-10-10. You can view and pay it here: "
    ) in msg.html
    assert 'href="https://pay.example.test/abc"' in msg.html
    assert "Thanks, Ada Obi" in msg.html
    assert "—" not in msg.html and "–" not in msg.html
    [att] = msg.attachments
    assert att.filename == "invoice-INV-2026-001.pdf"
    assert att.content.startswith(b"%PDF-")


def test_no_pay_url_omits_link_sentence(db, monkeypatch):
    sender = _sender(monkeypatch)
    _s, inv = _setup(db, pay_url=None)
    handler.handle_invoice_reminder(db, _job(inv.id))
    html = sender.sent[0].html
    assert "view and pay" not in html
    assert "was due on 2026-10-10. Thanks, Ada Obi" in html


def test_unsafe_pay_url_omits_link(db, monkeypatch):
    sender = _sender(monkeypatch)
    _s, inv = _setup(db, pay_url="javascript:alert(1)")
    handler.handle_invoice_reminder(db, _job(inv.id))
    html = sender.sent[0].html
    assert "javascript" not in html and "view and pay" not in html


def test_founder_falls_back_to_startup_name_and_client_to_there(db, monkeypatch):
    sender = _sender(monkeypatch)
    _s, inv = _setup(db, founder=None, client_name="   ")
    handler.handle_invoice_reminder(db, _job(inv.id))
    html = sender.sent[0].html
    assert html.startswith("<") and "Hi there, a friendly nudge" in html
    assert "Thanks, Initech" in html


def test_hostile_values_escaped_and_subject_has_no_newlines(db, monkeypatch):
    sender = _sender(monkeypatch)
    _s, inv = _setup(
        db,
        startup_name="Evil\r\nBcc: x@y.test",
        founder="<b>Bob</b>",
        client_name="<script>alert(1)</script> Co",
        pay_url='https://pay.example.test/a"onmouseover="x',
    )
    handler.handle_invoice_reminder(db, _job(inv.id))
    [msg] = sender.sent
    assert "\r" not in msg.subject and "\n" not in msg.subject
    assert "<script>" not in msg.html and "<b>" not in msg.html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in msg.html
    assert "&lt;b&gt;Bob&lt;/b&gt;" in msg.html
    assert 'a"onmouseover' not in msg.html


def test_skips_when_no_longer_sent_or_missing(db, monkeypatch):
    sender = _sender(monkeypatch)
    _s, inv = _setup(db, status=InvoiceStatus.paid)
    handler.handle_invoice_reminder(db, _job(inv.id))
    handler.handle_invoice_reminder(db, _job(uuid.uuid4()))
    assert sender.sent == []
