import uuid
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.db.models.enums import RoadmapStatus
from app.db.models.job import Job
from app.db.models.journal import JournalEntry
from app.db.models.marketing import ContentCalendarEntry
from app.db.models.mission import MissionTask
from app.db.models.roadmap import RoadmapMilestone
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.dashboard.kpi_snapshots import capture_snapshots
from app.services.dashboard.service import tasks_done_this_week
from app.services.documents.trash import run_purge
from app.services.finance.reminders import process_overdue
from app.services.marketing.service import publish_due_entry
from app.services.mission.service import get_or_generate_today
from app.services.notifications.digest import send_digest
from app.services.sales.stall import notify_stalled
from app.worker.runner import register_handler
from app.worker.scheduler import (
    SCHED_DOCUMENT_TRASH_PURGE,
    SCHED_DUE_SOON,
    SCHED_HEALTH_NIGHTLY,
    SCHED_INVOICE_OVERDUE,
    SCHED_JOURNAL_NUDGE,
    SCHED_KPI_SNAPSHOT,
    SCHED_MARKETING_PUBLISH,
    SCHED_MISSION,
    SCHED_NOTIF_DIGEST,
    SCHED_OVERDUE,
    SCHED_QUARTERLY,
    SCHED_SALES_STALL,
)


def handle_mission_generate(db: Session, job: Job) -> None:
    startup = db.get(Startup, uuid.UUID(str(job.payload["startup_id"])))
    if startup is None:
        return
    mission = get_or_generate_today(db, startup)
    if mission is None:
        return
    first = (
        db.query(MissionTask).filter_by(mission_id=mission.id).order_by(MissionTask.order).first()
    )
    if first is None:
        return  # a weekends-off rest day (or nothing left to do): no "ready" ping
    event_bus.publish(
        db,
        "mission.ready",
        {
            "startup_id": str(startup.id),
            "mission_id": str(mission.id),
            "first_task_title": first.title,
        },
    )


def handle_roadmap_overdue(db: Session, job: Job) -> None:
    milestone = db.get(RoadmapMilestone, uuid.UUID(str(job.payload["milestone_id"])))
    # re-check: it may have been completed between enqueue and run
    if milestone is None or milestone.status == RoadmapStatus.done:
        return
    event_bus.publish(
        db,
        "roadmap.milestone.overdue",
        {"startup_id": str(job.payload["startup_id"]), "milestone_id": str(milestone.id)},
    )


def _relative(due: date, today: date) -> str:
    days = (due - today).days
    return "today" if days <= 0 else "tomorrow" if days == 1 else f"in {days} days"


def handle_roadmap_due_soon(db: Session, job: Job) -> None:
    milestone = db.get(RoadmapMilestone, uuid.UUID(str(job.payload["milestone_id"])))
    # re-check: completed or rescheduled between enqueue and run
    if milestone is None or milestone.status == RoadmapStatus.done or milestone.due_on is None:
        return
    event_bus.publish(
        db,
        "roadmap.milestone.due_soon",
        {
            "startup_id": str(job.payload["startup_id"]),
            "milestone_id": str(milestone.id),
            "title": milestone.title,
            "relative": _relative(milestone.due_on, date.today()),
        },
    )


def handle_assessment_quarterly(db: Session, job: Job) -> None:
    event_bus.publish(
        db,
        "assessment.quarterly.due",
        {"startup_id": str(job.payload["startup_id"]), "href": "/assessment"},
    )


def handle_journal_nudge(db: Session, job: Job) -> None:
    """One opt-in journal nudge. The reminder is skipped when an entry already exists for the
    local day; it only checks the row's existence, never its content."""
    kind = job.payload["kind"]
    startup_id = uuid.UUID(str(job.payload["startup_id"]))
    founder_id = uuid.UUID(str(job.payload["founder_id"]))
    if kind == "reminder":
        written = (
            db.query(JournalEntry.id)
            .filter_by(
                startup_id=startup_id,
                founder_id=founder_id,
                date=date.fromisoformat(str(job.payload["date"])),
            )
            .first()
        )
        if written is not None:
            return
        event = "journal.reminder"
    else:
        event = "journal.prompt.daily"
    event_bus.publish(
        db,
        event,
        {
            "startup_id": str(startup_id),
            "founder_id": str(founder_id),
            "href": "/journal",
        },
    )


