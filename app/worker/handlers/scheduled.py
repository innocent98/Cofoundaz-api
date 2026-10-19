import uuid

from sqlalchemy.orm import Session

from app.db.models.enums import RoadmapStatus
from app.db.models.job import Job
from app.db.models.mission import MissionTask
from app.db.models.roadmap import RoadmapMilestone
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.dashboard.kpi_snapshots import capture_snapshots
from app.services.dashboard.service import tasks_done_this_week
from app.services.mission.service import get_or_generate_today
from app.worker.runner import register_handler
from app.worker.scheduler import (
    SCHED_HEALTH_NIGHTLY,
    SCHED_KPI_SNAPSHOT,
    SCHED_MISSION,
    SCHED_OVERDUE,
    SCHED_QUARTERLY,
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


def handle_assessment_quarterly(db: Session, job: Job) -> None:
    event_bus.publish(
        db, "assessment.quarterly.due", {"startup_id": str(job.payload["startup_id"])}
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


register_handler(SCHED_MISSION, handle_mission_generate)
register_handler(SCHED_OVERDUE, handle_roadmap_overdue)
register_handler(SCHED_QUARTERLY, handle_assessment_quarterly)
register_handler(SCHED_KPI_SNAPSHOT, handle_kpi_snapshot)
register_handler(SCHED_HEALTH_NIGHTLY, handle_health_nightly)
