import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.models.ai_chat import AiConversation, AiMessage
from app.db.models.assessment import AssessmentResult
from app.db.models.business import BusinessCanvas, BusinessRecord
from app.db.models.dashboard import DailyBriefing
from app.db.models.enums import (
    BriefingStatus,
    CanvasType,
    ChatMessageRole,
    ChatMessageStatus,
    EnrichmentStatus,
    RecommendationStatus,
    RecordKind,
)
from app.db.models.health_score import HealthRecommendation
from app.db.models.job import Job
from app.db.models.journal import JournalPrompt
from app.db.models.learning import LearningRecommendation
from app.db.models.mission import Mission, MissionTask
from app.db.models.roadmap import Roadmap, RoadmapReplan
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.activity import write_activity
from app.platform.events import event_bus
from app.platform.llm_budget import metered_complete, metered_complete_json
from app.services.ai_cofounder.prompts import build_cofounder_messages
from app.services.assessment.narrative import build_narrative_messages
from app.services.business.ai_fill import build_canvas_fill_messages, build_record_fill_messages
from app.services.business.canvas_defs import CANVAS_BLOCKS, canvas_json_schema
from app.services.business.record_defs import record_json_schema
from app.services.business.records import create_record
from app.services.business.service import (
    get_or_create_canvas,
    snapshot_canvas,
    validate_blocks,
)
from app.services.dashboard.ai_briefing import (
    build_dashboard_briefing_messages,
    dashboard_briefing_schema,
)
from app.services.dashboard.briefing import (
    normalize_actions,
    normalize_opportunities,
    normalize_risks,
    prose,
)
from app.services.dashboard.service import gather_briefing_context
from app.services.health_score.ai_recommendations import (
    build_health_recommendation_messages,
    catalog_bodies,
    health_recommendation_schema,
)
from app.services.journal.ai_prompt import (
    build_journal_prompt_messages,
    gather_prompt_context,
    journal_prompt_schema,
)
from app.services.learning.ai_reason import build_learning_reason_messages, learning_reason_schema
from app.services.learning.service import recommended_courses
from app.services.mission.ai_reason import build_mission_reason_messages, mission_reason_schema
from app.services.onboarding.ai_panel import build_onboarding_panel_messages
from app.services.roadmap.ai_rationale import build_roadmap_rationale_messages
from app.worker.runner import register_handler


def handle_assessment_narrative(db: Session, job: Job) -> None:
    """Rewrite an assessment's narrative with the LLM. No commit — the runner owns the txn."""
    assessment_id = job.payload["assessment_id"]
    startup_id = job.payload["startup_id"]
    result = (
        db.query(AssessmentResult)
        .filter(AssessmentResult.assessment_id == assessment_id)
        .one_or_none()
    )
    if result is None:
        return  # nothing to enrich (result missing) — benign no-op
    startup = db.get(Startup, startup_id)
    industry = startup.industry if startup else None
    stage = startup.stage.value if (startup and startup.stage) else None
    messages = build_narrative_messages(
        dimension_scores=result.dimension_scores,
        overall=result.overall_provisional,
        industry=industry,
        stage=stage,
    )
    text = metered_complete(db, startup_id, messages, max_tokens=settings.LLM_MAX_TOKENS)
    if text is None:
        return  # over budget — skip enrichment, keep the templated narrative
    result.narrative = text.strip()
    # Flush (not commit) so the change is visible to any read on this connection before
    # the runner's own db.commit() finalizes the job — the runner still owns the txn.
    db.flush()


register_handler("ai.assessment.narrative", handle_assessment_narrative)


