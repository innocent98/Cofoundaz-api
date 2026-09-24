import uuid

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import MarketingGenerationKind, MarketingGenerationStatus
from app.db.models.marketing import AudienceSegment, MarketingAiGeneration
from app.platform.jobs import job_dispatcher
from app.schemas.marketing import CopyGenerateRequest, GenerationResponse
from app.services.marketing.service import _validation


def serialize_generation(g: MarketingAiGeneration) -> GenerationResponse:
    return GenerationResponse.model_validate(g, from_attributes=True)


def create_copy_generation(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID, data: CopyGenerateRequest
) -> MarketingAiGeneration:
    if data.audience_segment_id is not None:
        seg = (
            db.query(AudienceSegment)
            .filter_by(id=data.audience_segment_id, startup_id=startup_id)
            .one_or_none()
        )
        if seg is None:
            raise _validation("audience_segment_id", "Unknown segment for this workspace.")
    g = MarketingAiGeneration(
        startup_id=startup_id,
        created_by=created_by,
        kind=MarketingGenerationKind.copy,
        inputs=data.model_dump(mode="json"),
        status=MarketingGenerationStatus.generating,
    )
    db.add(g)
    db.flush()
    job_dispatcher.enqueue(
        db,
        "ai.marketing.copy",
        {"generation_id": str(g.id), "startup_id": str(startup_id)},
        startup_id,
    )
    return g


def create_plan_week_generation(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID
) -> MarketingAiGeneration:
    g = MarketingAiGeneration(
        startup_id=startup_id,
        created_by=created_by,
        kind=MarketingGenerationKind.plan_week,
        inputs={},
        status=MarketingGenerationStatus.generating,
    )
    db.add(g)
    db.flush()
    job_dispatcher.enqueue(
        db,
        "ai.marketing.plan_week",
        {"generation_id": str(g.id), "startup_id": str(startup_id)},
        startup_id,
    )
    return g


def get_generation(
    db: Session, *, startup_id: uuid.UUID, generation_id: uuid.UUID, kind: MarketingGenerationKind
) -> MarketingAiGeneration:
    g = (
        db.query(MarketingAiGeneration)
        .filter_by(id=generation_id, startup_id=startup_id, kind=kind)
        .one_or_none()
    )
    if g is None:
        raise NotFound()
    return g


def list_copy_generations(db: Session, *, startup_id: uuid.UUID) -> list[MarketingAiGeneration]:
    return (
        db.query(MarketingAiGeneration)
        .filter_by(startup_id=startup_id, kind=MarketingGenerationKind.copy)
        .order_by(MarketingAiGeneration.created_at.desc())
        .all()
    )
