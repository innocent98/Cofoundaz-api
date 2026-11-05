"""Investor Readiness (PRD Module 15). Slice A: score, recompute, history, market-size inputs and
the share-safe snapshot Funding reads. Founder and team member read; only the founder writes."""

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.enums import READINESS_READ_ROLES, READINESS_WRITE_ROLES
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.readiness import ReadinessInputsIn
from app.services.readiness import service

router = APIRouter()
_read = require_role(*READINESS_READ_ROLES)
_write = require_role(*READINESS_WRITE_ROLES)


def _startup(db: Session, membership: Membership) -> Startup:
    s = db.get(Startup, membership.startup_id)
    if s is None:
        raise NotFound()
    return s


@router.get("/score")
def get_score(
    membership: Membership = Depends(_read),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(service.get_score(db, _startup(db, membership)))


@router.post("/score/recompute")
def recompute(
    membership: Membership = Depends(_write),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(service.force_recompute(db, _startup(db, membership)))


@router.get("/history")
def get_history(
    range: int = Query(90, description="Days: 30, 90 or 365"),  # noqa: A002, B008
    membership: Membership = Depends(_read),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(service.history(db, _startup(db, membership), range))


@router.get("/snapshot")
def get_snapshot(
    membership: Membership = Depends(_read),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(service.snapshot(db, _startup(db, membership)))


@router.get("/inputs")
def get_inputs(
    membership: Membership = Depends(_read),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(service.get_inputs(db, membership.startup_id))


@router.put("/inputs")
def put_inputs(
    body: ReadinessInputsIn,
    membership: Membership = Depends(_write),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    if data.get("source_url") is not None:
        data["source_url"] = str(data["source_url"])
    data = {k: v for k, v in data.items() if not (k == "currency" and v is None)}
    return success_response(service.put_inputs(db, _startup(db, membership), user.id, data))
