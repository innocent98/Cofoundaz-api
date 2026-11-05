import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class ReadinessScore(UUIDMixin, TimestampMixin, Base):
    """The latest Investor Readiness score for a workspace (one row; PRD 15 `readiness_scores`).
    `updated_at` is the compute time: the 15 minute throttle reads it."""

    __tablename__ = "readiness_scores"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_score: Mapped[int] = mapped_column(Integer, nullable=False)
    band: Mapped[str] = mapped_column(String(20), nullable=False)
    capped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dimensions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    must_haves: Mapped[list] = mapped_column(JSONB, nullable=False)
    gaps: Mapped[list] = mapped_column(JSONB, nullable=False)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Last "your score improved" notification (at most one a day, on a band change or +5).
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_score: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ReadinessScoreHistory(UUIDMixin, TimestampMixin, Base):
    """One point per change of score, dimension scores or cap state."""

    __tablename__ = "readiness_score_history"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    band: Mapped[str] = mapped_column(String(20), nullable=False)
    capped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dimension_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("ix_readiness_history_startup_created", "startup_id", "created_at"),)


class ReadinessInput(UUIDMixin, TimestampMixin, Base):
    """Founder-entered market size (a claim, so it only scores with a source URL)."""

    __tablename__ = "readiness_inputs"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    tam_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sam_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    som_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
