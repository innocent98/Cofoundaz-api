from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.business import BusinessPlan
from app.db.models.enums import BusinessPlanStatus, DocumentKind
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.platform.llm import get_llm_client
from app.platform.llm_budget import debit, over_budget
from app.services.business.plan_context import build_plan_context
from app.services.business.plan_defs import PLAN_SECTIONS
from app.services.business.plan_prompt import build_section_messages
from app.services.documents.service import create_document
from app.worker.runner import register_handler


def handle_plan_generate(db: Session, job: Job) -> None:
    """Generate a business plan section-by-section and store it as a Document. No commit."""
    plan = db.get(BusinessPlan, job.payload["plan_id"])
    if plan is None or plan.status != BusinessPlanStatus.generating:
        return  # benign no-op (missing / already resolved)
    startup = db.get(Startup, plan.startup_id)
    if startup is None:
        return
    if over_budget(db, startup.id):
        return  # skip — keep the plan `generating` for a later retry; no partial plan
    context = build_plan_context(db, startup)
    client = get_llm_client()
    sections = []
    for s in PLAN_SECTIONS:
        body = client.complete(
            build_section_messages(s, context), max_tokens=settings.LLM_MAX_TOKENS
        )
        debit(db, startup.id, getattr(client, "last_usage_tokens", 0))
        sections.append({"heading": s.heading, "body": body.strip()})
    doc = create_document(
        db,
        startup,
        created_by_id=plan.created_by_id,
        kind=DocumentKind.business_plan,
        title=f"{startup.name or 'Business'} — Business Plan",
        sections=sections,
        folder=None,
        template_key=None,
        ai_generated=True,
    )
    plan.document_id = doc.id
    plan.status = BusinessPlanStatus.complete
    db.flush()
    event_bus.publish(
        db,
        "business.plan.generated",
        {"startup_id": str(startup.id), "plan_id": str(plan.id), "document_id": str(doc.id)},
    )


register_handler("business.plan.generate", handle_plan_generate)
