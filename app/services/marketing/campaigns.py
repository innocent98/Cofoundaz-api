import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import CampaignStatus
from app.db.models.marketing import AudienceSegment, Campaign, CampaignSegment
from app.platform.events import event_bus
from app.schemas.marketing import CampaignCreate, CampaignResponse, CampaignUpdate
from app.services.marketing.service import _validation

# legal (from, to) status transitions -> the event to emit (or None)
_TRANSITIONS: dict[tuple[CampaignStatus, CampaignStatus], str | None] = {
    (CampaignStatus.draft, CampaignStatus.active): "marketing.campaign.launched",
    (CampaignStatus.active, CampaignStatus.completed): "marketing.campaign.completed",
    (CampaignStatus.active, CampaignStatus.paused): None,
    (CampaignStatus.paused, CampaignStatus.active): None,
}


def campaign_segment_ids(db: Session, campaign_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (
        db.query(CampaignSegment.segment_id)
        .filter(CampaignSegment.campaign_id == campaign_id)
        .order_by(CampaignSegment.created_at)
        .all()
    )
    return [r[0] for r in rows]


def serialize_campaign(c: Campaign, segment_ids: list[uuid.UUID]) -> CampaignResponse:
    return CampaignResponse(
        id=c.id,
        startup_id=c.startup_id,
        name=c.name,
        objective=c.objective,
        budget=c.budget,
        channel_mix=c.channel_mix,
        status=c.status,
        metrics=c.metrics,
        segment_ids=segment_ids,
        period_start=c.period_start,
        period_end=c.period_end,
        launched_at=c.launched_at,
        completed_at=c.completed_at,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _validate_segment_ids(db: Session, startup_id: uuid.UUID, segment_ids: list[uuid.UUID]) -> None:
    if not segment_ids:
        return
    found = {
        r[0]
        for r in db.query(AudienceSegment.id)
        .filter(AudienceSegment.startup_id == startup_id, AudienceSegment.id.in_(segment_ids))
        .all()
    }
    missing = [str(sid) for sid in segment_ids if sid not in found]
    if missing:
        raise _validation("segment_ids", f"Unknown segment(s) for this workspace: {missing}")


def _set_segments(db: Session, campaign: Campaign, segment_ids: list[uuid.UUID]) -> None:
    db.query(CampaignSegment).filter(CampaignSegment.campaign_id == campaign.id).delete()
    for sid in dict.fromkeys(segment_ids):  # dedupe, preserve order
        db.add(CampaignSegment(campaign_id=campaign.id, segment_id=sid))
    db.flush()


def create_campaign(db: Session, *, startup_id: uuid.UUID, data: CampaignCreate) -> Campaign:
    _validate_segment_ids(db, startup_id, data.segment_ids)
    campaign = Campaign(
        startup_id=startup_id,
        name=data.name,
        objective=data.objective,
        budget=data.budget,
        channel_mix=data.channel_mix,
        status=CampaignStatus.draft,
        period_start=data.period_start,
        period_end=data.period_end,
    )
    db.add(campaign)
    db.flush()
    if data.segment_ids:
        _set_segments(db, campaign, data.segment_ids)
    return campaign


def get_campaign(db: Session, *, startup_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign:
    c = db.query(Campaign).filter_by(id=campaign_id, startup_id=startup_id).one_or_none()
    if c is None:
        raise NotFound()
    return c


def list_campaigns(db: Session, *, startup_id: uuid.UUID) -> list[Campaign]:
    return (
        db.query(Campaign)
        .filter_by(startup_id=startup_id)
        .order_by(Campaign.created_at.desc())
        .all()
    )


def update_campaign(
    db: Session,
    *,
    startup_id: uuid.UUID,
    campaign_id: uuid.UUID,
    actor_id: uuid.UUID,
    data: CampaignUpdate,
) -> Campaign:
    campaign = get_campaign(db, startup_id=startup_id, campaign_id=campaign_id)
    fields = data.model_dump(exclude_unset=True)
    segment_ids = fields.pop("segment_ids", None)
    new_status = fields.pop("status", None)
    if segment_ids is not None:
        _validate_segment_ids(db, startup_id, segment_ids)
    event: str | None = None
    status_changing = new_status is not None and new_status != campaign.status
    if status_changing:
        transition = (campaign.status, new_status)
        if transition not in _TRANSITIONS:
            # Raise before any field is set on `campaign` -- an illegal transition must not
            # dirty the ORM object.
            raise _validation(
                "status", f"Illegal transition {campaign.status.value} -> {new_status.value}."
            )
        event = _TRANSITIONS[transition]
    for name, value in fields.items():
        setattr(campaign, name, value)
    if status_changing:
        campaign.status = new_status
        if new_status == CampaignStatus.active and campaign.launched_at is None:
            campaign.launched_at = datetime.now(UTC)
        if new_status == CampaignStatus.completed:
            campaign.completed_at = datetime.now(UTC)
    if segment_ids is not None:
        _set_segments(db, campaign, segment_ids)
    db.flush()
    if event:
        event_bus.publish(
            db,
            event,
            {
                "startup_id": str(campaign.startup_id),
                "campaign_id": str(campaign.id),
                "name": campaign.name,
                "actor_id": str(actor_id),
            },
        )
    return campaign


def delete_campaign(db: Session, *, startup_id: uuid.UUID, campaign_id: uuid.UUID) -> None:
    campaign = get_campaign(db, startup_id=startup_id, campaign_id=campaign_id)
    db.delete(campaign)
    db.flush()
