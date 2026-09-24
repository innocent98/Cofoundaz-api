import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import (
    CampaignObjective,
    CampaignStatus,
    ChannelKey,
    ChannelStatus,
    ContentStatus,
    MarketingGenerationKind,
    MarketingGenerationStatus,
)


class ContentCalendarEntry(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "content_calendar"

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
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[ChannelKey] = mapped_column(
        Enum(ChannelKey, native_enum=False, length=20), nullable=False
    )
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus, native_enum=False, length=12),
        nullable=False,
        default=ContentStatus.draft,
    )
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MarketingChannel(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "marketing_channels"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[ChannelKey] = mapped_column(
        Enum(ChannelKey, native_enum=False, length=20), nullable=False
    )
    status: Mapped[ChannelStatus] = mapped_column(
        Enum(ChannelStatus, native_enum=False, length=12),
        nullable=False,
        default=ChannelStatus.not_started,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("startup_id", "key", name="uq_marketing_channel_startup_key"),
    )


class AudienceSegment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "audience_segments"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    est_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    persona_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("business_records.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class Campaign(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    objective: Mapped[CampaignObjective] = mapped_column(
        Enum(CampaignObjective, native_enum=False, length=12), nullable=False
    )
    budget: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    channel_mix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, native_enum=False, length=12),
        nullable=False,
        default=CampaignStatus.draft,
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CampaignSegment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "campaign_segments"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    segment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audience_segments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    __table_args__ = (UniqueConstraint("campaign_id", "segment_id", name="uq_campaign_segment"),)


class MarketingAiGeneration(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "marketing_ai_generations"

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
    kind: Mapped[MarketingGenerationKind] = mapped_column(
        Enum(MarketingGenerationKind, native_enum=False, length=12), nullable=False, index=True
    )
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[MarketingGenerationStatus] = mapped_column(
        Enum(MarketingGenerationStatus, native_enum=False, length=12),
        nullable=False,
        default=MarketingGenerationStatus.generating,
    )
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
