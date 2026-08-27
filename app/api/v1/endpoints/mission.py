from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
from app.db.models.enums import MembershipRole
from app.db.models.membership import Membership
from app.db.models.mission import MissionSettings
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.schemas.mission import MissionSettingsUpdate
from app.services.mission.service import get_or_generate_today, serialize_mission, streak

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
