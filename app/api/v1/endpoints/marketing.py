import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import ChannelKey, ContentStatus, MembershipRole
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.marketing import (
    CalendarEntryCreate,
    CalendarEntryUpdate,
    ChannelResponse,
    ChannelUpdate,
    OverviewResponse,
)
from app.services.marketing import service as svc

router = APIRouter()
_marketing = require_role(MembershipRole.founder, MembershipRole.team_member)


@router.get("", response_model=dict[str, Any])
def marketing_overview(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        OverviewResponse(**svc.overview(db, startup_id=membership.startup_id)).model_dump()
    )


@router.post("/calendar-entries", response_model=dict[str, Any])
def create_entry(
    payload: CalendarEntryCreate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    entry = svc.create_entry(db, startup_id=membership.startup_id, created_by=user.id, data=payload)
    db.commit()
    return success_response(svc.serialize_entry(entry).model_dump())


@router.get("/calendar-entries", response_model=dict[str, Any])
def list_entries(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    date_from: datetime | None = Query(default=None, alias="from"),  # noqa: B008
    date_to: datetime | None = Query(default=None, alias="to"),  # noqa: B008
    channel: ChannelKey | None = Query(default=None),  # noqa: B008
    status: ContentStatus | None = Query(default=None),  # noqa: B008
) -> dict[str, Any]:
    rows = svc.list_entries(
        db,
        startup_id=membership.startup_id,
        date_from=date_from,
        date_to=date_to,
        channel=channel,
        status=status,
    )
    return success_response({"entries": [svc.serialize_entry(e).model_dump() for e in rows]})


@router.get("/calendar-entries/{entry_id}", response_model=dict[str, Any])
def get_entry(
    entry_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        svc.serialize_entry(
            svc.get_entry(db, startup_id=membership.startup_id, entry_id=entry_id)
        ).model_dump()
    )


@router.patch("/calendar-entries/{entry_id}", response_model=dict[str, Any])
def update_entry(
    entry_id: uuid.UUID,
    payload: CalendarEntryUpdate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    entry = svc.update_entry(
        db, startup_id=membership.startup_id, entry_id=entry_id, actor_id=user.id, data=payload
    )
    db.commit()
    return success_response(svc.serialize_entry(entry).model_dump())


@router.delete("/calendar-entries/{entry_id}", response_model=dict[str, Any])
def delete_entry(
    entry_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    svc.delete_entry(db, startup_id=membership.startup_id, entry_id=entry_id)
    db.commit()
    return success_response({"deleted": True})


@router.get("/channels", response_model=dict[str, Any])
def list_channels(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = svc.list_channels(db, startup_id=membership.startup_id)
    db.commit()  # lazy-seed persists
    return success_response(
        [ChannelResponse.model_validate(r, from_attributes=True).model_dump() for r in rows]
    )


@router.patch("/channels/{key}", response_model=dict[str, Any])
def update_channel(
    key: ChannelKey,
    payload: ChannelUpdate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.update_channel(db, startup_id=membership.startup_id, key=key, data=payload)
    db.commit()
    return success_response(ChannelResponse.model_validate(row, from_attributes=True).model_dump())
