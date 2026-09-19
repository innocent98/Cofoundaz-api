from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.models.assessment import AssessmentResult
from app.db.models.business import BusinessRecord
from app.db.models.enums import CanvasType, RecordKind
from app.db.models.job import Job
from app.db.models.mission import Mission, MissionTask
from app.db.models.startup import Startup
from app.platform.llm import get_llm_client
from app.services.assessment.narrative import build_narrative_messages
from app.services.business.ai_fill import build_canvas_fill_messages, build_record_fill_messages
from app.services.business.canvas_defs import CANVAS_BLOCKS, canvas_json_schema
from app.services.business.record_defs import record_json_schema
from app.services.business.records import create_record
from app.services.business.service import get_or_create_canvas, validate_blocks
from app.services.mission.ai_reason import build_mission_reason_messages, mission_reason_schema
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


def handle_record_ai_fill(db: Session, job: Job) -> None:
    """Draft up to 3 records for an EMPTY record kind via structured output. No commit."""
    kind = RecordKind(job.payload["kind"])
    startup = db.get(Startup, job.payload["startup_id"])
    if startup is None:
        return
    if db.query(BusinessRecord).filter_by(startup_id=startup.id, kind=kind).count():
        return  # fill-empties only
    result = get_llm_client().complete_json(
        build_record_fill_messages(
            kind,
            name=startup.name,
            industry=startup.industry,
            stage=(startup.stage.value if startup.stage else None),
        ),
        schema=record_json_schema(kind),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
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
    result = get_llm_client().complete_json(
        build_mission_reason_messages(
            [(t.order, t.title) for t in tasks],
            name=(startup.name if startup else None),
            industry=(startup.industry if startup else None),
            stage=(startup.stage.value if (startup and startup.stage) else None),
        ),
        schema=mission_reason_schema(len(tasks)),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
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
