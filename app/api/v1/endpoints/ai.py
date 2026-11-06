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
    FeedbackRequest,
    PostMessageRequest,
    SnoozeRequest,
)
from app.services.ai_cofounder import actions as ai_actions
from app.services.ai_cofounder import suggestions as ai_suggestions
from app.services.ai_cofounder.bench import list_agents
from app.services.ai_cofounder.service import (
    MAX_QUERY,
    MIN_QUERY,
    cancel_message,
    create_conversation,
    delete_conversation,
    fail_stale_generating,
    get_conversation,
    list_conversations,
    post_message,
    regenerate_message,
    serialize_conversation,
    serialize_message,
    serialize_message_full,
    serialize_thread,
    set_archived,
    set_feedback,
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
    conv = create_conversation(db, startup, user, payload.message, agent_key=payload.agent_key)
    db.commit()
    return success_response(serialize_thread(db, conv))


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
    return success_response(serialize_thread(db, conv))


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


@router.post("/conversations/{conversation_id}/messages/{message_id}/cancel")
def cancel_message_endpoint(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """ "Cancel" a reply that is still generating (D-AI-2). 200 with the message, now `cancelled`
    (also when it already was); 409 `REPLY_NOT_RUNNING` if it finished first."""
    conv = get_conversation(db, _startup(db, membership), user, conversation_id)
    msg = cancel_message(db, conv, message_id)
    db.commit()
    return success_response(serialize_message_full(db, msg))


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


@router.post("/conversations/{conversation_id}/messages/{message_id}/regenerate", status_code=202)
def regenerate_endpoint(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Answer the latest reply again; also the Retry of a failed or cancelled one. Poll the
    conversation as for any reply. Metered like a new message."""
    msg = regenerate_message(db, _startup(db, membership), user, conversation_id, message_id)
    db.commit()
    return success_response(serialize_message_full(db, msg))


@router.post("/messages/{message_id}/feedback")
def feedback_endpoint(
    message_id: uuid.UUID,
    payload: FeedbackRequest,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    msg = set_feedback(
        db, _startup(db, membership), user, message_id, rating=payload.rating, note=payload.note
    )
    db.commit()
    return success_response(serialize_message_full(db, msg))


def _action_response(db: Session, a: Any) -> dict[str, Any]:
    return success_response(ai_actions.serialize_action(a))


@router.post("/messages/{message_id}/actions/{action_id}/confirm")
def confirm_action_endpoint(
    message_id: uuid.UUID,
    action_id: uuid.UUID,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Make the proposed change. Nothing is written before this call."""
    a = ai_actions.confirm_action(
        db,
        _startup(db, membership),
        user_id=user.id,
        role=membership.role,
        message_id=message_id,
        action_id=action_id,
    )
    db.commit()
    return _action_response(db, a)


@router.post("/messages/{message_id}/actions/{action_id}/dismiss")
def dismiss_action_endpoint(
    message_id: uuid.UUID,
    action_id: uuid.UUID,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    a = ai_actions.dismiss_action(
        db, _startup(db, membership), user_id=user.id, message_id=message_id, action_id=action_id
    )
    db.commit()
    return _action_response(db, a)


@router.post("/messages/{message_id}/actions/{action_id}/undo")
def undo_action_endpoint(
    message_id: uuid.UUID,
    action_id: uuid.UUID,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    a = ai_actions.undo_action(
        db,
        _startup(db, membership),
        user_id=user.id,
        role=membership.role,
        message_id=message_id,
        action_id=action_id,
    )
    db.commit()
    return _action_response(db, a)


@router.get("/suggestions")
def list_suggestions_endpoint(
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    out = ai_suggestions.list_suggestions(db, _startup(db, membership), user.id)
    db.commit()  # new cards (and their events) created by this read
    return success_response(out)


def _suggestion_op(
    db: Session,
    membership: Membership,
    user: User,
    suggestion_id: uuid.UUID,
    op: str,
    days: int = 7,
) -> dict[str, Any]:
    r = ai_suggestions.act(db, _startup(db, membership), user.id, suggestion_id, op, days=days)
    db.commit()
    return success_response(ai_suggestions.serialize(r))


@router.post("/suggestions/{suggestion_id}/accept")
def accept_suggestion_endpoint(
    suggestion_id: uuid.UUID,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """ "Do it": marks the card accepted; the FE then opens its `href`."""
    return _suggestion_op(db, membership, user, suggestion_id, "accept")


@router.post("/suggestions/{suggestion_id}/dismiss")
def dismiss_suggestion_endpoint(
    suggestion_id: uuid.UUID,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return _suggestion_op(db, membership, user, suggestion_id, "dismiss")


@router.post("/suggestions/{suggestion_id}/snooze")
def snooze_suggestion_endpoint(
    suggestion_id: uuid.UUID,
    payload: SnoozeRequest | None = None,
    membership: Membership = Depends(_chat_access),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    days = payload.days if payload else 7
    return _suggestion_op(db, membership, user, suggestion_id, "snooze", days)


@router.get("/agents")
def list_agents_endpoint(
    membership: Membership = Depends(_chat_access),  # noqa: B008
) -> dict[str, Any]:
    return success_response(list_agents())
