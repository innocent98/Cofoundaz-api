import math
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.enums import ChannelKey, MarketingGenerationStatus
from app.db.models.job import Job
from app.db.models.marketing import AudienceSegment, MarketingAiGeneration
from app.db.models.startup import Startup
from app.platform.llm_budget import metered_complete_json
from app.services.marketing.ai_prompts import (
    build_channel_fit_messages,
    build_channel_plan_messages,
    build_copy_messages,
    build_plan_week_messages,
    channel_fit_schema,
    channel_plan_schema,
    copy_schema,
    plan_week_schema,
)
from app.services.marketing.service import list_channels
from app.worker.runner import register_handler


def _load(db: Session, job: Job) -> MarketingAiGeneration | None:
    g = db.get(MarketingAiGeneration, job.payload["generation_id"])
    if g is None or g.status != MarketingGenerationStatus.generating:
        return None
    return g


def _fail_over_budget(db: Session, g: MarketingAiGeneration) -> None:
    g.status = MarketingGenerationStatus.failed
    g.error = "over_budget"
    db.flush()


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
        _fail_over_budget(db, g)
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
        _fail_over_budget(db, g)
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


def normalize_channel_mix(raw: dict) -> dict[str, int]:
    """Keep only valid ChannelKeys, coerce to non-negative ints, and scale to sum 100
    (largest-remainder rounding). Even split across all 8 channels if nothing valid."""
    valid = [c.value for c in ChannelKey]
    cleaned = {}
    for k in valid:
        v = raw.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            continue
        cleaned[k] = float(v)
    total = sum(cleaned.values())
    if total <= 0:
        base, extra = divmod(100, len(valid))
        return {k: base + (1 if i < extra else 0) for i, k in enumerate(valid)}
    scaled = {k: (v / total) * 100 for k, v in cleaned.items()}
    floored = {k: int(v) for k, v in scaled.items()}
    remainder = 100 - sum(floored.values())
    # hand the remaining points to the largest fractional parts
    order = sorted(scaled, key=lambda k: scaled[k] - floored[k], reverse=True)
    for k in order[:remainder]:
        floored[k] += 1
    return floored


def handle_marketing_channel_plan(db: Session, job: Job) -> None:
    """Recommend a channel mix + rationale for an objective/stage. No commit."""
    g = _load(db, job)
    if g is None:
        return
    startup = db.get(Startup, g.startup_id)
    if startup is None:
        return
    inp = g.inputs
    result = metered_complete_json(
        db,
        g.startup_id,
        build_channel_plan_messages(
            objective=inp.get("objective", ""),
            stage=(startup.stage.value if startup.stage else None),
            industry=startup.industry,
            budget=inp.get("budget"),
        ),
        schema=channel_plan_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        _fail_over_budget(db, g)
        return
    g.output = {
        "channel_mix": normalize_channel_mix(result.get("channel_mix") or {}),
        "rationale": str(result.get("rationale") or ""),
    }
    g.status = MarketingGenerationStatus.ready
    db.flush()


register_handler("ai.marketing.channel_plan", handle_marketing_channel_plan)


def handle_marketing_channel_fit(db: Session, job: Job) -> None:
    """Generate one fit note per channel and persist onto the channel rows. No commit."""
    g = _load(db, job)
    if g is None:
        return
    startup = db.get(Startup, g.startup_id)
    if startup is None:
        return
    # Lazy-seeds the 8 channels (idempotent; founder's own rows). Runs before the budget
    # check, so an over-budget run may seed rows but writes no notes.
    channels = list_channels(db, startup_id=g.startup_id)
    statuses = {c.key.value: c.status.value for c in channels}
    result = metered_complete_json(
        db,
        g.startup_id,
        build_channel_fit_messages(
            stage=(startup.stage.value if startup.stage else None),
            industry=startup.industry,
            statuses=statuses,
        ),
        schema=channel_fit_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        _fail_over_budget(db, g)
        return
    valid = {c.value for c in ChannelKey}
    notes = {k: str(v) for k, v in (result.get("notes") or {}).items() if k in valid}
    now = datetime.now(UTC)
    by_key = {c.key.value: c for c in channels}
    for key, note in notes.items():
        row = by_key.get(key)
        if row is not None:
            row.ai_fit_note = note
            row.fit_note_generated_at = now
    g.output = {"notes": notes}
    g.status = MarketingGenerationStatus.ready
    db.flush()


register_handler("ai.marketing.channel_fit", handle_marketing_channel_fit)
