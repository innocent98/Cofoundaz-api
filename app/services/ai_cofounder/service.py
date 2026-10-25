import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, NotFound
from app.db.models.ai_chat import AiConversation, AiMessage
from app.db.models.enums import ChatMessageRole, ChatMessageStatus
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher

REPLY_FAILED_TEXT = "Sorry, I couldn't put a reply together. Please try asking again."
BUDGET_FAILED_TEXT = (
    "Today's AI usage limit for your workspace has been reached. Try again tomorrow."
)

_TITLE_MAX = 80


def _title_from(text: str) -> str:
    return " ".join(text.split())[:_TITLE_MAX]


def create_conversation(
    db: Session, startup: Startup, user: User, first_message: str | None
) -> AiConversation:
    conv = AiConversation(
        startup_id=startup.id,
        created_by=user.id,
        title=_title_from(first_message) if first_message else None,
    )
    db.add(conv)
    db.flush()
    if first_message:
        post_message(db, conv, first_message)
    return conv


def list_conversations(db: Session, startup: Startup, user: User) -> list[AiConversation]:
    # Conversations are private to their creator (a founder's AI chat is personal and
    # context-rich), not shared across the whole workspace.
    return (
        db.query(AiConversation)
        .filter(
            AiConversation.startup_id == startup.id,
            AiConversation.created_by == user.id,
        )
        .order_by(AiConversation.updated_at.desc())
        .all()
    )


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


def serialize_message(m: AiMessage) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "role": m.role.value,
        "status": m.status.value,
        "content": m.content,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def serialize_conversation(
    conv: AiConversation, messages: list[AiMessage] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(conv.id),
        "title": conv.title,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
    }
    if messages is not None:
        out["messages"] = [serialize_message(m) for m in messages]
    return out
