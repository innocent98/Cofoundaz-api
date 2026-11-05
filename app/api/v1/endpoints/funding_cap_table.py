"""Funding Hub slice C: the cap table. FOUNDER ONLY (D-FUND-5): team members and every other role
get 403 on every route, behind the `funding` plan module. Nothing here is reachable through a data
room share."""

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import CAP_TABLE_ROLES
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.cap_table import (
    EntryCreate,
    EntryUpdate,
    HolderCreate,
    HolderUpdate,
    ModelRequest,
    ScenarioCreate,
    ScenarioUpdate,
)
from app.services.billing.gate import assert_feature
from app.services.funding import cap_table as svc

router = APIRouter()
_role = require_role(*CAP_TABLE_ROLES)


def _cap_table(
    membership: Membership = Depends(_role),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Membership:
    """Role first (403), then the Scale plan module (402)."""
    assert_feature(db, membership.startup_id, "funding")
    return membership


@router.get("/cap-table", response_model=dict[str, Any])
def get_cap_table(
    fully_diluted: bool = True,
    as_of: dt.date | None = None,
    membership: Membership = Depends(_cap_table),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        svc.ledger(db, membership.startup_id, fully_diluted=fully_diluted, as_of=as_of)
    )


@router.post("/cap-table/holders", status_code=201, response_model=dict[str, Any])
def create_holder(
    payload: HolderCreate,
    membership: Membership = Depends(_cap_table),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.create_holder(db, membership.startup_id, user.id, payload)
    db.commit()
    return success_response(svc.holder_dict(row))


@router.patch("/cap-table/holders/{holder_id}", response_model=dict[str, Any])
def update_holder(
    holder_id: uuid.UUID,
    payload: HolderUpdate,
    membership: Membership = Depends(_cap_table),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.update_holder(db, membership.startup_id, user.id, holder_id, payload)
    db.commit()
    return success_response(svc.holder_dict(row))


@router.delete("/cap-table/holders/{holder_id}", response_model=dict[str, Any])
def delete_holder(
    holder_id: uuid.UUID,
    membership: Membership = Depends(_cap_table),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    svc.delete_holder(db, membership.startup_id, user.id, holder_id)
    db.commit()
    return success_response({"deleted": True})


@router.post("/cap-table/entries", status_code=201, response_model=dict[str, Any])
def create_entry(
    payload: EntryCreate,
    membership: Membership = Depends(_cap_table),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.create_entry(db, membership.startup_id, user.id, payload)
    db.commit()
    return success_response(svc.entry_dict(row))


@router.patch("/cap-table/entries/{entry_id}", response_model=dict[str, Any])
def update_entry(
    entry_id: uuid.UUID,
    payload: EntryUpdate,
    membership: Membership = Depends(_cap_table),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.update_entry(db, membership.startup_id, user.id, entry_id, payload)
    db.commit()
    return success_response(svc.entry_dict(row))


@router.delete("/cap-table/entries/{entry_id}", response_model=dict[str, Any])
def delete_entry(
    entry_id: uuid.UUID,
    membership: Membership = Depends(_cap_table),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    svc.delete_entry(db, membership.startup_id, user.id, entry_id)
    db.commit()
    return success_response({"deleted": True})


@router.post("/cap-table/model", response_model=dict[str, Any])
def model_round(
    payload: ModelRequest,
    membership: Membership = Depends(_cap_table),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Stateless: nothing is saved. 422 with an engine code when the inputs cannot work."""
    return success_response(svc.run_model(db, membership.startup_id, payload))


@router.get("/cap-table/scenarios", response_model=dict[str, Any])
def list_scenarios(
    membership: Membership = Depends(_cap_table),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = svc.list_scenarios(db, membership.startup_id)
    return success_response({"items": [svc.scenario_dict(r, full=False) for r in rows]})


@router.post("/cap-table/scenarios", status_code=201, response_model=dict[str, Any])
def create_scenario(
    payload: ScenarioCreate,
    membership: Membership = Depends(_cap_table),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.create_scenario(db, membership.startup_id, user.id, payload)
    db.commit()
    return success_response(svc.scenario_dict(row, full=True))


@router.get("/cap-table/scenarios/{scenario_id}", response_model=dict[str, Any])
def get_scenario(
    scenario_id: uuid.UUID,
    membership: Membership = Depends(_cap_table),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        svc.scenario_dict(svc.get_scenario(db, membership.startup_id, scenario_id), full=True)
    )


@router.patch("/cap-table/scenarios/{scenario_id}", response_model=dict[str, Any])
def rename_scenario(
    scenario_id: uuid.UUID,
    payload: ScenarioUpdate,
    membership: Membership = Depends(_cap_table),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.rename_scenario(db, membership.startup_id, user.id, scenario_id, payload.name)
    db.commit()
    return success_response(svc.scenario_dict(row, full=False))


@router.delete("/cap-table/scenarios/{scenario_id}", response_model=dict[str, Any])
def delete_scenario(
    scenario_id: uuid.UUID,
    membership: Membership = Depends(_cap_table),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    svc.delete_scenario(db, membership.startup_id, user.id, scenario_id)
    db.commit()
    return success_response({"deleted": True})


@router.get("/cap-table/audit", response_model=dict[str, Any])
def audit(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    membership: Membership = Depends(_cap_table),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(svc.audit_log(db, membership.startup_id, limit=limit, offset=offset))
