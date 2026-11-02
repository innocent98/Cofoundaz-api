import uuid
from datetime import UTC, date, datetime, timedelta

from app.db.models.enums import InvoiceStatus, MembershipRole
from app.db.models.invoice import Invoice
from app.db.models.job import Job
from app.platform import events as events_mod
from app.services.finance.reminders import MAX_AUTO_REMINDERS
from app.worker import scheduler
from app.worker.scheduler import SCHED_INVOICE_OVERDUE, scheduler_tick
from tests.factories import create_membership, create_startup, create_user

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


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
        "line_items": [],
        "total_minor": 1000,
        "currency": "NGN",
        "status": InvoiceStatus.sent,
        "due_on": date(2026, 10, 10),
        "auto_remind": True,
    }
    fields.update(kw)
    inv = Invoice(**fields)
    db.add(inv)
    db.flush()
    return inv


def _ids(db, now=NOW):
    return {d.scope_key for d in scheduler._due_invoice_overdue(db, now)}


def test_due_item_shape(db):
    _u, s = _ws(db)
    inv = _inv(db, s.id)
    [item] = scheduler._due_invoice_overdue(db, NOW)
    assert item.task_key == "finance.invoice_overdue"
    assert item.scope_key == str(inv.id)
    assert item.period_key == "2026-10-21"
    assert item.job_type == SCHED_INVOICE_OVERDUE == "scheduled.finance.invoice_overdue"
    assert item.startup_id == s.id
    assert item.payload == {"startup_id": str(s.id), "invoice_id": str(inv.id)}


def test_overdue_unnotified_is_picked_even_with_auto_remind_off(db):
    _u, s = _ws(db)
    inv = _inv(db, s.id, auto_remind=False)
    assert _ids(db) == {str(inv.id)}


def test_skips_paid_draft_not_due_and_deleted_workspace(db):
    _u, s = _ws(db)
    _u2, gone = _ws(db)
    _inv(db, s.id, status=InvoiceStatus.paid)
    _inv(db, s.id, status=InvoiceStatus.draft, due_on=None)
    _inv(db, s.id, due_on=NOW.date())
    _inv(db, gone.id)
    gone.deleted_at = NOW
    db.flush()
    assert _ids(db) == set()


def test_auto_remind_after_seven_days_only(db):
    _u, s = _ws(db)
    sixd = _inv(
        db,
        s.id,
        overdue_notified_at=NOW - timedelta(days=30),
        last_reminded_at=NOW - timedelta(days=6),
        reminder_count=1,
    )
    sevend = _inv(
        db,
        s.id,
        overdue_notified_at=NOW - timedelta(days=30),
        last_reminded_at=NOW - timedelta(days=7),
        reminder_count=1,
    )
    never_reminded = _inv(db, s.id, overdue_notified_at=NOW - timedelta(days=30))
    assert _ids(db) == {str(sevend.id), str(never_reminded.id)}
    assert str(sixd.id) not in _ids(db)


def test_notified_with_auto_remind_off_or_cap_reached_is_skipped(db):
    _u, s = _ws(db)
    old = NOW - timedelta(days=40)
    _inv(db, s.id, auto_remind=False, overdue_notified_at=old, last_reminded_at=old)
    _inv(
        db,
        s.id,
        overdue_notified_at=old,
        last_reminded_at=old,
        reminder_count=MAX_AUTO_REMINDERS,
    )
    assert _ids(db) == set()


def test_ordered_by_due_date_and_capped(db, monkeypatch):
    _u, s = _ws(db)
    monkeypatch.setattr(scheduler, "INVOICE_OVERDUE_BATCH", 2)
    a = _inv(db, s.id, due_on=date(2026, 9, 1))
    b = _inv(db, s.id, due_on=date(2026, 9, 2))
    _inv(db, s.id, due_on=date(2026, 9, 3))
    assert [d.scope_key for d in scheduler._due_invoice_overdue(db, NOW)] == [str(a.id), str(b.id)]


def test_two_ticks_same_day_enqueue_once(db):
    _u, s = _ws(db)
    _inv(db, s.id)
    scheduler_tick(db, now=NOW)
    scheduler_tick(db, now=NOW + timedelta(hours=3))
    jobs = db.query(Job).filter_by(type=SCHED_INVOICE_OVERDUE, startup_id=s.id).all()
    assert len(jobs) == 1


def test_handler_end_to_end_and_second_day_is_quiet(db, monkeypatch):
    from app.worker.handlers import scheduled

    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    _u, s = _ws(db)
    inv = _inv(db, s.id)
    scheduler_tick(db, now=NOW)
    job = db.query(Job).filter_by(type=SCHED_INVOICE_OVERDUE, startup_id=s.id).one()
    scheduled.handle_invoice_overdue(db, job)
    scheduled.handle_invoice_overdue(db, job)  # duplicate job: no second send
    assert [e for e, _ in published] == ["finance.invoice.overdue"]
    assert len(db.query(Job).filter_by(type="email.invoice_reminder").all()) == 1
    db.refresh(inv)
    assert inv.reminder_count == 1
    inv.last_reminded_at = NOW  # the handler stamps wall-clock now; pin it for the day maths
    db.flush()
    # next day: notified + spaced 7 days, so no new candidate
    assert _ids(db, NOW + timedelta(days=1)) == set()
    assert _ids(db, NOW + timedelta(days=7)) == {str(inv.id)}


def test_handler_ignores_foreign_workspace_payload(db, monkeypatch):
    from app.worker.handlers import scheduled

    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    _u, s = _ws(db)
    _u2, other = _ws(db)
    inv = _inv(db, s.id)
    job = Job(
        type=SCHED_INVOICE_OVERDUE,
        payload={"startup_id": str(other.id), "invoice_id": str(inv.id)},
    )
    scheduled.handle_invoice_overdue(db, job)
    assert published == []