def handle_canvas_ai_fill(db: Session, job: Job) -> None:
    """Draft a business canvas's EMPTY blocks with the LLM (structured output).

    Fill-empties-only: re-reads the canvas at run time and never overwrites a block the
    user already filled. No commit — the runner owns the txn.
    """
    startup = db.get(Startup, job.payload["startup_id"])
    if startup is None:
        return  # benign no-op
    canvas_type = CanvasType(job.payload["canvas_type"])
    canvas = get_or_create_canvas(db, startup, canvas_type)
    if job.payload.get("block"):
        _canvas_section_fill(db, job, startup, canvas)
        return
    current = dict(canvas.blocks or {})
    empty_keys = [b.key for b in CANVAS_BLOCKS[canvas_type] if not current.get(b.key)]
    if not empty_keys:
        return  # nothing to fill
    messages = build_canvas_fill_messages(
        name=startup.name,
        industry=startup.industry,
        stage=(startup.stage.value if startup.stage else None),
        blocks=CANVAS_BLOCKS[canvas_type],
    )
    filled = metered_complete_json(
        db,
        startup.id,
        messages,
        schema=canvas_json_schema(canvas_type),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if filled is None:
        return  # over budget — skip enrichment, keep the empty blocks
    # Re-read: a concurrent PATCH may have committed during the (multi-second) LLM call.
    # Under READ COMMITTED this reflects the latest committed blocks/version, so we never
    # clobber an edit that landed while we were waiting on the model.
    db.refresh(canvas)
    latest = dict(canvas.blocks or {})
    merged = dict(latest)
    for b in CANVAS_BLOCKS[canvas_type]:
        if not latest.get(b.key) and b.key in filled:  # only blocks STILL empty now
            merged[b.key] = filled[b.key]
    if merged == latest:
        return  # nothing left to fill (user filled everything meanwhile) — no version bump
    validate_blocks(canvas_type, merged)
    canvas.blocks = merged
    canvas.version += 1
    db.flush()
    snapshot_canvas(db, canvas, user_id=None, source="ai")


def _canvas_section_fill(db: Session, job: Job, startup: Startup, canvas: BusinessCanvas) -> None:
    """PRD 08 per-section AI assist (D-BB-4): append-only into ONE block. Result in job.result."""
    from app.services.business import ai_assist

    key = str(job.payload["block"])
    block = ai_assist.block_def(canvas, key)
    if block is None:
        job.result = {"status": "gone", "block": key, "added": []}
        return
    result = metered_complete_json(
        db,
        startup.id,
        ai_assist.build_section_messages(startup, canvas, block),
        schema=ai_assist.section_schema(block),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        job.result = {"status": "over_budget", "block": key, "added": []}
        return
    db.refresh(canvas)  # a concurrent edit during the LLM call wins; we only append to it
    added = ai_assist.merge_section(canvas, block, result)
    if not added:
        job.result = {"status": "nothing_new", "block": key, "added": []}
        return
    validate_blocks(canvas.type, canvas.blocks)
    canvas.version += 1
    db.flush()
    snapshot_canvas(db, canvas, user_id=None, source="ai")
    job.result = {"status": "ready", "block": key, "added": added, "version": canvas.version}


register_handler("business.canvas.ai_fill", handle_canvas_ai_fill)


def handle_record_ai_fill(db: Session, job: Job) -> None:
    """Draft up to 3 records for an EMPTY record kind via structured output. No commit."""
    kind = RecordKind(job.payload["kind"])
    startup = db.get(Startup, job.payload["startup_id"])
    if startup is None:
        return
    if db.query(BusinessRecord).filter_by(startup_id=startup.id, kind=kind).count():
        return  # fill-empties only
    result = metered_complete_json(
        db,
        startup.id,
        build_record_fill_messages(
            kind,
            name=startup.name,
            industry=startup.industry,
            stage=(startup.stage.value if startup.stage else None),
        ),
        schema=record_json_schema(kind),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        return  # over budget — skip enrichment, keep the kind empty
    for rec in (result.get("records") or [])[:3]:
        try:
            create_record(db, startup, kind, rec)
        except AppError:
            continue  # skip a record that fails the kind's validation; keep the good ones
    db.flush()


for _kind in RecordKind:
    register_handler(f"business.{_kind.value}.ai_fill", handle_record_ai_fill)


def handle_mission_reason(db: Session, job: Job) -> None:
    """Rewrite each task's reason for a mission via the LLM (structured output). No commit.

    The templated reason written at generation stays as the instant value and the fallback: a
    task the model returns no reason for is left untouched.
    """
    mission = db.get(Mission, job.payload["mission_id"])
    if mission is None:
        return  # benign no-op
    tasks = db.query(MissionTask).filter_by(mission_id=mission.id).order_by(MissionTask.order).all()
    if not tasks:
        return
    startup = db.get(Startup, mission.startup_id)
    result = metered_complete_json(
        db,
        mission.startup_id,
        build_mission_reason_messages(
            [(t.order, t.title) for t in tasks],
            name=(startup.name if startup else None),
            industry=(startup.industry if startup else None),
            stage=(startup.stage.value if (startup and startup.stage) else None),
        ),
        schema=mission_reason_schema(len(tasks)),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        return  # over budget — skip enrichment, keep the templated reasons
    by_order = {
        r["order"]: r["reason"]
        for r in (result.get("reasons") or [])
        if isinstance(r, dict) and "order" in r and "reason" in r
    }
    for t in tasks:
        new = by_order.get(t.order)
        if new:
            t.reason = new[:300]
    db.flush()


register_handler("ai.mission.reason", handle_mission_reason)


def handle_health_recommendations(db: Session, job: Job) -> None:
    """Personalize pending recommendation bodies via the LLM (structured output). No commit.

    Idempotent: only rewrites pending rows whose body is still the catalog default, and makes no
    LLM call when nothing is left to personalize -- so repeated recomputes cost nothing. Never
    touches accepted/dismissed rows (user decisions).
    """
    startup = db.get(Startup, job.payload["startup_id"])
    if startup is None:
        return  # benign no-op
    defaults = catalog_bodies()
    rows = (
        db.query(HealthRecommendation)
        .filter_by(startup_id=startup.id, status=RecommendationStatus.pending)
        .all()
    )
    todo = [r for r in rows if r.body == defaults.get(r.key)]
    if not todo:
        return  # nothing to personalize -- no LLM call
    result = metered_complete_json(
        db,
        startup.id,
        build_health_recommendation_messages(
            [(r.key, r.dimension, r.title) for r in todo],
            name=startup.name,
            industry=startup.industry,
            stage=(startup.stage.value if startup.stage else None),
        ),
        schema=health_recommendation_schema([r.key for r in todo]),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        return  # over budget — skip enrichment, keep the catalog default bodies
    by_key = {
        r["key"]: r["body"]
        for r in (result.get("recommendations") or [])
        if isinstance(r, dict) and "key" in r and "body" in r
    }
    for r in todo:
        new = by_key.get(r.key)
        if new:
            r.body = new
    db.flush()


register_handler("ai.health.recommendations", handle_health_recommendations)


def handle_dashboard_briefing(db: Session, job: Job) -> None:
    """Fill a startup's daily dashboard briefing via the LLM (structured output). No commit.

    Idempotent: no-ops unless the row is still `generating`. The `generating` placeholder text
    stays as the instant value and fallback if the job never completes.
    """
    startup = db.get(Startup, job.payload["startup_id"])
    if startup is None:
        return
    row = (
        db.query(DailyBriefing)
        .filter_by(
            startup_id=startup.id,
            briefing_date=date.fromisoformat(job.payload["briefing_date"]),
        )
        .one_or_none()
    )
    if row is None or row.status != BriefingStatus.generating:
        return
    ctx = gather_briefing_context(db, startup)
    result = metered_complete_json(
        db,
        startup.id,
        build_dashboard_briefing_messages(**ctx),
        schema=dashboard_briefing_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        return  # over budget — skip enrichment, keep the "generating" placeholder
    risks = normalize_risks(result.get("risks"))
    opportunities = normalize_opportunities(result.get("opportunities"))
    row.briefing = str(result.get("briefing") or "").strip() or row.briefing
    row.risk_items = risks
    row.opportunity_items = opportunities
    row.actions = normalize_actions(result.get("actions"))
    # Legacy prose columns, for readers that predate the structured items.
    row.risks = prose(risks)
    row.opportunities = prose(opportunities)
    row.status = BriefingStatus.ready
    db.flush()
    event_bus.publish(
        db,
        "dashboard.briefing.ready",
        {"startup_id": str(startup.id), "briefing_id": str(row.id)},
    )


register_handler("ai.dashboard.briefing", handle_dashboard_briefing)


def handle_roadmap_rationale(db: Session, job: Job) -> None:
    """Overwrite a roadmap re-plan's rationale with the LLM (prose). No commit.

    The templated rationale written at apply time stays as the instant value and fallback.
    """
    replan = db.get(RoadmapReplan, job.payload["replan_id"])
    if replan is None:
        return  # benign no-op
    roadmap = db.get(Roadmap, replan.roadmap_id)
    if roadmap is None:
        return  # benign no-op (orphaned replan)
    startup = db.get(Startup, roadmap.startup_id)
    messages = build_roadmap_rationale_messages(
        stage=(startup.stage.value if (startup and startup.stage) else None),
        name=(startup.name if startup else None),
        industry=(startup.industry if startup else None),
        changes=list(replan.changes or []),
    )
    text = metered_complete(db, roadmap.startup_id, messages, max_tokens=settings.LLM_MAX_TOKENS)
    if text is None:
        return  # over budget — skip enrichment, keep the templated rationale
    replan.rationale = text.strip()
    db.flush()


register_handler("ai.roadmap.rationale", handle_roadmap_rationale)


def handle_onboarding_panel(db: Session, job: Job) -> None:
    """Overwrite a startup's onboarding AI panel with the LLM (prose). No commit.

    The templated panel written at signals-complete stays as the instant value and fallback.
    """
    startup = db.get(Startup, job.payload["startup_id"])
    if startup is None or startup.profile is None:
        return  # benign no-op
    messages = build_onboarding_panel_messages(
        industry=startup.industry,
        stage=(startup.stage.value if startup.stage else None),
        goals=(startup.profile.goals or []),
    )
    text = metered_complete(db, startup.id, messages, max_tokens=settings.LLM_MAX_TOKENS)
    if text is None:
        return  # over budget — skip enrichment, keep the templated panel
    startup.profile.ai_panel = text.strip()
    db.flush()


register_handler("ai.onboarding.panel", handle_onboarding_panel)


def handle_learning_recommendations(db: Session, job: Job) -> None:
    """Write the shelf-level recommendation reason via the LLM. No commit.

    Idempotent: no-ops unless the row is still `generating`. Keeps the templated fallback when the
    startup has no stage, no recommendable courses, or the workspace is over budget.
    """
    startup = db.get(Startup, job.payload["startup_id"])
    if startup is None:
        return
    row = db.query(LearningRecommendation).filter_by(startup_id=startup.id).one_or_none()
    if row is None or row.status != EnrichmentStatus.generating:
        return
    stage = startup.stage
    titles = [c.title for c in recommended_courses(stage, set())] if stage is not None else []
    if stage is None or not titles:  # nothing to personalize -> keep fallback, no LLM call
        row.status = EnrichmentStatus.ready
        db.flush()
        return
    result = metered_complete_json(
        db,
        startup.id,
        build_learning_reason_messages(stage=stage, course_titles=titles),
        schema=learning_reason_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        return  # over budget — keep the templated reason, stay generating
    row.reason = str(result["reason"])[:300]
    row.status = EnrichmentStatus.ready
    db.flush()


register_handler("ai.learning.recommendations", handle_learning_recommendations)


def handle_journal_prompt(db: Session, job: Job) -> None:
    """Personalize a founder's daily journal prompt via the LLM. No commit.

    Grounds ONLY in operational signals (shipped milestone + mission focus) — never journal
    content or mood. Idempotent: no-ops unless the row is still `generating`. Keeps the static
    prompt when there is no signal or the workspace is over budget.
    """
    startup = db.get(Startup, job.payload["startup_id"])
    if startup is None:
        return
    founder = db.get(User, job.payload["founder_id"])
    if founder is None:
        return
    d = date.fromisoformat(job.payload["date"])
    row = (
        db.query(JournalPrompt)
        .filter_by(startup_id=startup.id, founder_id=founder.id, date=d)
        .one_or_none()
    )
    if row is None or row.status != EnrichmentStatus.generating:
        return
    milestone_title, mission_focus = gather_prompt_context(db, startup.id)
    if milestone_title is None and mission_focus is None:  # no signal -> keep static, no LLM
        row.status = EnrichmentStatus.ready
        db.flush()
        return
    result = metered_complete_json(
        db,
        startup.id,
        build_journal_prompt_messages(milestone_title=milestone_title, mission_focus=mission_focus),
        schema=journal_prompt_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        return  # over budget — keep the static prompt, stay generating
    row.prompt = str(result["prompt"])[:300]
    row.status = EnrichmentStatus.ready
    db.flush()


register_handler("ai.journal.prompt", handle_journal_prompt)


def handle_cofounder_reply(db: Session, job: Job) -> None:
    """Fill the assistant turn of an AI Co-Founder chat via the LLM (prose). No commit.

    Idempotent: no-ops unless the assistant row is still `generating`. A chat turn has no
    templated fallback, so over budget -> status `failed` (the FE polls and surfaces it).
    """
    msg = db.get(AiMessage, uuid.UUID(job.payload["message_id"]))
    if (
        msg is None
        or msg.role != ChatMessageRole.assistant
        or msg.status != ChatMessageStatus.generating
    ):
        return
    conversation = db.get(AiConversation, msg.conversation_id)
    if conversation is None:
        return
    startup = db.get(Startup, conversation.startup_id)
    if startup is None:
        return
    # Cap history to the most recent turns so one long conversation can't amplify the
    # LLM input cost of every subsequent reply.
    prior = list(
        reversed(
            db.query(AiMessage)
            .filter(
                AiMessage.conversation_id == conversation.id,
                AiMessage.id != msg.id,
                AiMessage.status == ChatMessageStatus.ready,
            )
            .order_by(AiMessage.created_at.desc(), AiMessage.id.desc())
            .limit(20)
            .all()
        )
    )
    profile = startup.profile
    messages = build_cofounder_messages(
        name=startup.name,
        industry=startup.industry,
        stage=(startup.stage.value if startup.stage else None),
        description=startup.description,
        goals=(list(profile.goals) if profile and profile.goals else []),
        history=[(m.role.value, m.content) for m in prior if m.content],
    )
    text = metered_complete(db, startup.id, messages, max_tokens=settings.LLM_MAX_TOKENS)
    if text is None:
        msg.status = ChatMessageStatus.failed  # over budget — no templated fallback for a chat turn
        db.flush()
        return
    msg.content = text.strip()
    msg.status = ChatMessageStatus.ready
    db.flush()


register_handler("ai.cofounder.reply", handle_cofounder_reply)


def handle_roadmap_breakdown(db: Session, job: Job) -> None:
    """PRD 05.3 "Ask AI to break this down": append 3-5 tasks to a milestone. No commit.
    The outcome is written to `job.result` for the FE poll."""
    from app.db.models.roadmap import RoadmapMilestone
    from app.services.roadmap.breakdown import apply_breakdown, breakdown_schema
    from app.services.roadmap.breakdown import build_breakdown_messages as _msgs

    startup = db.get(Startup, uuid.UUID(str(job.payload["startup_id"])))
    milestone = db.get(RoadmapMilestone, uuid.UUID(str(job.payload["milestone_id"])))
    if startup is None or milestone is None:
        job.result = {"status": "gone"}
        return
    existing = [t.title for t in milestone_tasks(db, milestone.id)]
    result = metered_complete_json(
        db,
        startup.id,
        _msgs(
            milestone=milestone.title,
            description=milestone.description,
            existing=existing,
            stage=startup.stage.value if startup.stage else None,
            industry=startup.industry,
        ),
        schema=breakdown_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        job.result = {"status": "over_budget", "milestone_id": str(milestone.id)}
        return
    created = apply_breakdown(db, milestone, result.get("tasks"))
    write_activity(
        db,
        startup_id=startup.id,
        action="roadmap.milestone.broken_down",
        entity_type="roadmap_milestone",
        entity_id=milestone.id,
        summary=f"AI broke '{milestone.title}' into {len(created)} tasks",
    )
    job.result = {
        "status": "ready",
        "milestone_id": str(milestone.id),
        "created_task_ids": [str(t.id) for t in created],
    }


def milestone_tasks(db: Session, milestone_id: uuid.UUID) -> list:
    from app.db.models.roadmap import RoadmapTask

    return db.query(RoadmapTask).filter(RoadmapTask.milestone_id == milestone_id).all()


register_handler("ai.roadmap.breakdown", handle_roadmap_breakdown)


def handle_mission_vision_options(db: Session, job: Job) -> None:
    """PRD 08.4 "Give me 3 options": variants in job.result, nothing written. No commit."""
    from app.services.business import ai_assist

    startup = db.get(Startup, uuid.UUID(str(job.payload["startup_id"])))
    field = str(job.payload["field"])
    if startup is None:
        job.result = {"status": "gone", "field": field, "options": []}
        return
    canvas = get_or_create_canvas(db, startup, CanvasType.mission_vision)
    result = metered_complete_json(
        db,
        startup.id,
        ai_assist.build_options_messages(startup, field, str(canvas.blocks.get(field) or "")),
        schema=ai_assist.options_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        job.result = {"status": "over_budget", "field": field, "options": []}
        return
    job.result = {"status": "ready", "field": field, "options": ai_assist.clean_options(result)}


def handle_personas_from_validation(db: Session, job: Job) -> None:
    """PRD 08.6 "Generate personas from my validation notes": append up to 3 personas. No commit."""
    from app.services.business import ai_assist

    startup = db.get(Startup, uuid.UUID(str(job.payload["startup_id"])))
    if startup is None:
        job.result = {"status": "gone", "created_record_ids": []}
        return
    ctx = ai_assist.validation_context(db, startup)
    if not ai_assist.has_validation_data(ctx):
        job.result = {"status": "no_validation_data", "created_record_ids": []}
        return
    result = metered_complete_json(
        db,
        startup.id,
        ai_assist.build_persona_messages(startup, ctx),
        schema=record_json_schema(RecordKind.persona),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        job.result = {"status": "over_budget", "created_record_ids": []}
        return
    taken = ai_assist.existing_persona_names(db, startup)
    created = []
    for rec in (result.get("records") or [])[:3]:
        name = str(rec.get("name", "")).strip().casefold()
        if not name or name in taken:
            continue  # append-only and de-duplicated by name
        try:
            created.append(create_record(db, startup, RecordKind.persona, rec))
        except AppError:
            continue
        taken.add(name)
    db.flush()
    job.result = {
        "status": "ready" if created else "nothing_new",
        "created_record_ids": [str(r.id) for r in created],
        "interviews_used": len(ctx["interviews"]),
    }


def handle_pricing_advisor(db: Session, job: Job) -> None:
    """PRD 08.7 AI advisor panel: WTP prompts + suggested model/tiers in job.result. No commit."""
    from app.services.business import ai_assist

    startup = db.get(Startup, uuid.UUID(str(job.payload["startup_id"])))
    if startup is None:
        job.result = {"status": "gone"}
        return
    result = metered_complete_json(
        db,
        startup.id,
        ai_assist.build_advisor_messages(
            startup,
            job.payload.get("competitor_prices") or [],
            job.payload.get("willingness_to_pay"),
        ),
        schema=ai_assist.advisor_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        job.result = {"status": "over_budget"}
        return
    job.result = {"status": "ready", "advice": result}


register_handler("ai.business.mission_vision_options", handle_mission_vision_options)
register_handler("ai.business.personas_from_validation", handle_personas_from_validation)
register_handler("ai.business.pricing_advisor", handle_pricing_advisor)
