from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.assessment import AssessmentResult
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.platform.llm import get_llm_client
from app.services.assessment.narrative import build_narrative_messages
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
