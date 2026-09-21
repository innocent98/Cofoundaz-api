from datetime import UTC, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.config import settings
from app.core.envelope import success_response
from app.db.models.enums import JobStatus
from app.db.models.job import Job
from app.db.models.llm_usage import LlmUsageDaily
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_workspace
from app.platform.llm_budget import is_ai_enrichment_job, over_budget, today_utc

router = APIRouter()

_MAX_RECENT_FAILURES = 20


@router.get("/status")
def ai_status(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup_id = membership.startup_id

    used = (
        db.execute(
            select(LlmUsageDaily.tokens_used).where(
                LlmUsageDaily.startup_id == startup_id,
                LlmUsageDaily.usage_date == today_utc(),
            )
        ).scalar_one_or_none()
        or 0
    )
    budget = settings.LLM_DAILY_TOKEN_BUDGET
    resets_at = datetime.combine(today_utc() + timedelta(days=1), time.min, tzinfo=UTC)

    failed_jobs = (
        db.query(Job)
        .filter(Job.startup_id == startup_id, Job.status == JobStatus.failed)
        .order_by(Job.updated_at.desc())
        .limit(200)
        .all()
    )
    failures = [
        {"type": j.type, "failed_at": j.updated_at.isoformat() if j.updated_at else None}
        for j in failed_jobs
        if is_ai_enrichment_job(j.type)
    ][:_MAX_RECENT_FAILURES]

    return success_response(
        {
            "tokens_used_today": used,
            "daily_budget": budget if budget > 0 else None,
            "over_budget": over_budget(db, startup_id),
            "resets_at": resets_at.isoformat(),
            "recent_enrichment_failures": failures,
        }
    )
