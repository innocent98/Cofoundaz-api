import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import InvoiceStatus, MembershipRole
from app.db.models.invoice import Invoice
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform import events as events_mod
from app.services.finance.invoices import serialize_invoice
from app.services.finance.reminders import (
    MANUAL_REMIND_MIN_GAP_HOURS,
    MAX_AUTO_REMINDERS,
    REMINDER_INTERVAL_DAYS,
    ProcessResult,
    process_overdue,
    send_manual_reminder,
)
from tests.factories import create_membership, create_startup, create_user

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
DUE = date(2026, 10, 10)


def _ws(db):
    u = create_user(db, email_verified_at=NOW)
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    return u, s


def _inv(db, startup_id, **kw) -> Invoice:
    fields = {
        "startup_id": startup_id,
        "number": f"INV-2026-{uuid.uuid4().hex[:6]}",
        "client_name": "Globex Ltd",
        "client_email": "billing@globex.test",
        "line_items": [{"description": "Work", "quantity": 1, "unit_price_minor": 150_000}],
        "subtotal_minor": 150_000,
        "total_minor": 150_000,
        "currency": "NGN",
        "status": InvoiceStatus.sent,
        "issued_on": date(2026, 9, 10),
        "due_on": DUE,
        "auto_remind": True,
    }
    fields.update(kw)
    inv = Invoice(**fields)
    db.add(inv)
    db.flush()
    return inv


def _events(monkeypatch):
    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    return published


def _reminder_jobs(db, startup_id):
    return db.query(Job).filter_by(type="email.invoice_reminder", startup_id=startup_id).all()


def test_constants():
    assert REMINDER_INTERVAL_DAYS == 7
    assert MAX_AUTO_REMINDERS == 8
    assert MANUAL_REMIND_MIN_GAP_HOURS == 24


def test_sent_past_due_serialises_overdue_and_future_does_not(db):
    _u, s = _ws(db)
    late = _inv(db, s.id)
    fresh = _inv(db, s.id, due_on=NOW.date() + timedelta(days=3))
    assert serialize_invoice(late, today=NOW.date()).status == "overdue"
    assert serialize_invoice(fresh, today=NOW.date()).status == "sent"


def test_first_run_fires_event_and_queues_reminder(db, monkeypatch):
    published = _events(monkeypatch)
    _u, s = _ws(db)
    inv = _inv(db, s.id)
    res = process_overdue(db, invoice_id=inv.id, startup_id=s.id, now=NOW)
    assert res == ProcessResult(overdue_event=True, reminder_queued=True)
    assert [e for e, _ in published] == ["finance.invoice.overdue"]
    payload = published[0][1]
    assert payload == {
        "startup_id": str(s.id),
        "invoice_id": str(inv.id),
        "number": inv.number,
        "client_name": "Globex Ltd",
        "amount_minor": 150_000,
        "currency": "NGN",
        "due_on": "2026-10-10",
        "href": "/finance/invoices",
    }
    assert inv.overdue_notified_at == NOW
    assert inv.last_reminded_at == NOW
    assert inv.reminder_count == 1
    jobs = _reminder_jobs(db, s.id)
    assert len(jobs) == 1 and jobs[0].payload == {"invoice_id": str(inv.id)}


def test_not_due_or_not_sent_or_wrong_workspace_does_nothing(db, monkeypatch):
    published = _events(monkeypatch)
    _u, s = _ws(db)
    _u2, other = _ws(db)
    not_due = _inv(db, s.id, due_on=NOW.date())  # due today is not yet overdue
    paid = _inv(db, s.id, status=InvoiceStatus.paid)
    draft = _inv(db, s.id, status=InvoiceStatus.draft, due_on=None)
    ok = _inv(db, s.id)
    none = ProcessResult(False, False)
    for inv in (not_due, paid, draft):
        assert process_overdue(db, invoice_id=inv.id, startup_id=s.id, now=NOW) == none
    # the payload's startup id is only a hint: a mismatch touches nothing
    assert process_overdue(db, invoice_id=ok.id, startup_id=other.id, now=NOW) == none
    assert process_overdue(db, invoice_id=uuid.uuid4(), startup_id=s.id, now=NOW) == none
    assert published == []
    assert ok.reminder_count == 0 and ok.overdue_notified_at is None
    assert _reminder_jobs(db, s.id) == []


def test_seven_day_spacing(db, monkeypatch):
    published = _events(monkeypatch)
    _u, s = _ws(db)
    inv = _inv(db, s.id)
    process_overdue(db, invoice_id=inv.id, startup_id=s.id, now=NOW)
    early = process_overdue(
        db, invoice_id=inv.id, startup_id=s.id, now=NOW + timedelta(days=6, hours=23)
    )
    assert early == ProcessResult(False, False)
    assert len(_reminder_jobs(db, s.id)) == 1
    on_time = process_overdue(db, invoice_id=inv.id, startup_id=s.id, now=NOW + timedelta(days=7))
    assert on_time == ProcessResult(False, True)  # event never repeats
    assert len(_reminder_jobs(db, s.id)) == 2
    assert inv.reminder_count == 2
    assert [e for e, _ in published] == ["finance.invoice.overdue"]


def test_cap_stops_auto_reminders(db, monkeypatch):
    _events(monkeypatch)
    _u, s = _ws(db)
    inv = _inv(
        db,
        s.id,
        reminder_count=MAX_AUTO_REMINDERS,
        last_reminded_at=NOW - timedelta(days=30),
        overdue_notified_at=NOW - timedelta(days=60),
    )
    res = process_overdue(db, invoice_id=inv.id, startup_id=s.id, now=NOW)
    assert res == ProcessResult(False, False)
    assert _reminder_jobs(db, s.id) == []


