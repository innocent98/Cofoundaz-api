"""Lazy-on-read mission generation, derived streak, and mission serialization.

READ-ONLY against the roadmap: this module only queries `Roadmap`/`RoadmapPhase`/
`RoadmapMilestone`/`RoadmapTask` to select candidate tasks -- it never writes to
those tables. `mission_tasks.roadmap_task_id` is an unconstrained soft link (no
FK), so a roadmap task disappearing later never blocks or cascades on a mission
snapshot that already copied its title/effort.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.db.models.enums import MissionStatus, MissionTaskStatus, RoadmapStatus
from app.db.models.mission import Mission, MissionSettings, MissionTask
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase, RoadmapTask
from app.db.models.startup import Startup

_DEFAULT_MISSION_SIZE = 3
_WEEKEND_ISO_WEEKDAYS = (5, 6)  # Saturday, Sunday (date.weekday())


def _today() -> date:
    return date.today()


def _roadmap(db: Session, startup: Startup) -> Roadmap | None:
    return db.query(Roadmap).filter_by(startup_id=startup.id).first()


def _settings(db: Session, startup: Startup) -> MissionSettings | None:
    return db.query(MissionSettings).filter_by(startup_id=startup.id).first()


def _candidate_tasks(db: Session, roadmap: Roadmap) -> list[RoadmapTask]:
    """Incomplete roadmap tasks for `roadmap`, in generation priority order."""
    return (
        db.query(RoadmapTask)
        .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(
            RoadmapPhase.roadmap_id == roadmap.id,
            RoadmapTask.status != RoadmapStatus.done,
        )
        .order_by(
            RoadmapMilestone.due_on.asc().nullslast(),
            RoadmapPhase.order,
            RoadmapMilestone.order,
            RoadmapTask.order,
        )
        .all()
    )


def _most_recent_prior_mission(db: Session, startup: Startup, today: date) -> Mission | None:
    return (
        db.query(Mission)
        .filter(Mission.startup_id == startup.id, Mission.mission_date < today)
        .order_by(Mission.mission_date.desc())
        .first()
    )


def _snoozed_tasks(db: Session, mission: Mission) -> list[MissionTask]:
    return (
        db.query(MissionTask)
        .filter_by(mission_id=mission.id, status=MissionTaskStatus.snoozed)
        .order_by(MissionTask.order)
        .all()
    )


def get_or_generate_today(db: Session, startup: Startup) -> Mission | None:
    today = _today()

    existing = db.query(Mission).filter_by(startup_id=startup.id, mission_date=today).first()
    if existing is not None:
        return existing

    roadmap = _roadmap(db, startup)
    if roadmap is None:
        return None

    settings = _settings(db, startup)
    mission_size = settings.mission_size if settings is not None else _DEFAULT_MISSION_SIZE
    weekend_missions = settings.weekend_missions if settings is not None else False

    mission = Mission(
        startup_id=startup.id,
        mission_date=today,
        generated_by="system",
        status=MissionStatus.pending,
    )
    db.add(mission)
    db.flush()

    if today.weekday() in _WEEKEND_ISO_WEEKDAYS and not weekend_missions:
        return mission  # empty mission -- "weekends off"

    prior = _most_recent_prior_mission(db, startup, today)
    carried = _snoozed_tasks(db, prior) if prior is not None else []

    order = 0
    carried_roadmap_ids: set[uuid.UUID] = set()
    for snoozed in carried:
        if order >= mission_size:
            break
        db.add(
            MissionTask(
                mission_id=mission.id,
                roadmap_task_id=snoozed.roadmap_task_id,
                title=snoozed.title,
                reason=snoozed.reason,
                effort=snoozed.effort,
                order=order,
            )
        )
        if snoozed.roadmap_task_id is not None:
            carried_roadmap_ids.add(snoozed.roadmap_task_id)
        order += 1

    if order < mission_size:
        milestones_by_id: dict[uuid.UUID, RoadmapMilestone] = {}
        for task in _candidate_tasks(db, roadmap):
            if order >= mission_size:
                break
            if task.id in carried_roadmap_ids:
                continue  # already carried forward this cycle -- don't duplicate
            milestone = milestones_by_id.get(task.milestone_id)
            if milestone is None:
                milestone = db.query(RoadmapMilestone).filter_by(id=task.milestone_id).first()
                if milestone is not None:
                    milestones_by_id[task.milestone_id] = milestone
            reason = f"From your '{milestone.title}' milestone." if milestone is not None else None
            db.add(
                MissionTask(
                    mission_id=mission.id,
                    roadmap_task_id=task.id,
                    title=task.title,
                    reason=reason,
                    effort=task.effort,
                    order=order,
                )
            )
            order += 1

    db.flush()
    return mission


def streak(db: Session, startup: Startup) -> int:
    """Consecutive days ending today (or yesterday, if today isn't complete yet)
    whose mission status is `complete`. Derived on read, not stored."""
    today = _today()
    todays_mission = db.query(Mission).filter_by(startup_id=startup.id, mission_date=today).first()

    cursor = (
        today
        if todays_mission is not None and todays_mission.status == MissionStatus.complete
        else today - timedelta(days=1)
    )

    count = 0
    while True:
        mission = db.query(Mission).filter_by(startup_id=startup.id, mission_date=cursor).first()
        if mission is None or mission.status != MissionStatus.complete:
            break
        count += 1
        cursor -= timedelta(days=1)
    return count


def serialize_mission(db: Session, mission: Mission, streak: int) -> dict:
    tasks = db.query(MissionTask).filter_by(mission_id=mission.id).order_by(MissionTask.order).all()
    return {
        "mission_date": mission.mission_date.isoformat(),
        "status": mission.status.value,
        "streak": streak,
        "tasks": [
            {
                "id": str(t.id),
                "roadmap_task_id": str(t.roadmap_task_id) if t.roadmap_task_id else None,
                "title": t.title,
                "reason": t.reason,
                "effort": t.effort.value,
                "status": t.status.value,
                "order": t.order,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            }
            for t in tasks
        ],
    }
