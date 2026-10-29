import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import EnrichmentStatus


class JournalEntry(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "journal_entries"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
    )
    founder_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    content_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    mood: Mapped[int] = mapped_column(Integer, nullable=False)
    stress: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "startup_id",
            "founder_id",
            "date",
            name="uq_journal_entries_startup_founder_date",
        ),
        CheckConstraint(
            "stress BETWEEN 1 AND 10",
            name="stress_range",
        ),
    )


class MoodLog(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "mood_logs"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
    )
    founder_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    mood: Mapped[int] = mapped_column(Integer, nullable=False)
    stress: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "startup_id",
            "founder_id",
            "date",
            name="uq_mood_logs_startup_founder_date",
        ),
        CheckConstraint(
            "stress BETWEEN 1 AND 10",
            name="stress_range",
        ),
    )


class JournalPrompt(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "journal_prompts"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    founder_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    prompt: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[EnrichmentStatus] = mapped_column(
        Enum(EnrichmentStatus, native_enum=False, length=12),
        nullable=False,
        default=EnrichmentStatus.generating,
    )
    dismissed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    __table_args__ = (
        UniqueConstraint(
            "startup_id", "founder_id", "date", name="uq_journal_prompts_startup_founder_date"
        ),
    )


class JournalSettings(UUIDMixin, TimestampMixin, Base):
    """Per-founder journal preferences and the optional passcode (D-JNL-1). The passcode is a
    bcrypt hash; `passcode_version` bumps on every change so outstanding unlock tokens die."""

    __tablename__ = "journal_settings"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    founder_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    passcode_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    passcode_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    daily_prompt_notifications: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    evening_reminder: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    supportive_dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("startup_id", "founder_id", name="uq_journal_settings_startup_founder"),
    )


class JournalRetro(UUIDMixin, TimestampMixin, Base):
    """A milestone retrospective (PRD 21.5). `content_encrypted` holds the three answers as one
    Fernet token; a dismissed offer is a row with no content. The milestone title is
    snapshotted so the retro still reads after the milestone is deleted."""

    __tablename__ = "journal_retros"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    founder_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    milestone_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("roadmap_milestones.id", ondelete="SET NULL"),
        nullable=True,
    )
    milestone_title: Mapped[str] = mapped_column(String(200), nullable=False)
    content_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    dismissed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    __table_args__ = (
        UniqueConstraint("founder_id", "milestone_id", name="uq_journal_retros_founder_milestone"),
    )
