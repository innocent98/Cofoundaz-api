import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import ChannelKey, ContentStatus, MarketingGenerationKind, MembershipRole
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.marketing import (
    CalendarEntryCreate,
    CalendarEntryUpdate,
    CampaignCreate,
    CampaignUpdate,
    ChannelResponse,
    ChannelUpdate,
    CopyGenerateRequest,
    OverviewResponse,
    SegmentCreate,
    SegmentUpdate,
)
from app.services.marketing import ai_content as ai_content_svc
from app.services.marketing import campaigns as campaigns_svc
from app.services.marketing import segments as segments_svc
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


# ---- Audience segments ----
@router.post("/segments", response_model=dict[str, Any])
def create_segment(
    payload: SegmentCreate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    seg = segments_svc.create_segment(db, startup_id=membership.startup_id, data=payload)
    db.commit()
    return success_response(segments_svc.serialize_segment(seg).model_dump())


@router.get("/segments", response_model=dict[str, Any])
def list_segments(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = segments_svc.list_segments(db, startup_id=membership.startup_id)
    return success_response(
        {"segments": [segments_svc.serialize_segment(s).model_dump() for s in rows]}
    )


@router.get("/segments/{segment_id}", response_model=dict[str, Any])
def get_segment(
    segment_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    seg = segments_svc.get_segment(db, startup_id=membership.startup_id, segment_id=segment_id)
    return success_response(segments_svc.serialize_segment(seg).model_dump())


@router.patch("/segments/{segment_id}", response_model=dict[str, Any])
def update_segment(
    segment_id: uuid.UUID,
    payload: SegmentUpdate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    seg = segments_svc.update_segment(
        db, startup_id=membership.startup_id, segment_id=segment_id, data=payload
    )
    db.commit()
    return success_response(segments_svc.serialize_segment(seg).model_dump())


@router.delete("/segments/{segment_id}", response_model=dict[str, Any])
def delete_segment(
    segment_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    segments_svc.delete_segment(db, startup_id=membership.startup_id, segment_id=segment_id)
    db.commit()
    return success_response({"deleted": True})


@router.get("/segments/{segment_id}/campaigns", response_model=dict[str, Any])
def segment_campaigns(
    segment_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = segments_svc.segment_campaigns(
        db, startup_id=membership.startup_id, segment_id=segment_id
    )
    data = [
        campaigns_svc.serialize_campaign(
            c, campaigns_svc.campaign_segment_ids(db, c.id)
        ).model_dump()
        for c in rows
    ]
    return success_response({"campaigns": data})


# ---- Campaigns ----
def _campaign_body(db: Session, c: Any) -> dict[str, Any]:
    return campaigns_svc.serialize_campaign(
        c, campaigns_svc.campaign_segment_ids(db, c.id)
    ).model_dump()


@router.post("/campaigns", response_model=dict[str, Any])
def create_campaign(
    payload: CampaignCreate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    c = campaigns_svc.create_campaign(db, startup_id=membership.startup_id, data=payload)
    db.commit()
    return success_response(_campaign_body(db, c))


@router.get("/campaigns", response_model=dict[str, Any])
def list_campaigns(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = campaigns_svc.list_campaigns(db, startup_id=membership.startup_id)
    return success_response({"campaigns": [_campaign_body(db, c) for c in rows]})


@router.get("/campaigns/{campaign_id}", response_model=dict[str, Any])
def get_campaign(
    campaign_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    c = campaigns_svc.get_campaign(db, startup_id=membership.startup_id, campaign_id=campaign_id)
    return success_response(_campaign_body(db, c))


@router.patch("/campaigns/{campaign_id}", response_model=dict[str, Any])
def update_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignUpdate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    c = campaigns_svc.update_campaign(
        db,
        startup_id=membership.startup_id,
        campaign_id=campaign_id,
        actor_id=user.id,
        data=payload,
    )
    db.commit()
    return success_response(_campaign_body(db, c))


@router.delete("/campaigns/{campaign_id}", response_model=dict[str, Any])
def delete_campaign(
    campaign_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    campaigns_svc.delete_campaign(db, startup_id=membership.startup_id, campaign_id=campaign_id)
    db.commit()
    return success_response({"deleted": True})


# ---- AI generation (copy + plan-week) ----
@router.post("/copy/generate", status_code=status.HTTP_202_ACCEPTED, response_model=dict[str, Any])
def generate_copy(
    payload: CopyGenerateRequest,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    g = ai_content_svc.create_copy_generation(
        db, startup_id=membership.startup_id, created_by=user.id, data=payload
    )
    db.commit()
    return success_response({"id": str(g.id), "status": g.status.value})


@router.get("/copy/generations", response_model=dict[str, Any])
def list_copy_generations(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = ai_content_svc.list_copy_generations(db, startup_id=membership.startup_id)
    return success_response(
        {"generations": [ai_content_svc.serialize_generation(g).model_dump() for g in rows]}
    )


@router.get("/copy/generations/{generation_id}", response_model=dict[str, Any])
def get_copy_generation(
    generation_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    g = ai_content_svc.get_generation(
        db,
        startup_id=membership.startup_id,
        generation_id=generation_id,
        kind=MarketingGenerationKind.copy,
    )
    return success_response(ai_content_svc.serialize_generation(g).model_dump())


@router.post(
    "/calendar/plan-week", status_code=status.HTTP_202_ACCEPTED, response_model=dict[str, Any]
)
def generate_plan_week(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    g = ai_content_svc.create_plan_week_generation(
        db, startup_id=membership.startup_id, created_by=user.id
    )
    db.commit()
    return success_response({"id": str(g.id), "status": g.status.value})


@router.get("/calendar/plan-week/{generation_id}", response_model=dict[str, Any])
def get_plan_week(
    generation_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    g = ai_content_svc.get_generation(
        db,
        startup_id=membership.startup_id,
        generation_id=generation_id,
        kind=MarketingGenerationKind.plan_week,
    )
    return success_response(ai_content_svc.serialize_generation(g).model_dump())
