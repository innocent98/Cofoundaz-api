import uuid

from sqlalchemy.orm import Session

from app.core.logger import log
from app.db.models.job import Job
from app.platform.events import event_bus
from app.services.calendar import assistant, invites
from app.services.calendar import sync as sync_svc
from app.services.calendar.connections import JOB_SYNC
from app.services.calendar.meetings import JOB_INVITES, JOB_SUMMARY
from app.services.calendar.service import JOB_PUSH_DELETE
from app.worker.runner import register_handler
from app.worker.scheduler import SCHED_CALENDAR_REMINDER


def handle_sync(db: Session, job: Job) -> None:
    sync_svc.sync_connection(db, uuid.UUID(str(job.payload["connection_id"])))


def handle_push_delete(db: Session, job: Job) -> None:
    sync_svc.push_deletes(
        db, uuid.UUID(str(job.payload["connection_id"])), list(job.payload["external_ids"])
    )


def handle_invites(db: Session, job: Job) -> None:
    organizer = job.payload.get("organizer_id")
    organizer_id = uuid.UUID(str(organizer)) if organizer else None
    if job.payload.get("cancel_snapshot"):
        invites.send_cancel_snapshot(db, job.payload["cancel_snapshot"], organizer_id)
        return
    invites.send_pending(
        db,
        event_id=uuid.UUID(str(job.payload["event_id"])),
        organizer_id=organizer_id,
        cancel_emails=list(job.payload.get("cancel_emails", [])),
    )


def handle_summary(db: Session, job: Job) -> None:
    event_id = uuid.UUID(str(job.payload["event_id"]))
    try:
        assistant.summarise(db, event_id)
    except (
        Exception
    ) as exc:  # noqa: BLE001 - a failed model call must end the turn, not retry-burn tokens
        log.warning(f"[calendar] summary for {event_id} failed: {exc}")
        assistant.mark_failed(
            db, event_id, "The assistant could not write this summary. Try again."
        )


def handle_reminder(db: Session, job: Job) -> None:
    p = job.payload
    event_bus.publish(
        db,
        "calendar.meeting.reminder" if p["kind"] == "meeting" else "calendar.reminder",
        {
            "startup_id": p["startup_id"],
            "kind": p["kind"],
            "source": p["source"],
            "source_id": p["source_id"],
            "title": p["title"],
            "offset_minutes": p["offset_minutes"],
            "date": p["date"],
            "user_ids": p["user_ids"],
        },
    )


register_handler(JOB_SYNC, handle_sync)
register_handler(JOB_PUSH_DELETE, handle_push_delete)
register_handler(JOB_INVITES, handle_invites)
register_handler(JOB_SUMMARY, handle_summary)
register_handler(SCHED_CALENDAR_REMINDER, handle_reminder)
