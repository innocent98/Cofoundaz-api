import datetime as dt
import uuid
from typing import NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.enums import InvoiceStatus
from app.db.models.invoice import Invoice
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.finance.errors import _validation
from app.services.finance.invoices import get_invoice_locked

REMINDER_INTERVAL_DAYS = 7
MAX_AUTO_REMINDERS = 8
MANUAL_REMIND_MIN_GAP_HOURS = 24
REMINDER_JOB = "email.invoice_reminder"


class ProcessResult(NamedTuple):
    overdue_event: bool
    reminder_queued: bool


def _local_today(now: dt.datetime) -> dt.date:
    return now.astimezone(ZoneInfo(settings.SCHEDULER_TIMEZONE)).date()


def _is_past_due(inv: Invoice, today: dt.date) -> bool:
    return inv.status == InvoiceStatus.sent and inv.due_on is not None and inv.due_on < today


def _auto_reminder_due(inv: Invoice, now: dt.datetime) -> bool:
    if not inv.auto_remind or inv.reminder_count >= MAX_AUTO_REMINDERS:
        return False
    return inv.last_reminded_at is None or now - inv.last_reminded_at >= dt.timedelta(
        days=REMINDER_INTERVAL_DAYS
    )


def _book_reminder(db: Session, inv: Invoice, now: dt.datetime) -> None:
    inv.last_reminded_at = now
    inv.reminder_count += 1
    db.flush()
    job_dispatcher.enqueue(db, REMINDER_JOB, {"invoice_id": str(inv.id)}, inv.startup_id)


def process_overdue(
    db: Session, *, invoice_id: uuid.UUID, startup_id: uuid.UUID, now: dt.datetime
) -> ProcessResult:
    """Fire the one-shot overdue event and queue an auto reminder when one is due.

    The scan's payload is only a hint, so everything is re-checked under a row lock scoped to
    `startup_id`. A locked row is SKIPPED (another worker is handling it right now), and a
    worker that arrives after the commit sees the bookkeeping columns already set, so a given
    reminder can only be queued once however many ticks, workers or duplicate jobs race.
    """
    inv = db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.startup_id == startup_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if inv is None or not _is_past_due(inv, _local_today(now)):
        return ProcessResult(False, False)

    event_fired = False
    if inv.overdue_notified_at is None:
        inv.overdue_notified_at = now
        db.flush()
        event_bus.publish(
            db,
            "finance.invoice.overdue",
            {
                "startup_id": str(startup_id),
                "invoice_id": str(inv.id),
                "number": inv.number,
                "client_name": inv.client_name,
                "amount_minor": inv.total_minor,
                "currency": inv.currency,
                "due_on": inv.due_on.isoformat() if inv.due_on else None,
                "href": "/finance/invoices",
            },
        )
        event_fired = True

    queued = False
    if _auto_reminder_due(inv, now):
        _book_reminder(db, inv, now)
        queued = True
    return ProcessResult(event_fired, queued)


def send_manual_reminder(
    db: Session, *, startup_id: uuid.UUID, invoice_id: uuid.UUID, now: dt.datetime
) -> Invoice:
    """Queue a reminder on demand. Only for a sent invoice past its due date, and not within
    `MANUAL_REMIND_MIN_GAP_HOURS` of the previous reminder (auto or manual). Resets the auto clock.
    """
    inv = get_invoice_locked(db, startup_id=startup_id, invoice_id=invoice_id)
    if not _is_past_due(inv, now.astimezone(dt.UTC).date()):
        raise _validation("status", "Only a sent invoice that is past due can be reminded.")
    if inv.last_reminded_at is not None and now - inv.last_reminded_at < dt.timedelta(
        hours=MANUAL_REMIND_MIN_GAP_HOURS
    ):
        raise _validation(
            "status",
            f"A reminder was already sent in the last {MANUAL_REMIND_MIN_GAP_HOURS} hours.",
        )
    _book_reminder(db, inv, now)
    return inv
