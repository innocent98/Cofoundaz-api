import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.enums import JobStatus, MembershipRole
from app.db.models.job import Job
from app.db.models.llm_usage import LlmUsageDaily
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.platform.llm_budget import is_ai_enrichment_job, over_budget, today_utc
from app.schemas.ai_chat import CreateConversationRequest, PostMessageRequest
from app.services.ai_cofounder.service import (
    create_conversation,
    fail_stale_generating,
    get_conversation,
    list_conversations,
    messages_for,
    post_message,
    serialize_conversation,
    serialize_message,
)

router = APIRouter()

_MAX_RECENT_FAILURES = 20

# The AI Co-Founder chat is for the founder and team members (the PRD's F/TM access for
# Module 03); mentors, accountants, and legal advisors do not get the founder's
# context-rich chat. Conversations are further scoped to their creator in the service.
_chat_access = require_role(MembershipRole.founder, MembershipRole.team_member)


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


def _startup(db: Session, membership: Membership) -> Startup:
    s = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if s is None:
        raise NotFound()
    return s


@router.post("/conversations", status_code=201)
def create_conversation_endpoint(
    payload: CreateConversationRequest,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    conv = create_conversation(db, startup, user, payload.message)
    db.commit()
    return success_response(serialize_conversation(conv, messages_for(db, conv)))


@router.get("/conversations")
def list_conversations_endpoint(
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    return success_response(
        [serialize_conversation(c) for c in list_conversations(db, startup, user)]
    )


@router.get("/conversations/{conversation_id}")
def get_conversation_endpoint(
    conversation_id: uuid.UUID,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    conv = get_conversation(db, startup, user, conversation_id)
    if fail_stale_generating(db, conv):
        db.commit()
    return success_response(serialize_conversation(conv, messages_for(db, conv)))


@router.post("/conversations/{conversation_id}/messages", status_code=202)
def post_message_endpoint(
    conversation_id: uuid.UUID,
    payload: PostMessageRequest,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    conv = get_conversation(db, startup, user, conversation_id)
    assistant = post_message(db, conv, payload.content)
    db.commit()
    return success_response(serialize_message(assistant))
