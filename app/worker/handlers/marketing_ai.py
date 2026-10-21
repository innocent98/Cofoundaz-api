import math
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.enums import CampaignStatus, ChannelKey, FitLevel, MarketingGenerationStatus
from app.db.models.job import Job
from app.db.models.marketing import (
    AudienceSegment,
    Campaign,
    CampaignReadout,
    MarketingAiGeneration,
    SeoKeyword,
)
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.platform.llm_budget import metered_complete_json
from app.services.marketing.ai_context import build_marketing_context, fence, has_placeholder
from app.services.marketing.ai_prompts import (
    build_channel_fit_messages,
    build_channel_plan_messages,
    build_content_gap_messages,
    build_copy_messages,
    build_plan_week_messages,
    build_refine_messages,
    channel_fit_schema,
    channel_plan_schema,
    content_gap_schema,
    copy_schema,
    plan_week_schema,
)
from app.services.marketing.platforms import limit_for
from app.services.marketing.readout_figures import build_figures
from app.services.marketing.readouts import generate_verified_output
from app.services.marketing.service import list_channels
from app.worker.runner import register_handler
from app.worker.scheduler import SCHED_MARKETING_READOUT


def _load(db: Session, job: Job) -> MarketingAiGeneration | None:
    g = db.get(MarketingAiGeneration, job.payload["generation_id"])
    if g is None or g.status != MarketingGenerationStatus.generating:
        return None
    return g


def _fail_over_budget(db: Session, g: MarketingAiGeneration) -> None:
    g.status = MarketingGenerationStatus.failed
    g.error = "over_budget"
    db.flush()


_RETRY_PLACEHOLDERS = (
    "Your previous answer contained bracket placeholders such as [link]. Rewrite it with real, "
    "finished text and no square-bracket placeholders at all."
)


def _complete_checked(
    db: Session,
    startup_id: Any,
    build: Callable[[str], list],
    schema: dict,
    problem: Callable[[dict], str | None],
) -> dict | None:
    """Call the model; if the answer has a fixable problem (bracket placeholders, copy over the
    length limit) ask once more, naming the problem. Returns the retry's answer when there is
    one, else the first. None only when the daily budget is spent on the first call."""
    result = metered_complete_json(
        db, startup_id, build(""), schema=schema, max_tokens=settings.LLM_MAX_TOKENS
    )
    if result is None:
        return None
    note = problem(result)
    if note:
        retry = metered_complete_json(
            db, startup_id, build(note), schema=schema, max_tokens=settings.LLM_MAX_TOKENS
        )
        if retry is not None:
            result = retry
    return result


def handle_marketing_copy(db: Session, job: Job) -> None:
    """Fill a copy generation with 3 variants via the LLM, or 3 refinements of one variant when
    `inputs.refine` is set. Platform length limits and bracket placeholders are checked once. No commit.
    """
    g = _load(db, job)
    if g is None:
        return
    startup = db.get(Startup, g.startup_id)
    if startup is None:
        return
    inp = g.inputs
    segment_name = None
    seg_id = inp.get("audience_segment_id")
    if seg_id:
        seg = db.get(AudienceSegment, seg_id)
        segment_name = seg.name if seg is not None else None
    plat = limit_for(inp.get("platform"))
    char_limit = plat.limit if plat else None
    context = fence(build_marketing_context(db, startup))

    refine = inp.get("refine")
    parent_variant = ""
    if refine:
        parent = db.get(MarketingAiGeneration, refine.get("parent_generation_id"))
        variants_in = (parent.output or {}).get("variants", []) if parent is not None else []
        idx = refine.get("variant_index", 0)
        if parent is None or parent.startup_id != g.startup_id or not 0 <= idx < len(variants_in):
            g.status = MarketingGenerationStatus.failed
            g.error = "parent_missing"
            db.flush()
            return
        parent_variant = str(variants_in[idx])

    def build(note: str) -> list:
        common = {
            "asset_type": inp.get("asset_type", ""),
            "channel": inp.get("channel"),
            "tone": inp.get("tone", ""),
            "key_message": inp.get("key_message", ""),
            "platform_label": plat.label if plat else None,
            "char_limit": char_limit,
            "context": context,
            "retry_note": note,
        }
        if refine:
            return build_refine_messages(
                variant=parent_variant, instruction=refine.get("instruction", ""), **common
            )
        return build_copy_messages(cta=inp.get("cta"), segment_name=segment_name, **common)

    def problem(result: dict) -> str | None:
        variants = [str(v) for v in (result.get("variants") or [])]
        if has_placeholder(*variants):
            return _RETRY_PLACEHOLDERS
        if char_limit and any(len(v) > char_limit for v in variants):
            return f"Every variant must be at most {char_limit} characters. Shorten them."
        return None

    result = _complete_checked(db, g.startup_id, build, copy_schema(), problem)
    if result is None:
        _fail_over_budget(db, g)
        return
    variants = [str(v) for v in (result.get("variants") or [])][:3]
    g.output = {
        "variants": variants,
        "platform": plat.key.value if plat else None,
        "limit": char_limit,
        "lengths": [len(v) for v in variants],
        "over_limit": [i for i, v in enumerate(variants) if char_limit and len(v) > char_limit],
    }
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
    context = fence(build_marketing_context(db, startup))

    def build(note: str) -> list:
        return build_plan_week_messages(
            stage=(startup.stage.value if startup.stage else None),
            industry=startup.industry,
            name=startup.name,
            context=context,
            retry_note=note,
        )

    def problem(result: dict) -> str | None:
        texts = [
            str(e.get(k, ""))
            for e in (result.get("entries") or [])
            if isinstance(e, dict)
            for k in ("title", "body")
        ]
        return _RETRY_PLACEHOLDERS if has_placeholder(*texts) else None

    result = _complete_checked(db, g.startup_id, build, plan_week_schema(), problem)
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
            context=fence(build_marketing_context(db, startup)),
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
            context=fence(build_marketing_context(db, startup)),
        ),
        schema=channel_fit_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        _fail_over_budget(db, g)
        return
    valid = {c.value for c in ChannelKey}
    notes: dict[str, str] = {}
    levels: dict[str, str] = {}
    for k, v in (result.get("notes") or {}).items():
        if k not in valid:
            continue
        if isinstance(v, dict):  # {"level": "high", "note": "..."}
            notes[k] = str(v.get("note", ""))
            if v.get("level") in {f.value for f in FitLevel}:
                levels[k] = v["level"]
        else:  # a bare note with no level
            notes[k] = str(v)
    now = datetime.now(UTC)
    by_key = {c.key.value: c for c in channels}
    for key, note in notes.items():
        row = by_key.get(key)
        if row is not None:
            row.ai_fit_note = note
            row.fit_level = FitLevel(levels[key]) if key in levels else None
            row.fit_note_generated_at = now
    g.output = {"notes": notes, "levels": levels}
    g.status = MarketingGenerationStatus.ready
    db.flush()


