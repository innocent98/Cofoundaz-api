import uuid

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.business import BusinessRecord
from app.db.models.enums import RecordKind
from app.db.models.marketing import AudienceSegment, Campaign, CampaignSegment
from app.schemas.marketing import SegmentCreate, SegmentResponse, SegmentUpdate
from app.services.marketing.service import _validation


def serialize_segment(seg: AudienceSegment) -> SegmentResponse:
    return SegmentResponse.model_validate(seg, from_attributes=True)


def _validate_persona(db: Session, startup_id: uuid.UUID, persona_id: uuid.UUID | None) -> None:
    if persona_id is None:
        return
    rec = (
        db.query(BusinessRecord)
        .filter_by(id=persona_id, startup_id=startup_id, kind=RecordKind.persona)
        .one_or_none()
    )
    if rec is None:
        raise _validation("persona_id", "persona_id must reference a persona in this workspace.")


def create_segment(db: Session, *, startup_id: uuid.UUID, data: SegmentCreate) -> AudienceSegment:
    _validate_persona(db, startup_id, data.persona_id)
    seg = AudienceSegment(
        startup_id=startup_id,
        name=data.name,
        definition=data.definition,
        est_size=data.est_size,
        persona_id=data.persona_id,
    )
    db.add(seg)
    db.flush()
    return seg


def get_segment(db: Session, *, startup_id: uuid.UUID, segment_id: uuid.UUID) -> AudienceSegment:
    seg = db.query(AudienceSegment).filter_by(id=segment_id, startup_id=startup_id).one_or_none()
    if seg is None:
        raise NotFound()
    return seg


def list_segments(db: Session, *, startup_id: uuid.UUID) -> list[AudienceSegment]:
    return (
        db.query(AudienceSegment)
        .filter_by(startup_id=startup_id)
        .order_by(AudienceSegment.created_at.desc())
        .all()
    )


def update_segment(
    db: Session, *, startup_id: uuid.UUID, segment_id: uuid.UUID, data: SegmentUpdate
) -> AudienceSegment:
    seg = get_segment(db, startup_id=startup_id, segment_id=segment_id)
    fields = data.model_dump(exclude_unset=True)
    if "persona_id" in fields:
        _validate_persona(db, startup_id, fields["persona_id"])
    for name, value in fields.items():
        setattr(seg, name, value)
    db.flush()
    return seg


def delete_segment(db: Session, *, startup_id: uuid.UUID, segment_id: uuid.UUID) -> None:
    seg = get_segment(db, startup_id=startup_id, segment_id=segment_id)
    db.delete(seg)
    db.flush()


def segment_campaigns(
    db: Session, *, startup_id: uuid.UUID, segment_id: uuid.UUID
) -> list[Campaign]:
    get_segment(db, startup_id=startup_id, segment_id=segment_id)  # tenancy check
    return (
        db.query(Campaign)
        .join(CampaignSegment, CampaignSegment.campaign_id == Campaign.id)
        .filter(CampaignSegment.segment_id == segment_id, Campaign.startup_id == startup_id)
        .order_by(Campaign.created_at.desc())
        .all()
    )
