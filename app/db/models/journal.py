import uuid
from datetime import date

from sqlalchemy import (
    CheckConstraint,
    Date,
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

    __table_args__ = (
        UniqueConstraint(
            "startup_id", "founder_id", "date", name="uq_journal_prompts_startup_founder_date"
        ),
    )
