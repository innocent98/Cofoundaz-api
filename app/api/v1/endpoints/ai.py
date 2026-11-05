import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
from app.db.models.enums import JobStatus, MembershipRole
from app.db.models.job import Job
from app.db.models.llm_usage import LlmUsageDaily
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.platform.llm_budget import is_ai_enrichment_job, over_budget, today_utc
from app.schemas.ai_chat import (
    ConversationIdsRequest,
    CreateConversationRequest,
    PostMessageRequest,
)
from app.services.ai_cofounder.service import (
    MAX_QUERY,
    MIN_QUERY,
    create_conversation,
    delete_conversation,
    fail_stale_generating,
    get_conversation,
    list_conversations,
    messages_for,
    post_message,
    serialize_conversation,
    serialize_message,
    set_archived,
)
from app.services.ai_cofounder.topics import TOPICS
from app.services.ai_preferences import AiPrefs, get_prefs, update_prefs

router = APIRouter()

_MAX_RECENT_FAILURES = 20

# The AI Co-Founder chat is for the founder and team members (the PRD's F/TM access for
# Module 03); mentors, accountants, and legal advisors do not get the founder's
# context-rich chat. Conversations are further scoped to their creator in the service.
_chat_access = require_role(MembershipRole.founder, MembershipRole.team_member)


class AiSettingsPatch(BaseModel):
    """Unknown fields are rejected (422); send only what changes. `tone` is a fixed set, never free text."""

    model_config = ConfigDict(extra="forbid")

    proactive_suggestions: bool | None = None
    daily_briefing: bool | None = None
    tone: Literal["straight_shooter", "encouraging"] | None = None


def _settings_json(p: AiPrefs) -> dict[str, Any]:
    return {
        "proactive_suggestions": p.proactive_suggestions,
        "daily_briefing": p.daily_briefing,
        "tone": p.tone,
    }


@router.get("/settings")
def get_ai_settings(
    membership: Membership = Depends(_chat_access),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        _settings_json(get_prefs(db, user_id=membership.user_id, startup_id=membership.startup_id))
    )


@router.patch("/settings")
def patch_ai_settings(
    body: AiSettingsPatch,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if not changes or any(v is None for v in changes.values()):
        raise AppError("VALIDATION_ERROR", "Send at least one setting, with a value.", 422)
    prefs = update_prefs(
        db, user_id=membership.user_id, startup_id=membership.startup_id, changes=changes
    )
    db.commit()
    return success_response(_settings_json(prefs))


@router.get("/status")
def ai_status(
    membership: Membership = Depends(_chat_access),  # noqa: B008
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
    query: str | None = Query(default=None),  # noqa: B008
    topic: str | None = Query(default=None),  # noqa: B008
    archived: bool = Query(default=False),  # noqa: B008
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    if topic is not None and topic not in TOPICS:
        raise AppError("VALIDATION_ERROR", f"topic must be one of: {', '.join(TOPICS)}.", 422)
    query = (query or "").strip() or None  # blank means no search
    if query is not None and not MIN_QUERY <= len(query) <= MAX_QUERY:
        raise AppError(
            "VALIDATION_ERROR", f"query must be {MIN_QUERY} to {MAX_QUERY} characters.", 422
        )
    if archived and query:
        raise AppError("VALIDATION_ERROR", "Archived conversations can't be searched.", 422)
    startup = _startup(db, membership)
    rows = list_conversations(db, startup, user, query=query, topic=topic, archived=archived)
    out = []
    for conv, snippet in rows:
        item = serialize_conversation(conv)
        if query:
            item["match_snippet"] = snippet
        out.append(item)
    return success_response(out)


@router.post("/conversations/archive")
def archive_conversations_endpoint(
    payload: ConversationIdsRequest,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    changed = set_archived(db, _startup(db, membership), user, payload.ids, archived=True)
    db.commit()
    return success_response({"archived": changed})


@router.post("/conversations/unarchive")
def unarchive_conversations_endpoint(
    payload: ConversationIdsRequest,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    changed = set_archived(db, _startup(db, membership), user, payload.ids, archived=False)
    db.commit()
    return success_response({"unarchived": changed})


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


@router.delete("/conversations/{conversation_id}")
def delete_conversation_endpoint(
    conversation_id: uuid.UUID,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """ "Delete forever": removes the conversation and every message in it."""
    conv = get_conversation(db, _startup(db, membership), user, conversation_id)
    delete_conversation(db, conv)
    db.commit()
    return success_response({"deleted": True})
