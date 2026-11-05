import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, cast, exists, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, NotFound
from app.db.models.ai_chat import AiConversation, AiMessage
from app.db.models.enums import ChatMessageRole, ChatMessageStatus
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.reports.core import REMOVED_PLACEHOLDER

REPLY_FAILED_TEXT = "Sorry, I couldn't put a reply together. Please try asking again."
BUDGET_FAILED_TEXT = (
    "Today's AI usage limit for your workspace has been reached. Try again tomorrow."
)

_TITLE_MAX = 80
_LIST_LIMIT = 200
_SNIPPET_RADIUS = 60
MIN_QUERY, MAX_QUERY = 2, 100


def _title_from(text: str) -> str:
    return " ".join(text.split())[:_TITLE_MAX]


def create_conversation(
    db: Session, startup: Startup, user: User, first_message: str
) -> AiConversation:
    conv = AiConversation(
        startup_id=startup.id,
        created_by=user.id,
        title=_title_from(first_message),
    )
    db.add(conv)
    db.flush()
    post_message(db, conv, first_message)
    return conv


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _snippet(text: str, term: str) -> str:
    """A short window of `text` around the first case-insensitive hit of `term`."""
    flat = " ".join(text.split())
    at = flat.lower().find(term.lower())
    if at < 0:
        return flat[: 2 * _SNIPPET_RADIUS]
    lo, hi = max(0, at - _SNIPPET_RADIUS), min(len(flat), at + len(term) + _SNIPPET_RADIUS)
    return ("…" if lo else "") + flat[lo:hi] + ("…" if hi < len(flat) else "")


def list_conversations(
    db: Session,
    startup: Startup,
    user: User,
    *,
    query: str | None = None,
    topic: str | None = None,
    archived: bool = False,
) -> list[tuple[AiConversation, str | None]]:
    """The caller's own conversations, newest activity first, each with a match snippet when
    `query` is given. Conversations are private to their creator (a founder's AI chat is
    personal and context-rich), not shared across the whole workspace. Archived ones are only
    listed with `archived=True` and are never searched (D-AI-4)."""
    q = db.query(AiConversation).filter(
        AiConversation.startup_id == startup.id,
        AiConversation.created_by == user.id,
        (
            AiConversation.archived_at.is_not(None)
            if archived
            else AiConversation.archived_at.is_(None)
        ),
    )
    if topic is not None:
        q = q.filter(AiConversation.topic == topic)
    term = (query or "").strip()
    if term:
        pattern = _like(term)
        readable = (
            AiMessage.conversation_id == AiConversation.id,
            AiMessage.status == ChatMessageStatus.ready,
            AiMessage.removed_by_moderation_at.is_(None),
            cast(AiMessage.content, String).ilike(pattern, escape="\\"),
        )
        q = q.filter(
            or_(
                cast(AiConversation.title, String).ilike(pattern, escape="\\"),
                exists().where(*readable),
            )
        )
    convs = q.order_by(AiConversation.updated_at.desc()).limit(_LIST_LIMIT).all()
    if not term:
        return [(c, None) for c in convs]
    out: list[tuple[AiConversation, str | None]] = []
    for c in convs:
        hit = (
            db.query(AiMessage.content)
            .filter(
                AiMessage.conversation_id == c.id,
                AiMessage.status == ChatMessageStatus.ready,
                AiMessage.removed_by_moderation_at.is_(None),
                cast(AiMessage.content, String).ilike(pattern, escape="\\"),
            )
            .order_by(AiMessage.created_at.asc(), AiMessage.id.asc())
            .first()
        )
        out.append((c, _snippet(hit[0], term) if hit else _snippet(c.title or "", term)))
    return out


def get_conversation(
    db: Session, startup: Startup, user: User, conversation_id: uuid.UUID
) -> AiConversation:
    conv = (
        db.query(AiConversation)
        .filter(
            AiConversation.id == conversation_id,
            AiConversation.startup_id == startup.id,
            AiConversation.created_by == user.id,
        )
        .first()
    )
    if conv is None:
        raise NotFound()
    return conv


def messages_for(db: Session, conversation: AiConversation) -> list[AiMessage]:
    return (
        db.query(AiMessage)
        .filter(AiMessage.conversation_id == conversation.id)
        .order_by(AiMessage.created_at.asc(), AiMessage.id.asc())
        .all()
    )


def fail_message(db: Session, msg: AiMessage, text: str) -> None:
    """End an assistant turn as `failed` with a founder-visible message (the FE polls it)."""
    msg.status = ChatMessageStatus.failed
    msg.content = text
    db.flush()
    event_bus.publish(
        db,
        "ai.cofounder.reply.failed",
        {"message_id": str(msg.id), "conversation_id": str(msg.conversation_id)},
    )


