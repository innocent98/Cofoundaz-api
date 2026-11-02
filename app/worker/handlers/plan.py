import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import log
from app.db.models.business import BusinessPlan
from app.db.models.document import Document
from app.db.models.enums import BusinessPlanStatus, DocumentKind
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.db.session import SessionLocal
from app.platform.events import event_bus
from app.platform.llm import get_llm_client
from app.platform.llm_budget import debit, over_budget
from app.services.business.plan_context import build_plan_context
from app.services.business.plan_defs import PLAN_SECTIONS, SECTIONS_BY_KEY, STAGE_CAPTIONS
from app.services.business.plan_prompt import build_section_messages
from app.services.documents.service import (
    create_document,
    search_text_of,
    validate_sections,
)
from app.services.documents.versions import snapshot
from app.worker.runner import register_handler


def _report_progress(plan_id: uuid.UUID, progress: dict) -> None:
    """Publish progress from a separate short transaction so a polling FE sees it while the
    job's own transaction is still open (the runner commits only at the end). Best effort."""
    try:
        with SessionLocal() as s:
            s.query(BusinessPlan).filter(
                BusinessPlan.id == plan_id, BusinessPlan.status == BusinessPlanStatus.generating
            ).update({BusinessPlan.progress: progress}, synchronize_session=False)
            s.commit()
    except Exception as exc:  # noqa: BLE001 - progress is cosmetic; never fail the plan on it
        log.warning(f"[plan] progress update skipped: {exc}")


def _fail(db: Session, plan: BusinessPlan, reason: str) -> None:
    plan.status = BusinessPlanStatus.failed
    plan.error = reason
    db.flush()


def handle_plan_generate(db: Session, job: Job) -> None:
    """Generate a business plan section-by-section and store it as a Document. No commit.

    PRD 08.11: honours `plan.options` (audience, length), reports stage progress, and ends
    `failed` (never stuck `generating`) when over budget or when the LLM still fails on the
    job's last attempt; earlier attempts re-raise so the runner retries with backoff.
    """
    plan = db.get(BusinessPlan, job.payload["plan_id"])
    if plan is None or plan.status != BusinessPlanStatus.generating:
        return  # benign no-op (missing / already resolved)
    startup = db.get(Startup, plan.startup_id)
    if startup is None:
        return
    if over_budget(db, startup.id):
        _fail(db, plan, "over_budget")
        return
    context = build_plan_context(db, startup)
    client = get_llm_client()
    total = len(PLAN_SECTIONS)
    sections = []
    try:
        for i, s in enumerate(PLAN_SECTIONS):
            _report_progress(plan.id, {"done": i, "total": total, "stage": STAGE_CAPTIONS[s.key]})
            body = client.complete(
                build_section_messages(s, context, plan.options),
                max_tokens=settings.LLM_MAX_TOKENS,
            )
            debit(db, startup.id, getattr(client, "last_usage_tokens", 0))
            sections.append({"heading": s.heading, "body": body.strip()})
    except Exception:
        if (job.attempts or 0) >= settings.WORKER_MAX_ATTEMPTS:
            _fail(db, plan, "generation_failed")
            return
        raise
    doc = create_document(
        db,
        startup,
        created_by_id=plan.created_by_id,
        kind=DocumentKind.business_plan,
        title=f"{startup.name or 'Business'}: Business Plan",
        sections=sections,
        folder=None,
        template_key=None,
        ai_generated=True,
    )
    plan.document_id = doc.id
    plan.status = BusinessPlanStatus.complete
    plan.progress = {"done": total, "total": total, "stage": "Done"}
    db.flush()
    event_bus.publish(
        db,
        "business.plan.generated",
        {"startup_id": str(startup.id), "plan_id": str(plan.id), "document_id": str(doc.id)},
    )


def handle_plan_section(db: Session, job: Job) -> None:
    """PRD 08.11 "Regenerate a section": rewrite ONE section of the plan's Document in place
    (matched by its heading), leaving every other section exactly as the founder edited it.
    Outcome in job.result. No commit."""
    key = str(job.payload["section"])
    plan = db.get(BusinessPlan, uuid.UUID(str(job.payload["plan_id"])))
    doc = db.get(Document, plan.document_id) if plan and plan.document_id else None
    section = SECTIONS_BY_KEY.get(key)
    if plan is None or doc is None or section is None:
        job.result = {"status": "gone", "section": key}
        return
    startup = db.get(Startup, plan.startup_id)
    if startup is None:
        job.result = {"status": "gone", "section": key}
        return
    if over_budget(db, startup.id):
        job.result = {"status": "over_budget", "section": key}
        return
    client = get_llm_client()
    body = client.complete(
        build_section_messages(section, build_plan_context(db, startup), plan.options),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    debit(db, startup.id, getattr(client, "last_usage_tokens", 0))
    db.refresh(doc, with_for_update=True)  # pick up edits made while the model was writing
    if doc.trashed_at is not None:
        job.result = {"status": "gone", "section": key}
        return
    current = list(doc.sections or [])
    idx = next((i for i, sec in enumerate(current) if sec.get("heading") == section.heading), None)
    if idx is None:
        job.result = {"status": "section_missing", "section": key}
        return
    current[idx] = {**current[idx], "body": body.strip()}
    doc.sections = validate_sections(current)
    doc.search_text = search_text_of(doc.sections)
    doc.version += 1
    db.flush()
    snapshot(db, doc, author_id=None, note="AI regenerated a section")
    job.result = {"status": "ready", "section": key, "document_version": doc.version}


register_handler("business.plan.generate", handle_plan_generate)
register_handler("ai.business.plan_section", handle_plan_section)
