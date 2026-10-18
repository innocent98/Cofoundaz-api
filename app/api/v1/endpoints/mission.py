import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
from app.db.models.enums import MembershipRole, MissionTaskStatus
from app.db.models.membership import Membership
from app.db.models.mission import Mission, MissionSettings, MissionTask
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.platform.activity import write_activity
from app.schemas.mission import (
    MissionSettingsUpdate,
    MissionTaskAction,
    MissionTaskCreate,
    UpcomingOrder,
)
from app.services.mission.planning import (
    assigned_to,
    completed_log,
    set_upcoming_order,
    upcoming,
)
from app.services.mission.service import (
    VALID_REJECT_REASONS,
    add_custom_task,
    complete_task,
    ensure_today_mission,
    get_or_generate_today,
    milestones_for,
    mission_empty_reason,
    mission_history,
    reject_task,
    reorder_task,
    serialize_mission,
    serialize_task,
    snooze_task,
    streak,
)

router = APIRouter()
_editor = require_role(MembershipRole.founder, MembershipRole.team_member)


def _startup(db: Session, membership: Membership) -> Startup:
    s = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if s is None:
        raise NotFound()
    return s


def _settings(db: Session, membership: Membership) -> MissionSettings:
    s = db.query(MissionSettings).filter_by(startup_id=membership.startup_id).first()
    if s is None:
        s = MissionSettings(startup_id=membership.startup_id)
        db.add(s)
        db.flush()
    return s


def _serialize_settings(s: MissionSettings) -> dict[str, Any]:
    return {
        "mission_size": s.mission_size,
        "delivery_time": s.delivery_time.isoformat(),
        "weekend_missions": s.weekend_missions,
    }


def _actor_name(user: User) -> str:
    return (user.profile.full_name if user.profile else None) or "A teammate"


def _mission_task(db: Session, membership: Membership, task_id: uuid.UUID) -> MissionTask:
    """Fetch `task_id`, scoped to the caller's workspace via its parent mission.
    Cross-workspace or unknown IDs both fall through to the same 404."""
    t = (
        db.query(MissionTask)
        .join(Mission, MissionTask.mission_id == Mission.id)
        .filter(MissionTask.id == task_id, Mission.startup_id == membership.startup_id)
        .first()
    )
    if t is None:
        raise NotFound()
    return t


@router.get("/today")
def get_today(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    m = get_or_generate_today(db, startup)
    db.commit()
    mine = assigned_to(db, startup, user.id)
    if m is None:
        return success_response({"status": "no_roadmap", "assigned_to_me": mine})
    return success_response(
        {
            **serialize_mission(db, m, streak(db, startup)),
            # Why the list is empty (weekend_off / all_caught_up), null with tasks (PRD 04.5).
            "empty_reason": mission_empty_reason(db, startup, m),
            "assigned_to_me": mine,
        }
    )


@router.get("/history")
def get_history(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    # Read-only: reports existing missions newest-first; does not generate today's.
    startup = _startup(db, membership)
    return success_response(mission_history(db, startup))


@router.get("/upcoming")
def get_upcoming(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 04.2: the next 7 days projected from the roadmap queue (nothing is created)."""
    return success_response(upcoming(db, _startup(db, membership)))


@router.put("/upcoming/order")
def put_upcoming_order(
    payload: UpcomingOrder,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 04.2 "editable order": rank the queue; later missions draw in this order."""
    startup = _startup(db, membership)
    set_upcoming_order(db, startup, payload.roadmap_task_ids)
    db.commit()
    return success_response(upcoming(db, startup))


@router.get("/completed")
def get_completed(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 04.3: completed tasks, newest first."""
    return success_response(completed_log(db, _startup(db, membership), cursor, limit))


@router.get("/settings")
def get_settings(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    s = _settings(db, membership)
    db.commit()
    return success_response(_serialize_settings(s))


@router.patch("/settings")
def patch_settings(
    payload: MissionSettingsUpdate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    s = _settings(db, membership)
    if payload.mission_size is not None:
        if not 1 <= payload.mission_size <= 3:
            raise AppError(
                "VALIDATION_ERROR",
                "Mission size must be 1–3.",
                422,
                field_errors=[{"field": "mission_size", "message": "Must be 1, 2, or 3."}],
            )
        s.mission_size = payload.mission_size
    if payload.delivery_time is not None:
        s.delivery_time = payload.delivery_time
    if payload.weekend_missions is not None:
        s.weekend_missions = payload.weekend_missions
    db.commit()
    return success_response(_serialize_settings(s))


@router.post("/tasks")
def create_task(
    payload: MissionTaskCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    # No roadmap yet is fine: a custom task doesn't need one (see ensure_today_mission).
    mission = ensure_today_mission(db, startup)
    task = add_custom_task(db, mission, payload.title, payload.effort)
    write_activity(
        db,
        startup_id=startup.id,
        actor_user_id=user.id,
        action="mission.task.added",
        entity_type="mission_task",
        entity_id=task.id,
        summary=f"{_actor_name(user)} added '{task.title}' to today's mission",
    )
    db.commit()
    return success_response(serialize_task(task))


@router.patch("/tasks/{task_id}")
def patch_task(
    task_id: uuid.UUID,
    payload: MissionTaskAction,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    task = _mission_task(db, membership, task_id)

    match payload.action:
        case "complete":
            was_done = task.status == MissionTaskStatus.done
            complete_task(db, startup, task)
            if not was_done and task.status == MissionTaskStatus.done:
                write_activity(
                    db,
                    startup_id=startup.id,
                    actor_user_id=user.id,
                    action="mission.task.completed",
                    entity_type="mission_task",
                    entity_id=task.id,
                    summary=f"{_actor_name(user)} completed '{task.title}'",
                )
        case "snooze":
            snooze_task(db, task)
            write_activity(
                db,
                startup_id=startup.id,
                actor_user_id=user.id,
                action="mission.task.snoozed",
                entity_type="mission_task",
                entity_id=task.id,
                summary=f"{_actor_name(user)} snoozed '{task.title}'",
            )
        case "reorder":
            if payload.order is None:
                raise AppError(
                    "VALIDATION_ERROR",
                    "order is required for the reorder action.",
                    422,
                    field_errors=[{"field": "order", "message": "Required for reorder."}],
                )
            reorder_task(db, task, payload.order)
        case "reject":
            if payload.reject_reason not in VALID_REJECT_REASONS:
                raise AppError(
                    "VALIDATION_ERROR",
                    "Please choose one of the listed reject reasons.",
                    422,
                    field_errors=[{"field": "reject_reason", "message": "Not a valid reason."}],
                )
            reject_task(db, task, payload.reject_reason)
            write_activity(
                db,
                startup_id=startup.id,
                actor_user_id=user.id,
                action="mission.task.rejected",
                entity_type="mission_task",
                entity_id=task.id,
                summary=f"{_actor_name(user)} rejected '{task.title}'",
            )
        case _:
            raise AppError(
                "VALIDATION_ERROR",
                "Unknown action.",
                422,
                field_errors=[
                    {"field": "action", "message": "Must be complete/snooze/reorder/reject."}
                ],
            )

    db.commit()
    milestone = (
        milestones_for(db, [task.roadmap_task_id]).get(task.roadmap_task_id)
        if task.roadmap_task_id
        else None
    )
    return success_response(serialize_task(task, milestone))
