import uuid

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import MarketingGenerationKind, MarketingGenerationStatus
from app.db.models.marketing import AudienceSegment, Campaign, MarketingAiGeneration
from app.db.models.startup import Startup
from app.platform.jobs import job_dispatcher
from app.schemas.marketing import (
    ChannelPlanRequest,
    CopyGenerateRequest,
    GenerationResponse,
    RefineRequest,
)
from app.services.marketing.ai_context import build_marketing_context, context_hash
from app.services.marketing.service import _validation


def serialize_generation(
    g: MarketingAiGeneration, *, stale: bool | None = None
) -> GenerationResponse:
    resp = GenerationResponse.model_validate(g, from_attributes=True)
    resp.inputs = {k: v for k, v in resp.inputs.items() if k != "context_hash"}
    resp.stale = stale
    return resp


def content_gap_stale(db: Session, g: MarketingAiGeneration) -> bool | None:
    """True when the brand context changed since this run was written (or the run predates the
    stored fingerprint), so the FE can offer a regenerate. Never regenerates by itself."""
    startup = db.get(Startup, g.startup_id)
    if g.status != MarketingGenerationStatus.ready or startup is None:
        return None
    return g.inputs.get("context_hash") != context_hash(build_marketing_context(db, startup))


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
    if data.campaign_id is not None and (
        db.query(Campaign.id).filter_by(id=data.campaign_id, startup_id=startup_id).first() is None
    ):
        raise _validation("campaign_id", "Unknown campaign for this workspace.")
    g = MarketingAiGeneration(
        startup_id=startup_id,
        created_by=created_by,
        campaign_id=data.campaign_id,
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


def refine_copy_generation(
    db: Session,
    *,
    startup_id: uuid.UUID,
    created_by: uuid.UUID,
    generation_id: uuid.UUID,
    data: RefineRequest,
) -> MarketingAiGeneration:
    """Refine one variant of a finished copy generation: a NEW generation (3 refined variants)
    that keeps the parent's inputs, including the original key message, and links to it in
    `inputs.refine`. The parent is never modified."""
    parent = get_generation(
        db, startup_id=startup_id, generation_id=generation_id, kind=MarketingGenerationKind.copy
    )
    variants = (parent.output or {}).get("variants", [])
    if parent.status != MarketingGenerationStatus.ready:
        raise _validation("generation_id", "Only a finished generation can be refined.")
    if data.variant_index >= len(variants):
        raise _validation("variant_index", "That variant does not exist.")
    g = MarketingAiGeneration(
        startup_id=startup_id,
        created_by=created_by,
        campaign_id=parent.campaign_id,
        kind=MarketingGenerationKind.copy,
        inputs={
            **parent.inputs,
            "refine": {
                "parent_generation_id": str(parent.id),
                "variant_index": data.variant_index,
                "instruction": data.instruction,
            },
        },
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


def create_channel_plan_generation(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID, data: ChannelPlanRequest
) -> MarketingAiGeneration:
    g = MarketingAiGeneration(
        startup_id=startup_id,
        created_by=created_by,
        kind=MarketingGenerationKind.channel_plan,
        inputs=data.model_dump(mode="json"),
        status=MarketingGenerationStatus.generating,
    )
    db.add(g)
    db.flush()
    job_dispatcher.enqueue(
        db,
        "ai.marketing.channel_plan",
        {"generation_id": str(g.id), "startup_id": str(startup_id)},
        startup_id,
    )
    return g


def create_channel_fit_generation(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID
) -> MarketingAiGeneration:
    g = MarketingAiGeneration(
        startup_id=startup_id,
        created_by=created_by,
        kind=MarketingGenerationKind.channel_fit,
        inputs={},
        status=MarketingGenerationStatus.generating,
    )
    db.add(g)
    db.flush()
    job_dispatcher.enqueue(
        db,
        "ai.marketing.channel_fit",
        {"generation_id": str(g.id), "startup_id": str(startup_id)},
        startup_id,
    )
    return g


def create_content_gap_generation(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID
) -> MarketingAiGeneration:
    g = MarketingAiGeneration(
        startup_id=startup_id,
        created_by=created_by,
        kind=MarketingGenerationKind.content_gap,
        inputs={},
        status=MarketingGenerationStatus.generating,
    )
    db.add(g)
    db.flush()
    job_dispatcher.enqueue(
        db,
        "ai.marketing.content_gap",
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


def list_copy_generations(
    db: Session, *, startup_id: uuid.UUID, campaign_id: uuid.UUID | None = None
) -> list[MarketingAiGeneration]:
    q = db.query(MarketingAiGeneration).filter_by(
        startup_id=startup_id, kind=MarketingGenerationKind.copy
    )
    if campaign_id is not None:
        q = q.filter(MarketingAiGeneration.campaign_id == campaign_id)
    return q.order_by(MarketingAiGeneration.created_at.desc()).all()


def list_content_gap_generations(
    db: Session, *, startup_id: uuid.UUID
) -> list[MarketingAiGeneration]:
    return (
        db.query(MarketingAiGeneration)
        .filter_by(startup_id=startup_id, kind=MarketingGenerationKind.content_gap)
        .order_by(MarketingAiGeneration.created_at.desc())
        .all()
    )
