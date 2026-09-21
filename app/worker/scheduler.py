from datetime import datetime, timedelta
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import String, cast, exists, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import log
from app.db.models.assessment import Assessment
from app.db.models.enums import AssessmentStatus, MembershipStatus, RoadmapStatus
from app.db.models.membership import Membership
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.db.models.scheduled_run import ScheduledRun
from app.db.models.startup import Startup
from app.platform.jobs import job_dispatcher

SCHED_MISSION = "scheduled.mission.generate"
SCHED_OVERDUE = "scheduled.roadmap.overdue"
SCHED_QUARTERLY = "scheduled.assessment.quarterly"


class Due(NamedTuple):
    task_key: str
    scope_key: str
    period_key: str
    job_type: str
    startup_id: Any
    payload: dict


def _claim(db: Session, task_key: str, scope_key: str, period_key: str) -> bool:
    """Claim (task, scope, period) exactly once. True if newly claimed, False if already taken.

    Inserts inside a SAVEPOINT so an IntegrityError (someone else claimed it) rolls back only
    this insert and leaves the caller's transaction usable.
    """
    try:
        with db.begin_nested():
            db.add(ScheduledRun(task_key=task_key, scope_key=scope_key, period_key=period_key))
            db.flush()
        return True
    except IntegrityError:
        return False


def _local(now: datetime) -> datetime:
    return now.astimezone(ZoneInfo(settings.SCHEDULER_TIMEZONE))


def _quarter_key(d: datetime) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def _active_startup_ids(db: Session) -> list[Any]:
    rows = (
        db.query(Startup.id)
        .join(Membership, Membership.startup_id == Startup.id)
        .filter(Membership.status == MembershipStatus.active, Startup.deleted_at.is_(None))
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


def _due_missions(db: Session, now: datetime) -> list[Due]:
    local = _local(now)
    if local.hour < settings.MISSION_GEN_HOUR:
        return []
    period = local.date().isoformat()
    claimed = {
        r[0]
        for r in db.query(ScheduledRun.scope_key)
        .filter(ScheduledRun.task_key == "mission.generate", ScheduledRun.period_key == period)
        .all()
    }
    return [
        Due("mission.generate", str(sid), period, SCHED_MISSION, sid, {"startup_id": str(sid)})
        for sid in _active_startup_ids(db)
        if str(sid) not in claimed
    ]


def _due_overdue_milestones(db: Session, now: datetime) -> list[Due]:
    today = _local(now).date()
    already_claimed = exists().where(
        ScheduledRun.task_key == "roadmap.overdue",
        ScheduledRun.period_key == "once",
        ScheduledRun.scope_key == cast(RoadmapMilestone.id, String),
    )
    rows = (
        db.query(RoadmapMilestone.id, Roadmap.startup_id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .join(Startup, Roadmap.startup_id == Startup.id)
        .filter(
            RoadmapMilestone.due_on < today,
            RoadmapMilestone.status != RoadmapStatus.done,
            Startup.deleted_at.is_(None),
            ~already_claimed,
        )
        .all()
    )
    return [
        Due(
            "roadmap.overdue",
            str(mid),
            "once",
            SCHED_OVERDUE,
            sid,
            {"startup_id": str(sid), "milestone_id": str(mid)},
        )
        for mid, sid in rows
    ]


def _due_quarterly(db: Session, now: datetime) -> list[Due]:
    period = _quarter_key(_local(now))
    cutoff = now - timedelta(days=settings.QUARTERLY_REASSESS_DAYS)
    latest = (
        db.query(Assessment.startup_id, func.max(Assessment.completed_at).label("last"))
        .filter(Assessment.status == AssessmentStatus.completed)
        .group_by(Assessment.startup_id)
        .subquery()
    )
    due_ids = [
        r[0]
        for r in db.query(latest.c.startup_id)
        .join(Startup, Startup.id == latest.c.startup_id)
        .filter(latest.c.last <= cutoff, Startup.deleted_at.is_(None))
        .all()
    ]
    in_progress = {
        r[0]
        for r in db.query(Assessment.startup_id)
        .filter(Assessment.status == AssessmentStatus.in_progress)
        .all()
    }
    claimed = {
        r[0]
        for r in db.query(ScheduledRun.scope_key)
        .filter(ScheduledRun.task_key == "assessment.quarterly", ScheduledRun.period_key == period)
        .all()
    }
    return [
        Due(
            "assessment.quarterly", str(sid), period, SCHED_QUARTERLY, sid, {"startup_id": str(sid)}
        )
        for sid in due_ids
        if sid not in in_progress and str(sid) not in claimed
    ]


def scheduler_tick(db: Session, *, now: datetime) -> int:
    """Claim + enqueue every due scheduled task. Returns the count enqueued.

    Each (task, scope) is isolated: a lost claim (already fired this period) is a normal skip;
    any other per-item error is logged and the rest proceed. Commits once at the end.
    """
    due = [*_due_missions(db, now), *_due_overdue_milestones(db, now), *_due_quarterly(db, now)]
    enqueued = 0
    for item in due:
        try:
            if _claim(db, item.task_key, item.scope_key, item.period_key):
                job_dispatcher.enqueue(db, item.job_type, item.payload, item.startup_id)
                enqueued += 1
        except Exception as exc:  # noqa: BLE001 - one bad item must not stop the rest
            log.warning(f"[scheduler] {item.task_key}/{item.scope_key} failed: {exc}")
    db.commit()
    return enqueued
