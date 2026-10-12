import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import CalendarEventType, MembershipRole
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.calendar import EventCreate, EventUpdate
from app.services.calendar import milestones as milestones_svc
from app.services.calendar import service as calendar_svc

router = APIRouter()
# Founders and team members only (spec D4) — accountant has no calendar grant.
_calendar = require_role(MembershipRole.founder, MembershipRole.team_member)


@router.post("/events", response_model=dict[str, Any])
def create_event(
    payload: EventCreate,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = calendar_svc.create_event(
        db, startup_id=membership.startup_id, created_by=user.id, data=payload
    )
    db.commit()
    return success_response(calendar_svc.serialize_event(row).model_dump(mode="json"))


@router.get("/events", response_model=dict[str, Any])
def list_events(
    date_from: date | None = None,
    date_to: date | None = None,
    type: CalendarEventType | None = None,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """One list for three views: Month, Week and Agenda differ only in the range they ask for."""
    rows = calendar_svc.list_events(
        db,
        startup_id=membership.startup_id,
        date_from=date_from,
        date_to=date_to,
        type=type,
    )
    return success_response(
        {"events": [calendar_svc.serialize_event(r).model_dump(mode="json") for r in rows]}
    )


@router.get("/events/{event_id}", response_model=dict[str, Any])
def get_event(
    event_id: uuid.UUID,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = calendar_svc.get_event(db, startup_id=membership.startup_id, event_id=event_id)
    return success_response(calendar_svc.serialize_event(row).model_dump(mode="json"))


@router.patch("/events/{event_id}", response_model=dict[str, Any])
def update_event(
    event_id: uuid.UUID,
    payload: EventUpdate,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = calendar_svc.update_event(
        db, startup_id=membership.startup_id, event_id=event_id, data=payload
    )
    db.commit()
    return success_response(calendar_svc.serialize_event(row).model_dump(mode="json"))


@router.delete("/events/{event_id}", response_model=dict[str, Any])
def delete_event(
    event_id: uuid.UUID,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    calendar_svc.delete_event(db, startup_id=membership.startup_id, event_id=event_id)
    db.commit()
    return success_response({"deleted": True})


@router.get("/milestones", response_model=dict[str, Any])
def list_milestones(
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Read-only projection of this workspace's roadmap milestones (spec D1).

    Owns no table and never writes: editing a milestone stays in Roadmap. The "+ Milestone"
    button on this screen creates a calendar EVENT via POST /calendar/events (D11/D13), so what
    it creates does not appear in this list.
    """
    rows = milestones_svc.list_milestones(db, startup_id=membership.startup_id)
    return success_response({"milestones": [m.model_dump(mode="json") for m in rows]})
