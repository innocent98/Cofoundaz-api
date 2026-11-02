import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
from app.db.models.enums import MembershipRole, RecommendationStatus
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.services.health_score import service as hs_service

router = APIRouter()


def _startup(db: Session, membership: Membership) -> Startup:
    s = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if s is None:
        raise NotFound()
    return s


@router.get("")
def get_health_score(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    return success_response(hs_service.get_overview(db, startup))


@router.get("/dimensions/{dim}")
def get_dimension(
    dim: str,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(hs_service.get_dimension(db, _startup(db, membership), dim))


@router.get("/benchmarks")
def get_benchmarks(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(hs_service.get_benchmarks(db, _startup(db, membership)))


@router.get("/recommendations")
def list_recommendations(
    status: str | None = Query(None),  # noqa: B008
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    if status is not None and status not in ("pending", "accepted", "dismissed"):
        raise AppError(
            "VALIDATION_ERROR",
            "Unknown status.",
            422,
            field_errors=[{"field": "status", "message": "Use pending, accepted, or dismissed."}],
        )
    return success_response(hs_service.list_recommendations(db, _startup(db, membership), status))


@router.get("/history")
def get_history(
    range: str = Query("30d"),  # noqa: B008
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    _check_range(range)
    return success_response(hs_service.get_history(db, _startup(db, membership), range))


@router.get("/history/markers")
def get_history_markers(
    range: str = Query("30d"),  # noqa: B008
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 06.3 annotated event markers for the trend chart (same `range` as /history)."""
    _check_range(range)
    return success_response(hs_service.get_history_markers(db, _startup(db, membership), range))


def _check_range(range_key: str) -> None:
    if range_key not in hs_service.HISTORY_RANGES:
        raise AppError(
            "VALIDATION_ERROR",
            "Unknown range.",
            422,
            field_errors=[{"field": "range", "message": "Use 7d, 30d, 90d, 365d, or all."}],
        )


@router.post("/recommendations/{rec_id}/accept")
def accept_recommendation(
    rec_id: uuid.UUID,
    membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        hs_service.resolve_recommendation(
            db, _startup(db, membership), rec_id, RecommendationStatus.accepted
        )
    )


@router.post("/recommendations/{rec_id}/dismiss")
def dismiss_recommendation(
    rec_id: uuid.UUID,
    membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        hs_service.resolve_recommendation(
            db, _startup(db, membership), rec_id, RecommendationStatus.dismissed
        )
    )
