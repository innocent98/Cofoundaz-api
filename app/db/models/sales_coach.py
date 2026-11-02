import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import (
    CallPrepStatus,
    ChatMessageRole,
    ChatMessageStatus,
    CoachDifficulty,
    CoachSessionStatus,
)


class SalesCoachSession(UUIDMixin, TimestampMixin, Base):
    """One role-play practice call. Private to the member who started it."""

    __tablename__ = "sales_coach_sessions"

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
    mode: Mapped[str] = mapped_column(String(12), nullable=False, default="roleplay")
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("deals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scenario: Mapped[str | None] = mapped_column(Text, nullable=True)
    persona: Mapped[str] = mapped_column(String(200), nullable=False)
    difficulty: Mapped[CoachDifficulty] = mapped_column(
        Enum(CoachDifficulty, native_enum=False, length=10), nullable=False
    )
    # The deal/account/contact/activities as they were when the session started, so the buyer
    # stays consistent for the whole call even if the deal is edited or deleted meanwhile.
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[CoachSessionStatus] = mapped_column(
        Enum(CoachSessionStatus, native_enum=False, length=10),
        nullable=False,
        default=CoachSessionStatus.active,
    )
    scorecard_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    scorecard_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_sales_coach_sessions_owner", "startup_id", "created_by"),)


class SalesCoachMessage(UUIDMixin, TimestampMixin, Base):
    """A turn of the call. The seller is `user`; the AI buyer's turn starts `generating`."""

    __tablename__ = "sales_coach_messages"

    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sales_coach_sessions.id", ondelete="CASCADE"),
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


class SalesCallPrep(UUIDMixin, TimestampMixin, Base):
    """An AI call-prep sheet for a deal. Regenerating adds a row; GET returns the latest."""

    __tablename__ = "sales_call_preps"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    deal_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[CallPrepStatus] = mapped_column(
        Enum(CallPrepStatus, native_enum=False, length=12),
        nullable=False,
        default=CallPrepStatus.generating,
    )
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
