import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
from app.db.models.enums import MembershipRole, MissionStatus
from app.db.models.membership import Membership
from app.db.models.mission import Mission, MissionSettings, MissionTask
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.schemas.mission import MissionSettingsUpdate, MissionTaskAction, MissionTaskCreate
from app.services.mission.service import (
    VALID_REJECT_REASONS,
    add_custom_task,
    complete_task,
    get_or_generate_today,
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
    if m is None:
        return success_response({"status": "no_roadmap"})
    return success_response(serialize_mission(db, m, streak(db, startup)))


@router.get("/history")
def get_history(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    # Read-only: reports existing missions newest-first; does not generate today's.
    startup = _startup(db, membership)
    return success_response(mission_history(db, startup))


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
    mission = get_or_generate_today(db, startup)
    if mission is None:
        # No roadmap yet -- a custom task doesn't need one, so start today's
        # mission ourselves rather than blocking the user on roadmap generation.
        mission = Mission(
            startup_id=startup.id,
            mission_date=date.today(),
            generated_by="user",
            status=MissionStatus.pending,
        )
        db.add(mission)
        db.flush()
    task = add_custom_task(db, mission, payload.title, payload.effort)
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
            complete_task(db, startup, task)
        case "snooze":
            snooze_task(db, task)
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
    return success_response(serialize_task(task))
