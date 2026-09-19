from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.assessment import AssessmentResult
from app.db.models.enums import CanvasType
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.platform.llm import get_llm_client
from app.services.assessment.narrative import build_narrative_messages
from app.services.business.ai_fill import build_canvas_fill_messages
from app.services.business.canvas_defs import CANVAS_BLOCKS, canvas_json_schema
from app.services.business.service import get_or_create_canvas, validate_blocks
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
    text = get_llm_client().complete(messages, max_tokens=settings.LLM_MAX_TOKENS)
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
    filled = get_llm_client().complete_json(
        messages, schema=canvas_json_schema(canvas_type), max_tokens=settings.LLM_MAX_TOKENS
    )
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


register_handler("business.canvas.ai_fill", handle_canvas_ai_fill)