def test_auto_remind_off_fires_event_but_no_reminder(db, monkeypatch):
    published = _events(monkeypatch)
    _u, s = _ws(db)
    inv = _inv(db, s.id, auto_remind=False)
    res = process_overdue(db, invoice_id=inv.id, startup_id=s.id, now=NOW)
    assert res == ProcessResult(True, False)
    assert [e for e, _ in published] == ["finance.invoice.overdue"]
    assert inv.reminder_count == 0 and inv.last_reminded_at is None


def test_auto_remind_toggled_on_after_event_sends_reminder_without_event(db, monkeypatch):
    published = _events(monkeypatch)
    _u, s = _ws(db)
    inv = _inv(db, s.id, auto_remind=False)
    process_overdue(db, invoice_id=inv.id, startup_id=s.id, now=NOW)
    inv.auto_remind = True
    db.flush()
    res = process_overdue(db, invoice_id=inv.id, startup_id=s.id, now=NOW + timedelta(days=1))
    assert res == ProcessResult(False, True)
    assert [e for e, _ in published] == ["finance.invoice.overdue"]
    assert len(_reminder_jobs(db, s.id)) == 1


def test_paid_between_scan_and_handle_sends_nothing(db, monkeypatch):
    published = _events(monkeypatch)
    _u, s = _ws(db)
    inv = _inv(db, s.id)
    inv.status = InvoiceStatus.paid  # paid after the scan picked it
    db.flush()
    assert process_overdue(db, invoice_id=inv.id, startup_id=s.id, now=NOW) == ProcessResult(
        False, False
    )
    assert published == [] and _reminder_jobs(db, s.id) == []


def test_locked_row_is_skipped_by_second_session(engine, monkeypatch):
    """Two real sessions (autoflush off like prod): while A holds the row lock, B skips it."""
    published = _events(monkeypatch)
    a = Session(bind=engine, autoflush=False)
    b = Session(bind=engine, autoflush=False)
    user_id = startup_id = None
    try:
        user, startup = _ws(a)
        inv = _inv(a, startup.id)
        a.commit()
        user_id, startup_id, inv_id = user.id, startup.id, inv.id

        a.query(Invoice).filter_by(id=inv_id).with_for_update().one()
        res = process_overdue(b, invoice_id=inv_id, startup_id=startup_id, now=NOW)
        assert res == ProcessResult(False, False)
        b.commit()
        assert published == []
        assert _reminder_jobs(b, startup_id) == []
        a.rollback()  # release the lock

        first = process_overdue(b, invoice_id=inv_id, startup_id=startup_id, now=NOW)
        b.commit()
        assert first == ProcessResult(True, True)
        again = process_overdue(a, invoice_id=inv_id, startup_id=startup_id, now=NOW)
        a.commit()
        assert again == ProcessResult(False, False)
        assert len(_reminder_jobs(b, startup_id)) == 1
    finally:
        a.rollback()
        b.rollback()
        if startup_id is not None:
            a.query(Job).filter_by(startup_id=startup_id).delete()
            a.query(Startup).filter_by(id=startup_id).delete()
            a.query(User).filter_by(id=user_id).delete()
            a.commit()
        a.close()
        b.close()


# ---- manual reminders ----


def test_manual_reminder_queues_and_resets_clock(db):
    _u, s = _ws(db)
    inv = _inv(db, s.id, auto_remind=False)
    out = send_manual_reminder(db, startup_id=s.id, invoice_id=inv.id, now=NOW)
    assert out.id == inv.id
    assert inv.last_reminded_at == NOW and inv.reminder_count == 1
    assert len(_reminder_jobs(db, s.id)) == 1
    # the manual send resets the auto clock: 6 days later the auto path stays quiet
    inv.auto_remind = True
    db.flush()
    res = process_overdue(db, invoice_id=inv.id, startup_id=s.id, now=NOW + timedelta(days=6))
    assert res.reminder_queued is False


def test_manual_reminder_rejects_not_overdue(db):
    _u, s = _ws(db)
    paid = _inv(db, s.id, status=InvoiceStatus.paid)
    draft = _inv(db, s.id, status=InvoiceStatus.draft, due_on=None)
    future = _inv(db, s.id, due_on=NOW.date() + timedelta(days=2))
    for inv in (paid, draft, future):
        with pytest.raises(AppError) as exc:
            send_manual_reminder(db, startup_id=s.id, invoice_id=inv.id, now=NOW)
        assert exc.value.http_status == 422


def test_manual_reminder_24h_gap(db):
    _u, s = _ws(db)
    inv = _inv(db, s.id, last_reminded_at=NOW - timedelta(hours=23))
    with pytest.raises(AppError) as exc:
        send_manual_reminder(db, startup_id=s.id, invoice_id=inv.id, now=NOW)
    assert exc.value.http_status == 422
    inv.last_reminded_at = NOW - timedelta(hours=24)
    db.flush()
    send_manual_reminder(db, startup_id=s.id, invoice_id=inv.id, now=NOW)
    assert inv.reminder_count == 1


def test_manual_reminder_cross_tenant_is_not_found(db):
    _u, s = _ws(db)
    _u2, other = _ws(db)
    inv = _inv(db, s.id)
    with pytest.raises(NotFound):
        send_manual_reminder(db, startup_id=other.id, invoice_id=inv.id, now=NOW)