def fail_stale_generating(db: Session, conversation: AiConversation) -> int:
    """Fail assistant turns stuck `generating` past AI_REPLY_STALE_SECONDS (worker down,
    job lost). Returns how many were failed; the caller commits."""
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.AI_REPLY_STALE_SECONDS)
    stale = (
        db.query(AiMessage)
        .filter(
            AiMessage.conversation_id == conversation.id,
            AiMessage.role == ChatMessageRole.assistant,
            AiMessage.status == ChatMessageStatus.generating,
            AiMessage.created_at < cutoff,
        )
        .all()
    )
    for m in stale:
        fail_message(db, m, REPLY_FAILED_TEXT)
    return len(stale)


def post_message(db: Session, conversation: AiConversation, content: str) -> AiMessage:
    """Store the founder's turn + an empty `generating` assistant turn, and enqueue the
    `ai.cofounder.reply` job that fills it. Returns the assistant turn to poll for."""
    if conversation.archived_at is not None:
        raise AppError(
            "CONVERSATION_ARCHIVED", "This conversation is archived. Unarchive it to continue.", 409
        )
    fail_stale_generating(db, conversation)
    # Guard against spam / LLM-cost amplification: refuse a new turn while one is still
    # being generated in this conversation (the FE should be polling the existing one).
    in_flight = (
        db.query(AiMessage)
        .filter(
            AiMessage.conversation_id == conversation.id,
            AiMessage.role == ChatMessageRole.assistant,
            AiMessage.status == ChatMessageStatus.generating,
        )
        .first()
    )
    if in_flight is not None:
        raise AppError(
            "REPLY_IN_PROGRESS",
            "A reply is still being generated in this conversation. Please wait.",
            409,
        )
    # Both rows are inserted in one transaction, so Postgres `now()` would give them an
    # identical created_at and the user/assistant order would be nondeterministic. Set it
    # explicitly with a 1ms gap so the turn order is stable.
    now = datetime.now(UTC)
    db.add(
        AiMessage(
            conversation_id=conversation.id,
            role=ChatMessageRole.user,
            status=ChatMessageStatus.ready,
            content=content,
            created_at=now,
        )
    )
    assistant = AiMessage(
        conversation_id=conversation.id,
        role=ChatMessageRole.assistant,
        status=ChatMessageStatus.generating,
        content="",
        created_at=now + timedelta(milliseconds=1),
    )
    db.add(assistant)
    if conversation.title is None:
        conversation.title = _title_from(content)
    # Bump so conversation lists order by most-recent activity.
    conversation.updated_at = now + timedelta(milliseconds=1)
    db.flush()
    job_dispatcher.enqueue(
        db,
        "ai.cofounder.reply",
        {"message_id": str(assistant.id), "startup_id": str(conversation.startup_id)},
        conversation.startup_id,
    )
    return assistant


def _owned(db: Session, startup: Startup, user: User, ids: list[uuid.UUID]) -> list[AiConversation]:
    wanted = set(ids)
    convs = (
        db.query(AiConversation)
        .filter(
            AiConversation.id.in_(wanted),
            AiConversation.startup_id == startup.id,
            AiConversation.created_by == user.id,
        )
        .all()
    )
    # All or nothing: an id that is not yours looks exactly like one that does not exist.
    if len(convs) != len(wanted):
        raise NotFound()
    return convs


def set_archived(
    db: Session, startup: Startup, user: User, ids: list[uuid.UUID], *, archived: bool
) -> int:
    """Archive (or unarchive) the caller's conversations. Idempotent; returns how many changed."""
    changed = 0
    now = datetime.now(UTC)
    for conv in _owned(db, startup, user, ids):
        if (conv.archived_at is not None) != archived:
            conv.archived_at = now if archived else None
            changed += 1
    db.flush()
    return changed


def delete_conversation(db: Session, conversation: AiConversation) -> None:
    """ "Delete forever": the conversation and all its messages go (FK cascade).

    Refused (409) while a reply is still being generated: deleting under a running model call
    would let the worker's result land on a deleted row. (The worker also re-checks, see
    `handle_cofounder_reply`.) Reported-message snippets kept by Admin moderation are separate
    copies and are not removed (D-ADM-9)."""
    fail_stale_generating(db, conversation)
    in_flight = (
        db.query(AiMessage.id)
        .filter(
            AiMessage.conversation_id == conversation.id,
            AiMessage.status == ChatMessageStatus.generating,
        )
        .first()
    )
    if in_flight is not None:
        raise AppError(
            "REPLY_IN_PROGRESS",
            "A reply is still being generated. Wait for it to finish, then delete.",
            409,
        )
    db.delete(conversation)
    db.flush()


def serialize_message(m: AiMessage) -> dict[str, Any]:
    removed = m.removed_by_moderation_at is not None
    return {
        "id": str(m.id),
        "role": m.role.value,
        "status": m.status.value,
        "content": REMOVED_PLACEHOLDER if removed else m.content,
        "removed_by_moderation": removed,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def serialize_conversation(
    conv: AiConversation, messages: list[AiMessage] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(conv.id),
        "title": conv.title,
        "topic": conv.topic,
        "archived_at": conv.archived_at.isoformat() if conv.archived_at else None,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
    }
    if messages is not None:
        out["messages"] = [serialize_message(m) for m in messages]
    return out
