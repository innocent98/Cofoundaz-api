"""Today's Mission read models beyond today: the Upcoming projection (PRD 04.2), its editable
order, and the Completed log (PRD 04.3).

Upcoming is a pure projection: nothing is created for future days, so it always reflects the
live roadmap and settings. It mirrors `get_or_generate_today`'s selection rules: per-day
`mission_size`, weekends skipped when weekends are off, today's snoozed tasks carried to the next
working day, and `RoadmapTask.mission_rank` first. It assumes today's other tasks get done.
"""

import base64
import binascii
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.enums import MissionTaskStatus
from app.db.models.mission import Mission, MissionTask
from app.db.models.roadmap import RoadmapMilestone, RoadmapPhase, RoadmapTask
from app.db.models.startup import Startup
from app.services.mission.service import (
    _WEEKEND_ISO_WEEKDAYS,
    _candidate_tasks,
    _mission_candidates,
    _roadmap,
    _settings,
    _today,
    milestones_for,
)

UPCOMING_DAYS = 7
_DEFAULT_SIZE = 3


def _task_row(task: RoadmapTask, milestone: dict | None, carried: bool = False) -> dict:
    return {
        "roadmap_task_id": str(task.id),
        "title": task.title,
        "effort": task.effort.value,
        "milestone": milestone,
        "carried_over": carried,
    }


def upcoming(db: Session, startup: Startup) -> dict[str, Any]:
    roadmap = _roadmap(db, startup)
    settings = _settings(db, startup)
    size = settings.mission_size if settings is not None else _DEFAULT_SIZE
    weekends = settings.weekend_missions if settings is not None else False
    today = _today()
    if roadmap is None:
        return {"has_roadmap": False, "days": []}

    todays = db.query(Mission).filter_by(startup_id=startup.id, mission_date=today).first()
    today_tasks = (
        db.query(MissionTask).filter_by(mission_id=todays.id).all() if todays is not None else []
    )
    snoozed_ids = [
        t.roadmap_task_id
        for t in sorted(today_tasks, key=lambda t: t.order)
        if t.status == MissionTaskStatus.snoozed and t.roadmap_task_id
    ]
    used = {t.roadmap_task_id for t in today_tasks if t.roadmap_task_id}

    candidates = _mission_candidates(db, roadmap, today)
    by_id = {t.id: t for t in candidates}
    carried = [by_id[i] for i in snoozed_ids if i in by_id]
    queue = [t for t in candidates if t.id not in used]
    chips = milestones_for(db, [t.id for t in candidates])

    days = []
    carry_pending = True
    for offset in range(1, UPCOMING_DAYS + 1):
        day = today + timedelta(days=offset)
        if day.weekday() in _WEEKEND_ISO_WEEKDAYS and not weekends:
            days.append({"date": day.isoformat(), "rest_day": True, "tasks": []})
            continue
        picked: list[dict] = []
        if carry_pending:
            picked = [_task_row(t, chips.get(t.id), carried=True) for t in carried[:size]]
            carry_pending = False
        while len(picked) < size and queue:
            t = queue.pop(0)
            picked.append(_task_row(t, chips.get(t.id)))
        days.append({"date": day.isoformat(), "rest_day": False, "tasks": picked})
    return {"has_roadmap": True, "mission_size": size, "days": days}


def set_upcoming_order(db: Session, startup: Startup, roadmap_task_ids: list[uuid.UUID]) -> None:
    """Persist the founder's order for the upcoming queue. Every id must be an open task on this
    workspace's roadmap; the list is ranked 0..n-1 and every other task in the roadmap is unranked
    (so the order is exactly what the founder last saw and arranged)."""
    roadmap = _roadmap(db, startup)
    if roadmap is None:
        raise AppError("NO_ROADMAP", "Generate your roadmap first.", 409)
    if len(set(roadmap_task_ids)) != len(roadmap_task_ids):
        raise AppError(
            "VALIDATION_ERROR",
            "Each task can appear only once.",
            422,
            field_errors=[{"field": "roadmap_task_ids", "message": "Duplicate task."}],
        )
    open_ids = {t.id for t in _candidate_tasks(db, roadmap)}
    unknown = [i for i in roadmap_task_ids if i not in open_ids]
    if unknown:
        raise AppError(
            "VALIDATION_ERROR",
            "Some tasks aren't open tasks on your roadmap.",
            422,
            field_errors=[{"field": "roadmap_task_ids", "message": f"Unknown task {unknown[0]}."}],
        )
    rank = {tid: i for i, tid in enumerate(roadmap_task_ids)}
    tasks = (
        db.query(RoadmapTask)
        .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(RoadmapPhase.roadmap_id == roadmap.id)
        .all()
    )
    for t in tasks:
        t.mission_rank = rank.get(t.id)
    db.flush()


def _encode(completed_at: datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{completed_at.isoformat()}|{row_id}".encode()).decode()


def _decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, rid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(rid)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AppError(
            "VALIDATION_ERROR",
            "Invalid pagination cursor.",
            422,
            field_errors=[{"field": "cursor", "message": "This page link is no longer valid."}],
        ) from exc


def completed_log(
    db: Session, startup: Startup, cursor: str | None = None, limit: int = 20
) -> dict[str, Any]:
    """Completed mission tasks, newest first (PRD 04.3), keyset-paginated on (completed_at, id)."""
    limit = max(1, min(limit, 50))
    q = (
        db.query(MissionTask, Mission.mission_date)
        .join(Mission, MissionTask.mission_id == Mission.id)
        .filter(
            Mission.startup_id == startup.id,
            MissionTask.status == MissionTaskStatus.done,
            MissionTask.completed_at.isnot(None),
        )
        .order_by(MissionTask.completed_at.desc(), MissionTask.id.desc())
    )
    if cursor:
        ts, rid = _decode(cursor)
        q = q.filter(
            (MissionTask.completed_at < ts)
            | ((MissionTask.completed_at == ts) & (MissionTask.id < rid))
        )
    rows = q.limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    chips = milestones_for(db, [t.roadmap_task_id for t, _ in rows if t.roadmap_task_id])
    items = [
        {
            "id": str(t.id),
            "title": t.title,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            "mission_date": d.isoformat(),
            "milestone": chips.get(t.roadmap_task_id) if t.roadmap_task_id else None,
        }
        for t, d in rows
    ]
    last = rows[-1][0] if has_more and rows else None
    next_cursor = (
        _encode(last.completed_at, last.id) if last is not None and last.completed_at else None
    )
    return {"items": items, "next_cursor": next_cursor}


ASSIGNED_LIMIT = 10


def assigned_to(db: Session, startup: Startup, user_id: uuid.UUID) -> list[dict]:
    """Open roadmap tasks assigned to this member (PRD 04 access: "own assigned tasks appear").
    Soonest milestone first; the mission itself stays the workspace's shared plan."""
    roadmap = _roadmap(db, startup)
    if roadmap is None:
        return []
    tasks = [t for t in _candidate_tasks(db, roadmap) if t.assignee_id == user_id][:ASSIGNED_LIMIT]
    chips = milestones_for(db, [t.id for t in tasks])
    return [_task_row(t, chips.get(t.id)) for t in tasks]
