from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import SALES_ROLES
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.sales import (
    SettingsOut,
    SettingsUpdate,
    StageOut,
    StagesReplace,
)
from app.services.sales import stages as stages_svc

router = APIRouter()
_sales = require_role(*SALES_ROLES)


# ---- Pipeline stages ----
@router.get("/stages", response_model=dict[str, Any])
def list_stages(
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = stages_svc.list_stages(db, membership.startup_id)
    db.commit()  # lazy-seed persists
    return success_response([StageOut.model_validate(r).model_dump(mode="json") for r in rows])


@router.put("/stages", response_model=dict[str, Any])
def replace_stages(
    payload: StagesReplace,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = stages_svc.replace_stages(db, membership.startup_id, payload.stages, actor_id=user.id)
    db.commit()
    return success_response([StageOut.model_validate(r).model_dump(mode="json") for r in rows])


# ---- Settings ----
@router.get("/settings", response_model=dict[str, Any])
def get_settings(
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = stages_svc.get_settings(db, membership.startup_id)
    db.commit()
    return success_response(SettingsOut.model_validate(row).model_dump())


@router.patch("/settings", response_model=dict[str, Any])
def update_settings(
    payload: SettingsUpdate,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = stages_svc.update_settings(db, membership.startup_id, payload, actor_id=user.id)
    db.commit()
    return success_response(SettingsOut.model_validate(row).model_dump())
