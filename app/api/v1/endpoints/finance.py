import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import MembershipRole, TransactionDirection
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.finance import TransactionCreate, TransactionUpdate
from app.services.finance import service as finance_svc

router = APIRouter()

_finance = require_role(
    MembershipRole.founder, MembershipRole.team_member, MembershipRole.accountant
)


@router.post("/transactions", response_model=dict[str, Any])
def create_transaction(
    payload: TransactionCreate,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = finance_svc.create_transaction(db, startup_id=membership.startup_id, data=payload)
    db.commit()
    return success_response(finance_svc.serialize_transaction(row).model_dump())


@router.get("/transactions", response_model=dict[str, Any])
def list_transactions(
    category: str | None = None,
    uncategorized: bool = False,
    direction: TransactionDirection | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = finance_svc.list_transactions(
        db,
        startup_id=membership.startup_id,
        category=category,
        uncategorized=uncategorized,
        direction=direction,
        date_from=date_from,
        date_to=date_to,
    )
    return success_response(
        {"transactions": [finance_svc.serialize_transaction(r).model_dump() for r in rows]}
    )


@router.patch("/transactions/{transaction_id}", response_model=dict[str, Any])
def update_transaction(
    transaction_id: uuid.UUID,
    payload: TransactionUpdate,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = finance_svc.update_transaction(
        db, startup_id=membership.startup_id, transaction_id=transaction_id, data=payload
    )
    db.commit()
    return success_response(finance_svc.serialize_transaction(row).model_dump())


@router.delete("/transactions/{transaction_id}", response_model=dict[str, Any])
def delete_transaction(
    transaction_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    finance_svc.delete_transaction(
        db, startup_id=membership.startup_id, transaction_id=transaction_id
    )
    db.commit()
    return success_response({"deleted": True})
