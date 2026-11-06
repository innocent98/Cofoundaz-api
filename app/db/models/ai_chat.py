import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
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
    # One of ai_cofounder.topics.TOPICS; null until the first reply is routed (slice 2).
    topic: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    # Archived conversations leave the default list and are not searchable (D-AI-4).
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "Ask {agent}" (gap row 19): when set, every reply in this conversation goes to that agent.
    pinned_agent_key: Mapped[str | None] = mapped_column(String(24), nullable=True)


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
    # Set when staff remove it after a report; the row stays so the removal can be undone.
    removed_by_moderation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Orchestrator output (assistant turns only; null on user turns and on turns from before routing).
    agent_key: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # "Why I said this": built in code from the route and the context sections actually used.
    reasoning_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fixed legal/financial disclaimer appended by code, never written by the model (PRD 5.4.5).
    disclaimer: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Thumbs: "up" | "down" | null, plus an optional short note (the caller's own, private).
    feedback: Mapped[str | None] = mapped_column(String(8), nullable=True)
    feedback_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Bumped by "regenerate"; a reply job carries the value it was queued for, so a job left over
    # from before a regenerate can never answer (and bill) the turn a second time.
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class AiAgentRoute(UUIDMixin, TimestampMixin, Base):
    """The orchestrator's routing decision for one assistant turn (PRD `ai_agent_routes`).

    One row per assistant message (unique), written and committed before the agent call, so a
    worker retry reuses the decision instead of paying for the router again."""

    __tablename__ = "ai_agent_routes"

    message_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ai_messages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ai_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_key: Mapped[str] = mapped_column(String(24), nullable=False)
    # rules | llm | fallback
    method: Mapped[str] = mapped_column(String(12), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)


class AiAction(UUIDMixin, TimestampMixin, Base):
    """A change the co-founder proposed in a reply ("Add as task", "Update roadmap", "Draft the
    document"). Nothing is written to the workspace until the founder confirms (PRD 5.4.4).

    status: proposed -> executed -> undone, or proposed -> dismissed, or proposed -> failed."""

    __tablename__ = "ai_actions"

    message_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ai_messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Validated, size-capped parameters (never raw model output).
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="proposed")
    # What the execution created or changed, kept so Undo can reverse exactly that.
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AiSuggestion(UUIDMixin, TimestampMixin, Base):
    """One card in "Suggested actions" for one member. Built from what the app already generates
    (D-AI-3a), never from a new model call; `(user, source, source_ref)` is unique so a dismissed
    card does not come back."""

    __tablename__ = "ai_suggestions"
    __table_args__ = (
        UniqueConstraint("user_id", "startup_id", "source", "source_ref", name="uq_ai_suggestion"),
    )

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(String(300), nullable=True)
    href: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # new | accepted | dismissed | snoozed
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="new")
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
