from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import LEGAL_ROLES, MembershipRole
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.legal import FormationStepPatch
from app.services.legal import formation as formation_svc
from app.services.legal.catalog import JURISDICTIONS

router = APIRouter()

# Two dependencies, not one (spec D1). A legal advisor reads all three Slice 1 surfaces and
# writes none of them; commenting is Slice 2. LEGAL_ROLES is shared with the notification
# AUDIENCE map, so a reminder naming a filing can never reach a role this would 403.
_legal_reader = require_role(*LEGAL_ROLES)
_legal_writer = require_role(MembershipRole.founder, MembershipRole.team_member)


# --- formation ---------------------------------------------------------------------------------


@router.get("/formation/jurisdictions", response_model=dict[str, Any])
def list_jurisdictions(
    membership: Membership = Depends(_legal_reader),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    """The jurisdictions Slice 1 supports, as codes plus a display name.

    Codes, not the prototype's display strings: the FE sends `NG`, and renders `name`.
    """
    return success_response(
        {"jurisdictions": [{"code": code, "name": name} for code, name in JURISDICTIONS.items()]}
    )


@router.get("/formation/checklist", response_model=dict[str, Any])
def get_checklist(
    jurisdiction: str,
    membership: Membership = Depends(_legal_reader),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        formation_svc.checklist(db, startup_id=membership.startup_id, jurisdiction=jurisdiction)
    )


@router.patch("/formation/steps/{step_key}", response_model=dict[str, Any])
def set_formation_step(
    step_key: str,
    payload: FormationStepPatch,
    membership: Membership = Depends(_legal_writer),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Tick or untick one step, returning the whole checklist so the FE needs no second call."""
    formation_svc.set_step(
        db,
        startup_id=membership.startup_id,
        jurisdiction=payload.jurisdiction,
        step_key=step_key,
        completed=payload.completed,
        actor_id=user.id,
    )
    db.commit()
    return success_response(
        formation_svc.checklist(
            db, startup_id=membership.startup_id, jurisdiction=payload.jurisdiction
        )
    )
