import uuid

from sqlalchemy import Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import ChatMessageRole, ChatMessageStatus


class AiConversation(UUIDMixin, TimestampMixin, Base):
    """One AI Co-Founder chat thread in a workspace."""

    __tablename__ = "ai_conversations"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Auto-derived from the first user message; nullable until one exists.
    title: Mapped[str | None] = mapped_column(Text, nullable=True)


class AiMessage(UUIDMixin, TimestampMixin, Base):
    """A single turn in a conversation. Assistant turns are filled async by the worker."""

    __tablename__ = "ai_messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ai_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[ChatMessageRole] = mapped_column(
        Enum(ChatMessageRole, native_enum=False, length=12), nullable=False
    )
    status: Mapped[ChatMessageStatus] = mapped_column(
        Enum(ChatMessageStatus, native_enum=False, length=12),
        nullable=False,
        default=ChatMessageStatus.ready,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