def handle_notification_digest(db: Session, job: Job) -> None:
    """One recipient's digest email; the payload is a hint, preferences are re-read inside."""
    send_digest(
        db,
        user_id=uuid.UUID(str(job.payload["user_id"])),
        startup_id=uuid.UUID(str(job.payload["startup_id"])),
        now=datetime.now(UTC),
    )


def handle_kpi_snapshot(db: Session, job: Job) -> None:
    startup = db.get(Startup, uuid.UUID(str(job.payload["startup_id"])))
    if startup is None:
        return
    capture_snapshots(db, startup, tasks_done_this_week(db, startup))


def handle_health_nightly(db: Session, job: Job) -> None:
    from app.services.health_score.service import refresh_health_score

    startup = db.get(Startup, uuid.UUID(str(job.payload["startup_id"])))
    if startup is not None:
        refresh_health_score(db, startup, trigger="nightly", force=True)


def handle_marketing_publish(db: Session, job: Job) -> None:
    """Flip one due scheduled post to published (status only; nothing is sent to a platform).
    Re-checks the post under a row lock, so an edit made after the scan wins."""
    entry_id = uuid.UUID(str(job.payload["entry_id"]))
    startup_id = str(job.payload["startup_id"])
    # The job payload is only a hint: the post must still belong to the workspace it was
    # scanned under before anything is written.
    entry = db.get(ContentCalendarEntry, entry_id)
    if entry is None or str(entry.startup_id) != startup_id:
        return
    publish_due_entry(db, entry_id=entry_id, now=datetime.now(UTC))


def handle_sales_stall(db: Session, job: Job) -> None:
    """Notify that one deal went quiet. The payload is a hint: `notify_stalled` re-checks the
    deal under a row lock (workspace match, still open, not yet notified, still past threshold)."""
    notify_stalled(
        db,
        deal_id=uuid.UUID(str(job.payload["deal_id"])),
        startup_id=uuid.UUID(str(job.payload["startup_id"])),
        now=datetime.now(UTC),
    )


def handle_invoice_overdue(db: Session, job: Job) -> None:
    """Overdue event + auto reminder for one invoice. The payload is a hint: `process_overdue`
    re-checks workspace, status, due date, spacing and cap under a SKIP LOCKED row lock."""
    process_overdue(
        db,
        invoice_id=uuid.UUID(str(job.payload["invoice_id"])),
        startup_id=uuid.UUID(str(job.payload["startup_id"])),
        now=datetime.now(UTC),
    )


def handle_documents_trash_purge(db: Session, job: Job) -> None:  # noqa: ARG001
    """Delete documents and files whose 30 days in the trash are up. Self-managed because the
    stored objects may only be removed after the row deletions have committed."""
    run_purge(db, now=datetime.now(UTC))


register_handler(SCHED_MISSION, handle_mission_generate)
register_handler(SCHED_OVERDUE, handle_roadmap_overdue)
register_handler(SCHED_DUE_SOON, handle_roadmap_due_soon)
register_handler(SCHED_QUARTERLY, handle_assessment_quarterly)
register_handler(SCHED_KPI_SNAPSHOT, handle_kpi_snapshot)
register_handler(SCHED_NOTIF_DIGEST, handle_notification_digest)
register_handler(SCHED_JOURNAL_NUDGE, handle_journal_nudge)
register_handler(SCHED_HEALTH_NIGHTLY, handle_health_nightly)
register_handler(SCHED_MARKETING_PUBLISH, handle_marketing_publish)
register_handler(SCHED_SALES_STALL, handle_sales_stall)
register_handler(SCHED_INVOICE_OVERDUE, handle_invoice_overdue)
register_handler(
    SCHED_DOCUMENT_TRASH_PURGE, handle_documents_trash_purge, manages_transactions=True
)
