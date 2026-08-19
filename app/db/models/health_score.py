import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import RecommendationEffort, RecommendationStatus


class HealthScore(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "health_scores"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    band: Mapped[str] = mapped_column(String(20), nullable=False)
    dimension_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="assessment")
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)


class HealthScoreHistory(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "health_score_history"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("ix_health_history_startup_created", "startup_id", "created_at"),)


class HealthSignal(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "health_signals"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dimension: Mapped[str] = mapped_column(String(20), nullable=False)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    contribution: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False)


class HealthRecommendation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "health_recommendations"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dimension: Mapped[str] = mapped_column(String(20), nullable=False)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_lift: Mapped[int] = mapped_column(Integer, nullable=False)
    effort: Mapped[RecommendationEffort] = mapped_column(
        Enum(RecommendationEffort, native_enum=False, length=10), nullable=False
    )
    status: Mapped[RecommendationStatus] = mapped_column(
        Enum(RecommendationStatus, native_enum=False, length=12),
        nullable=False,
        default=RecommendationStatus.pending,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("uq_recommendation_startup_key", "startup_id", "key", unique=True),)
