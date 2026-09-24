from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.enums import ChannelKey, MarketingGenerationStatus
from app.db.models.job import Job
from app.db.models.marketing import AudienceSegment, MarketingAiGeneration
from app.db.models.startup import Startup
from app.platform.llm_budget import metered_complete_json
from app.services.marketing.ai_prompts import (
    build_copy_messages,
    build_plan_week_messages,
    copy_schema,
    plan_week_schema,
)
from app.worker.runner import register_handler


def _load(db: Session, job: Job) -> MarketingAiGeneration | None:
    g = db.get(MarketingAiGeneration, job.payload["generation_id"])
    if g is None or g.status != MarketingGenerationStatus.generating:
        return None
    return g


def handle_marketing_copy(db: Session, job: Job) -> None:
    """Fill a copy generation with 3 variants via the LLM. No commit."""
    g = _load(db, job)
    if g is None:
        return
    inp = g.inputs
    segment_name = None
    seg_id = inp.get("audience_segment_id")
    if seg_id:
        seg = db.get(AudienceSegment, seg_id)
        segment_name = seg.name if seg is not None else None
    result = metered_complete_json(
        db,
        g.startup_id,
        build_copy_messages(
            asset_type=inp.get("asset_type", ""),
            channel=inp.get("channel"),
            tone=inp.get("tone", ""),
            key_message=inp.get("key_message", ""),
            cta=inp.get("cta"),
            segment_name=segment_name,
        ),
        schema=copy_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        g.status = MarketingGenerationStatus.failed
        g.error = "over_budget"
        db.flush()
        return
    variants = [str(v) for v in (result.get("variants") or [])][:3]
    g.output = {"variants": variants}
    g.status = MarketingGenerationStatus.ready
    db.flush()


register_handler("ai.marketing.copy", handle_marketing_copy)


def handle_marketing_plan_week(db: Session, job: Job) -> None:
    """Fill a plan-week generation with up to 7 proposed calendar entries. No commit."""
    g = _load(db, job)
    if g is None:
        return
    startup = db.get(Startup, g.startup_id)
    if startup is None:
        return
    result = metered_complete_json(
        db,
        g.startup_id,
        build_plan_week_messages(
            stage=(startup.stage.value if startup.stage else None),
            industry=startup.industry,
            name=startup.name,
        ),
        schema=plan_week_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        g.status = MarketingGenerationStatus.failed
        g.error = "over_budget"
        db.flush()
        return
    valid = {c.value for c in ChannelKey}
    entries = [
        e
        for e in (result.get("entries") or [])
        if isinstance(e, dict) and e.get("channel") in valid
    ][:7]
    g.output = {"entries": entries}
    g.status = MarketingGenerationStatus.ready
    db.flush()


register_handler("ai.marketing.plan_week", handle_marketing_plan_week)
