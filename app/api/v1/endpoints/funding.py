"""Funding Hub (PRD Module 14), slice A: the round and the investor pipeline. Founder and team
member only (D-FUND-5), behind the `funding` plan module (dark until PLAN_ENFORCEMENT, D-FUND-L10).
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import FUNDING_ROLES, InvestorStage, InvestorType
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.finance import TransactionResponse
from app.schemas.funding import (
    EventCreate,
    InvestorCreate,
    InvestorUpdate,
    RecordFunding,
    RoundUpdate,
    StageMove,
)
from app.services.billing.gate import assert_feature
from app.services.funding import investors as inv_svc
from app.services.funding import overview as overview_svc
from app.services.funding import rounds as rounds_svc

router = APIRouter()
_role = require_role(*FUNDING_ROLES)


def _funding(
    membership: Membership = Depends(_role),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Membership:
    """Role first (403), then the Scale plan module (402). Dark until PLAN_ENFORCEMENT is on."""
    assert_feature(db, membership.startup_id, "funding")
    return membership


# ---- Round ----
@router.get("/round", response_model=dict[str, Any])
def get_round(
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = rounds_svc.latest_round(db, membership.startup_id)
    return success_response(rounds_svc.serialize(row) if row else None)


@router.put("/round", response_model=dict[str, Any])
def put_round(
    payload: RoundUpdate,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = rounds_svc.upsert_round(db, membership.startup_id, payload)
    db.commit()
    return success_response(rounds_svc.serialize(row))


@router.get("/overview", response_model=dict[str, Any])
def get_overview(
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(overview_svc.overview(db, membership.startup_id))


# ---- Investors ----
@router.get("/investors", response_model=dict[str, Any])
def list_investors(
    stage: InvestorStage | None = None,
    q: str | None = Query(default=None, max_length=100),
    warm: bool | None = None,
    type: InvestorType | None = None,  # noqa: A002
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        inv_svc.list_investors(
            db,
            membership.startup_id,
            stage=stage,
            q=q,
            warm=warm,
            type_=type.value if type else None,
            limit=limit,
            offset=offset,
        )
    )


@router.post("/investors", status_code=201, response_model=dict[str, Any])
def create_investor(
    payload: InvestorCreate,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = inv_svc.create_investor(db, membership.startup_id, user.id, payload)
    db.commit()
    return success_response(inv_svc.serialize(row))


@router.get("/investors/{investor_id}", response_model=dict[str, Any])
def get_investor(
    investor_id: uuid.UUID,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        inv_svc.serialize(inv_svc.get_investor(db, membership.startup_id, investor_id))
    )


@router.patch("/investors/{investor_id}", response_model=dict[str, Any])
def update_investor(
    investor_id: uuid.UUID,
    payload: InvestorUpdate,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = inv_svc.update_investor(db, membership.startup_id, user.id, investor_id, payload)
    db.commit()
    return success_response(inv_svc.serialize(row))


@router.delete("/investors/{investor_id}", response_model=dict[str, Any])
def delete_investor(
    investor_id: uuid.UUID,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    inv_svc.delete_investor(db, membership.startup_id, user.id, investor_id)
    db.commit()
    return success_response({"deleted": True})


@router.post("/investors/{investor_id}/stage", response_model=dict[str, Any])
def move_stage(
    investor_id: uuid.UUID,
    payload: StageMove,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row, offer = inv_svc.move_stage(db, membership.startup_id, user.id, investor_id, payload)
    db.commit()
    return success_response({"investor": inv_svc.serialize(row), "record_in_finance": offer})


@router.get("/investors/{investor_id}/events", response_model=dict[str, Any])
def list_events(
    investor_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        inv_svc.list_events(db, membership.startup_id, investor_id, limit=limit, offset=offset)
    )


@router.post("/investors/{investor_id}/events", status_code=201, response_model=dict[str, Any])
def add_event(
    investor_id: uuid.UUID,
    payload: EventCreate,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    ev = inv_svc.add_event(db, membership.startup_id, user.id, investor_id, payload)
    db.commit()
    return success_response(inv_svc.serialize_event(ev))


@router.post(
    "/investors/{investor_id}/record-funding", status_code=201, response_model=dict[str, Any]
)
def record_funding(
    investor_id: uuid.UUID,
    payload: RecordFunding,
    membership: Membership = Depends(_funding),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row, tx = inv_svc.record_funding(db, membership.startup_id, investor_id, payload)
    db.commit()
    return success_response(
        {
            "investor": inv_svc.serialize(row),
            "transaction": TransactionResponse.model_validate(tx, from_attributes=True).model_dump(
                mode="json"
            ),
        }
    )