register_handler("ai.marketing.channel_fit", handle_marketing_channel_fit)


def handle_marketing_content_gap(db: Session, job: Job) -> None:
    """Propose up to 7 SEO content-gap ideas grounded in tracked keywords. No commit."""
    g = _load(db, job)
    if g is None:
        return
    startup = db.get(Startup, g.startup_id)
    if startup is None:
        return
    keywords = [
        k.keyword
        for k in db.query(SeoKeyword)
        .filter_by(startup_id=g.startup_id)
        .order_by(SeoKeyword.created_at.desc())
        .limit(10)
        .all()
    ]
    result = metered_complete_json(
        db,
        g.startup_id,
        build_content_gap_messages(
            keywords=keywords,
            stage=(startup.stage.value if startup.stage else None),
            industry=startup.industry,
            context=fence(build_marketing_context(db, startup)),
        ),
        schema=content_gap_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        _fail_over_budget(db, g)
        return
    gaps = [x for x in (result.get("gaps") or []) if isinstance(x, dict)][:7]
    g.output = {"gaps": gaps}
    g.status = MarketingGenerationStatus.ready
    db.flush()


register_handler("ai.marketing.content_gap", handle_marketing_content_gap)


def handle_marketing_readout(db: Session, job: Job) -> None:
    """Write this week's readout for one active campaign and tell the marketing team. The row
    is unique per (campaign, week), so a re-run never writes or notifies twice. No commit."""
    campaign = db.get(Campaign, uuid.UUID(str(job.payload["campaign_id"])))
    startup_id = str(job.payload["startup_id"])
    if (
        campaign is None
        or str(campaign.startup_id) != startup_id
        or campaign.status != CampaignStatus.active
    ):
        return
    week_start = date.fromisoformat(str(job.payload["week_start"]))
    existing = (
        db.query(CampaignReadout).filter_by(campaign_id=campaign.id, week_start=week_start).first()
    )
    if existing is not None:
        return
    row = CampaignReadout(
        startup_id=campaign.startup_id,
        campaign_id=campaign.id,
        week_start=week_start,
        status=MarketingGenerationStatus.generating,
    )
    db.add(row)
    db.flush()
    figures = build_figures(db, campaign, datetime.now(UTC).date())
    output = generate_verified_output(db, campaign, figures)
    if output is None:
        row.status = MarketingGenerationStatus.failed
        row.error = "over_budget"
        db.flush()
        return
    row.output = {**output, "figures": figures}
    row.status = MarketingGenerationStatus.ready
    db.flush()
    event_bus.publish(
        db,
        "marketing.campaign.readout.ready",
        {
            "startup_id": startup_id,
            "campaign_id": str(campaign.id),
            "readout_id": str(row.id),
            "name": campaign.name,
            "href": f"/marketing/campaigns/{campaign.id}",
        },
    )


register_handler(SCHED_MARKETING_READOUT, handle_marketing_readout)
